"""ProofRay — live demo server.

Runs the real ProofRay pipeline per query (routing -> verification -> composition), not a
lookup table: documents are ingested into an actual ProofRay-compatible store, every returned claim
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
import threading
import time

import requests
from flask import Flask, jsonify, render_template, request, send_from_directory

from horizon_memory import (
    BM25Generator, DEFAULT_PROFILE, HorizonAnswerEngine, QueryEnvelope, RouteDocument,
    RoutingIndex,
)

app = Flask(__name__)

SCOPE = 7
DATASET = "demo_dataset.jsonl"

# Where the "Database Connector" nav button links -- App/cmd/horizon-web's Go web UI, a separate
# process/port. The two front ends stay independent (different language, different runtime);
# this is just a cross-link so a visitor can jump between them.
CONNECTOR_URL = os.environ.get("HORIZON_CONNECTOR_URL", "http://127.0.0.1:8080")

# The exact budgets the published 0.95 result was measured at -- read off DEFAULT_PROFILE
# (the same profile ENGINE runs with below) rather than duplicated as separate constants, so
# these two can never quietly drift apart the way this file's own answer-selection logic once
# did from answer_engine.py's (see run_horizon's docstring).
ACQUISITION_BYTES = DEFAULT_PROFILE.acquisition_bytes
ANSWER_BYTES = DEFAULT_PROFILE.answer_bytes
RAG_TOP_K = 12          # conventional RAG retrieval depth for the control
WARM_WORKERS = 2        # parallel warm-up processes; see _warm_cache for why this is low
RAG_CONTEXT_BYTES = ANSWER_BYTES   # budget-matched to Horizon's own final answer budget

ENGINE = HorizonAnswerEngine(profile=DEFAULT_PROFILE, scope_id=SCOPE, session_id="s1")

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
    print(f"[proofray] indexed {len(ROWS)} questions from {DATASET} (contexts read on demand)")


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
    """The real pipeline. Returns composed claims with per-claim provenance.

    Runs through the shared `HorizonAnswerEngine` (`answer_engine.py`) instead of hand-wiring
    routing/verification/composition here -- this file's own inline copy of that pipeline (and
    its own, separately-drifted clean-answer selector) was the exact bug `answer_engine.py`'s
    own docstring describes fixing: a greedy `gain = new_words * (0.3 + relevance)` formula with
    no relevance floor could let a long, low-relevance sentence outscore and exclude the single
    most relevant claim (MemGym-DR ordinal 382, BARM/UCEF). Routing through one shared
    implementation means this demo can no longer silently fall behind that fix, or any future
    one (2026-08-19, found via code review)."""
    cached = _RESULT_CACHE.get(row["ordinal"])
    if cached is not None:
        return {**cached, "cached": True}

    started = time.time()
    documents = _documents(row)
    result = ENGINE.answer(row["question"], documents)

    if result.state != "RESOLVED":
        return {"state": result.state, "claims": [], "answer_lines": [],
                "documents": result.documents_considered,
                "elapsed_ms": int((time.time() - started) * 1000)}

    claims = [{"text": c.text, "source": f"doc:{c.fact_id}"} for c in result.claims]
    answer_lines = [{"text": c.text, "source": f"doc:{c.fact_id}"} for c in result.answer_lines]
    answer_text = "\n".join(c["text"] for c in claims)

    payload = {
        "state": "RESOLVED",
        "answer_lines": answer_lines,
        "claims": claims,
        "documents": result.documents_considered,
        "verified_candidates": result.verified_candidates,
        "answer_bytes": len(answer_text.encode("utf-8")),
        "coverage": _coverage(row["answer"], answer_text),
        "elapsed_ms": int((time.time() - started) * 1000),
    }
    _RESULT_CACHE[row["ordinal"]] = payload
    return payload


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
    """Worker: computes one question's full result in a separate process.

    Catches its own exception rather than letting it propagate into the pool's
    `imap_unordered` iterator -- an exception raised there aborts the `with Pool(...)` block for
    every ordinal not yet reached, not just this one (2026-08-19, found via code review: one bad
    question used to silently cancel warm-up for the rest of the corpus)."""
    row = next(r for r in ROWS if r["ordinal"] == ordinal)
    try:
        payload = run_horizon(row)
    except Exception as error:
        return ordinal, None, str(error)
    payload.pop("cached", None)
    return ordinal, payload, None


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
    failed = 0
    try:
        with multiprocessing.Pool(workers) as pool:
            for done, (ordinal, payload, error) in enumerate(
                    pool.imap_unordered(_warm_one, ordinals), start=1):
                if error is not None:
                    failed += 1
                    print(f"[proofray] warm-up failed for ordinal {ordinal}: {error}", flush=True)
                else:
                    _RESULT_CACHE[ordinal] = payload
                if done % 20 == 0:
                    print(f"[proofray] warmed {done}/{len(ordinals)}", flush=True)
    except Exception as error:          # a failed warm-up must never take the server down
        print(f"[proofray] warm-up stopped: {error}", flush=True)
        return
    print(f"[proofray] warm-up complete in {time.time() - started:.0f}s "
          f"({len(_RESULT_CACHE)}/{len(ordinals)} ready, {failed} failed)", flush=True)


@app.route("/")
def index():
    return render_template("index.html", connector_url=CONNECTOR_URL)


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
    """Exact match first (case-insensitive), substring only as a last-resort typing-convenience
    fallback -- a first-dict-order substring match silently binds a short/generic query to
    whichever question happens to be inserted first, returning that row's claims/gold answer/
    judge scores as a "success" with no sign the match was inexact (2026-08-19, found via code
    review)."""
    query = query.strip()
    if not query:
        return None
    if query in BY_QUESTION:
        return BY_QUESTION[query]
    query_lower = query.lower()
    for question, row in BY_QUESTION.items():
        if question.lower() == query_lower:
            return row
    for question, row in BY_QUESTION.items():
        if query in question or question in query:
            return row
    return None


def _query_from_body() -> str:
    """A valid JSON body that isn't an object (e.g. a bare array or string) makes `.get()` raise
    `AttributeError` -- `request.json` only guards against invalid/missing JSON, not against
    valid JSON of the wrong shape (2026-08-19, found via code review, reproduced: POSTing `[1,2,3]`
    crashed this route with an unhandled 500)."""
    body = request.json
    if not isinstance(body, dict):
        return ""
    return (body.get("query") or "").strip()


@app.route("/api/search_proofray", methods=["POST"])
@app.route("/api/search_horizon", methods=["POST"])
def search_horizon():
    query = _query_from_body()
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
    query = _query_from_body()
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
