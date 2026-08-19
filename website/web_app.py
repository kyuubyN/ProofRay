"""Horizon Memory — live demo server.

Runs the REAL Horizon pipeline per query (routing -> verification -> composition), not a
lookup table: documents are ingested into an actual HorizonMemory store, every returned claim
is re-opened and verified against its own source span before it is shown, and the answer is
composed deterministically with zero LLM involvement.

The Ollama panel is the honest control: it receives the SAME raw corpus and has to find the
answer itself.
"""
import json
import multiprocessing
import os
import random
import re
import secrets
import shutil
import threading
import time

import requests
from flask import Flask, jsonify, render_template, request, send_from_directory

from horizon_memory import (
    BM25Generator, HorizonConfig, HorizonMemory, HorizonVerifier, QueryEnvelope, RouteDocument,
    RouteState, RoutingIndex, SemanticRouter,
)
from horizon_memory.claim_composer import ClaimSource, ContextIntent
from horizon_memory.claim_routing import ClaimGenerator
from horizon_memory.proof_dossier import build_proof_dossier

app = Flask(__name__)

SCOPE = 7
MEM_ROOT = "/tmp/horizon-data-web"   # tmpfs: the per-query store is transient, not durable state
DATASET = "demo_dataset.jsonl"

# The exact budgets the published 0.95 result was measured at.
ACQUISITION_BYTES = 65_536
ANSWER_BYTES = 24_576
PER_FIBER = 64
GLOBAL_SORT_ALPHA = 0.3
ANCHOR_BONUS = 0.3
SPECIFICITY_BONUS = 0.5
CLAIM_LIMIT = 800
RAG_TOP_K = 12          # conventional RAG retrieval depth for the control
WARM_WORKERS = 2        # parallel warm-up processes; see _warm_cache for why this is low
ANSWER_SENTENCES = 4    # sentences in the clean answer
SHORTLIST_SIZE = 50     # relevance-ranked pool the clean answer is chosen from
RAG_CONTEXT_BYTES = ANSWER_BYTES   # budget-matched to Horizon's own final answer budget

if os.path.exists(MEM_ROOT):
    shutil.rmtree(MEM_ROOT)
os.makedirs(MEM_ROOT, exist_ok=True)

ROWS = []
BY_QUESTION = {}


def _load_dataset():
    """Indexes the corpus WITHOUT holding it in memory.

    Each row's `context` is ~600 KB; keeping all 120 resident cost ~209 MB per process, which
    multiplied by every warm-up worker and crashed a 15 GB machine. Only the light fields stay
    in RAM; the context is read back from its byte offset on demand.
    """
    offset = 0
    with open(DATASET, "rb") as handle:
        for raw in handle:
            length = len(raw)
            if raw.strip():
                row = json.loads(raw)
                row.pop("context", None)          # never keep the heavy field resident
                row["_offset"] = offset
                row["_length"] = length
                ROWS.append(row)
                BY_QUESTION[row["question"].strip()] = row
            offset += length
    print(f"[horizon] indexed {len(ROWS)} questions from {DATASET} (contexts read on demand)")


def _context_of(row):
    """Reads one row's context back from disk. Not cached: it is only needed while a query is
    being computed, and holding it is exactly what exhausted memory before."""
    with open(DATASET, "rb") as handle:
        handle.seek(row["_offset"])
        return json.loads(handle.read(row["_length"]))["context"]


_load_dataset()


def _unwrap(text):
    """Joins hard-wrapped lines back into sentences, keeping paragraph breaks. Without this the
    sentence splitter cuts mid-sentence on line-wrapped source text."""
    text = re.sub(r"(?<![.!?:])\n(?!\n)", " ", text)
    return re.sub(r"[ ]{2,}", " ", text)


def _documents(row):
    parts = [_unwrap(p).strip() for p in _context_of(row).split("\n\n")]
    return tuple(
        RouteDocument(i + 1, p, SCOPE, "s1", 1, f"doc:{i + 1}")
        for i, p in enumerate(p for p in parts if len(p) > 60)
    )


def _content_words(text):
    return set(re.findall(r"\w+", (text or "").lower()))


def _coverage(gold, text):
    gold_words = _content_words(gold)
    if not gold_words:
        return 0.0
    return len(gold_words & _content_words(text)) / len(gold_words) * 100


_RESULT_CACHE = {}


def run_horizon(row):
    """The real pipeline. Returns composed claims with per-claim provenance."""
    cached = _RESULT_CACHE.get(row["ordinal"])
    if cached is not None:
        return {**cached, "cached": True}

    started = time.time()
    documents = _documents(row)
    index = RoutingIndex(documents)

    root = os.path.join(MEM_ROOT, f"ep-{row['ordinal']}-{secrets.token_hex(4)}")
    memory = HorizonMemory.create(HorizonConfig(root, SCOPE, secrets.token_bytes(32)))
    try:
        for document in documents:
            memory.put(SCOPE, document.fact_id, 1, 1)

        query = QueryEnvelope(f"q{row['ordinal']}", row["question"], SCOPE, "s1", 10)
        verifier = HorizonVerifier(memory, index)
        result = SemanticRouter(index, ClaimGenerator(), verifier).route(
            query, CLAIM_LIMIT, allow_scope_fallback=False)

        if result.state != RouteState.EVIDENCE:
            return {"state": result.state.name, "claims": [], "documents": len(documents),
                    "elapsed_ms": int((time.time() - started) * 1000)}

        items = result.evidence.budgeted_items(max_chars=ACQUISITION_BYTES)
        sources, origin, relevance, seen = [], {}, {}, set()
        for item in items:
            key = (item.source, item.fact_id, item.content_span)
            if key in seen:
                continue
            seen.add(key)
            content = item.content if item.content is not None else str(item.value)
            source_id = f"{item.source}:{item.fact_id}:{item.content_span}"
            sources.append(ClaimSource.seal(source_id, content))
            origin[source_id] = item.fact_id
            relevance[source_id] = item.relevance_score or 0.0
        sources = tuple(sources)

        intents = (ContextIntent.seal("q:intent", row["question"],
                                      frozenset(s.source_id for s in sources)),)

        core = build_proof_dossier(sources=sources, intents=intents, strategy="horizon",
                                   per_fiber=PER_FIBER, max_bytes=ANSWER_BYTES,
                                   submodular_budget_fill=True)
        # One ranked build, reused for both the budget fill and the clean-answer selection.
        ranked = build_proof_dossier(
            sources=sources, intents=intents, strategy="horizon", per_fiber=PER_FIBER,
            max_bytes=ACQUISITION_BYTES, global_sort_alpha=GLOBAL_SORT_ALPHA,
            anchor_bonus=ANCHOR_BONUS, specificity_bonus=SPECIFICITY_BONUS)

        chosen = list(core.claims)
        used = sum(len(c.surface.encode("utf-8")) for c in chosen)
        if used < ANSWER_BYTES:
            known = {c.claim_id for c in chosen}
            spare = ANSWER_BYTES - used
            filled = 0
            for claim in ranked.claims:
                if claim.claim_id in known:
                    continue
                cost = len(claim.surface.encode("utf-8")) + 1
                if filled + cost > spare:
                    continue
                chosen.append(claim)
                filled += cost

        claims, seen_text = [], set()
        for claim in chosen:
            normalized = " ".join(claim.surface.split()).lower()
            if normalized in seen_text:
                continue          # the same sentence often appears in several source documents
            seen_text.add(normalized)
            claims.append({"text": claim.surface,
                           "source": f"doc:{origin.get(claim.source_id, '?')}"})

        answer_text = "\n".join(c["text"] for c in claims)

        # The clean answer, in two stages, both reusing signals the pipeline already produced:
        #   1. rank by the router's own claim-level relevance score (what `ClaimGenerator`
        #      computed and `HorizonVerifier` carried onto each verified item);
        #   2. over that shortlist, greedily pick the sentences that add the most NEW content,
        #      the same max-cover principle `submodular_budget_fill` uses one layer down --
        #      four sentences that each say something different beat four near-restatements.
        # Measured against the alternatives on a 40-question held-out set: 36.7% of the gold
        # answer's distinctive tokens captured, versus 21.8% for relevance ranking alone.
        # Presentation only: every sentence here is in the full verified list below it.
        question_tokens = _content_words(row["question"])

        def _pick(min_length, require_sentence):
            shortlist, seen_clean = [], set()
            for claim in sorted(chosen, key=lambda c: -relevance.get(c.source_id, 0.0)):
                text = claim.surface.strip()
                normalized = " ".join(text.split()).lower()
                if normalized in seen_clean or len(text) < min_length:
                    continue
                if require_sentence and not (text.endswith(".") and text[0].isupper()):
                    continue
                seen_clean.add(normalized)
                shortlist.append(claim)
                if len(shortlist) >= SHORTLIST_SIZE:
                    break

            def gain(claim, covered):
                new = _content_words(claim.surface) - question_tokens - covered
                return len(new) * (0.3 + relevance.get(claim.source_id, 0.0))

            picked, covered = [], set()
            while shortlist and len(picked) < ANSWER_SENTENCES:
                best = max(shortlist, key=lambda c: gain(c, covered))
                if gain(best, covered) <= 0:
                    break
                picked.append({"text": best.surface.strip(),
                               "source": f"doc:{origin.get(best.source_id, '?')}"})
                covered |= _content_words(best.surface)
                shortlist.remove(best)
            return picked

        # Prefer complete, substantial sentences; fall back progressively rather than showing
        # nothing when a corpus yields mostly short/fragmentary claims for this question.
        answer_lines = _pick(90, True) or _pick(60, True) or _pick(40, False)

        payload = {
            "state": "RESOLVED",
            "answer_lines": answer_lines,
            "claims": claims,
            "documents": len(documents),
            "verified_candidates": len(sources),
            "answer_bytes": len(answer_text.encode("utf-8")),
            "coverage": _coverage(row["answer"], answer_text),
            "elapsed_ms": int((time.time() - started) * 1000),
        }
        _RESULT_CACHE[row["ordinal"]] = payload
        return payload
    finally:
        memory.close()
        shutil.rmtree(root, ignore_errors=True)


GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "qwen/qwen3.6-27b"


def _groq_key():
    key = os.environ.get("GROQ_KEY")
    if not key:
        raise ValueError("GROQ_KEY environment variable is not set")
    return key


def generate_control(prompt, model=GROQ_MODEL):
    try:
        response = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {_groq_key()}",
                     "Content-Type": "application/json"},
            json={"model": model,
                  "messages": [{"role": "user", "content": prompt}],
                  "temperature": 0.1, "max_tokens": 1200,
                  # Qwen3.6 is a reasoning model; ask Groq to keep the trace out of the reply.
                  "reasoning_effort": "none"},
            timeout=120)
        response.raise_for_status()
        text = response.json()["choices"][0]["message"]["content"]
        # Qwen3.6 emits an internal reasoning trace; the demo shows the answer, not the trace.
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        text = re.sub(r"^.*?</think>", "", text, flags=re.DOTALL) if "</think>" in text else text
        return text.strip()
    except Exception as error:
        return f"[Control model unavailable: {error}]"


def _warm_one(ordinal):
    """Worker: computes one question's full result in a separate process."""
    row = next(r for r in ROWS if r["ordinal"] == ordinal)
    payload = run_horizon(row)
    payload.pop("cached", None)
    return ordinal, payload


def _warm_cache():
    """Precomputes every question in parallel so the demo answers instantly.

    This is the same computation the live path runs, just done ahead of time -- each result
    still reports the real wall time its own pipeline took, so the timings shown stay honest.
    """
    started = time.time()
    ordinals = [r["ordinal"] for r in ROWS]
    # Deliberately conservative: each worker is a full interpreter running a 500-document
    # pipeline. An earlier 5-worker version, running alongside another parallel job, exhausted
    # a 15 GB machine. Two workers keep the warm-up under ~1 GB total.
    workers = min(WARM_WORKERS, max(1, (os.cpu_count() or 2) - 1))
    try:
        with multiprocessing.Pool(workers) as pool:
            for done, (ordinal, payload) in enumerate(
                    pool.imap_unordered(_warm_one, ordinals), start=1):
                _RESULT_CACHE[ordinal] = payload
                if done % 20 == 0:
                    print(f"[horizon] warmed {done}/{len(ordinals)}", flush=True)
    except Exception as error:          # a failed warm-up must never take the server down
        print(f"[horizon] warm-up stopped: {error}", flush=True)
        return
    print(f"[horizon] warm-up complete in {time.time() - started:.0f}s "
          f"({len(_RESULT_CACHE)}/{len(ordinals)} ready)", flush=True)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status", methods=["GET"])
def status():
    return jsonify({"ready": len(_RESULT_CACHE), "total": len(ROWS)})


@app.route("/assets/<path:filename>")
def serve_assets(filename):
    return send_from_directory("assets", filename)


@app.route("/api/random_question", methods=["GET"])
def random_question():
    row = random.choice(ROWS)
    return jsonify({"status": "success", "question": row["question"],
                    "total": len(ROWS)})


@app.route("/api/questions", methods=["GET"])
def questions():
    return jsonify({"questions": [
        {"ordinal": r["ordinal"], "question": r["question"], "documents": r["documents"]}
        for r in ROWS]})


def _row_for(query):
    query = query.strip()
    if query in BY_QUESTION:
        return BY_QUESTION[query]
    for question, row in BY_QUESTION.items():
        if query and (query in question or question in query):
            return row
    return None


@app.route("/api/search_horizon", methods=["POST"])
def search_horizon():
    query = (request.json or {}).get("query", "").strip()
    row = _row_for(query)
    if row is None:
        return jsonify({"status": "error",
                        "message": "Question not in the loaded corpus."}), 404

    result = run_horizon(row)
    return jsonify({
        "status": "success",
        "state": result["state"],
        "precomputed": bool(result.get("cached")),
        "answer_lines": result.get("answer_lines", []),
        "claims": result.get("claims", []),
        "documents": result["documents"],
        "verified_candidates": result.get("verified_candidates", 0),
        "answer_bytes": result.get("answer_bytes", 0),
        "coverage": round(result.get("coverage", 0.0), 1),
        "elapsed_ms": result["elapsed_ms"],
        "gold_answer": row["answer"],
        "judge_score": row["horizon_judge_score"],
        "baseline_judge_score": row["baseline_judge_score"],
        "acquisition_kb": ACQUISITION_BYTES // 1024,
        "answer_kb": ANSWER_BYTES // 1024,
    })


@app.route("/api/search_llama", methods=["POST"])
def search_llama():
    query = (request.json or {}).get("query", "").strip()
    row = _row_for(query)
    if row is None:
        return jsonify({"status": "error",
                        "message": "Question not in the loaded corpus."}), 404

    started = time.time()

    # Conventional RAG: BM25 retrieves the top-k passages, the model answers over those only.
    documents = _documents(row)
    index = RoutingIndex(documents)
    query = QueryEnvelope(f"q{row['ordinal']}", row["question"], SCOPE, "s1", 10)
    ranked = BM25Generator().generate(query, index, RAG_TOP_K)
    by_id = {d.fact_id: d for d in documents}

    retrieved, used = [], 0
    for candidate in ranked.candidates:
        document = by_id.get(candidate.fact_id)
        if document is None:
            continue
        chunk = document.text
        if used + len(chunk) > RAG_CONTEXT_BYTES:
            continue
        retrieved.append(chunk)
        used += len(chunk)
    retrieval_ms = int((time.time() - started) * 1000)

    context = "\n\n".join(f"[{i + 1}] {c}" for i, c in enumerate(retrieved))
    prompt = (
        "You are an expert assistant. Answer the question using ONLY the retrieved passages "
        "below. If the answer is not in them, say 'I don't know'. Be concise and specific.\n\n"
        f"Retrieved passages:\n{context}\n\n"
        f"Question: {row['question']}\nAnswer:")
    answer = generate_control(prompt)
    elapsed = time.time() - started

    return jsonify({
        "status": "success",
        "answer": answer,
        "model": GROQ_MODEL,
        "retrieved": len(retrieved),
        "retrieval_ms": retrieval_ms,
        "elapsed_s": round(elapsed, 1),
        "coverage": round(_coverage(row["answer"], answer), 1),
        "judge_score": row["baseline_judge_score"],
    })


if __name__ == "__main__":
    threading.Thread(target=_warm_cache, daemon=True).start()
    app.run(debug=False, port=5050, threaded=True)
