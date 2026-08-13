#!/usr/bin/env python3
"""V84-D26: induce event predicates as gauge invariants, then execute the algebra over typed events.

See `lab/D26_INDUCED_PREDICATE_CHARTER.md`.

D24 and D25 both used numeric literals as operands and both landed near 0.08 precision. Event type is
worth ~0.92 of precision and was the only variable that ever moved it. Here the predicate is induced
rather than hand-written:

    gauge transform   numbers -> '#', entity mentions -> '@'
    skeleton          n-grams carrying at least one operand slot
    recurrence        predicate status requires MIN_PASSAGES distinct passages
    content           at least one token that is neither stopword nor slot
    instantiate       a sentence matching a skeleton yields a typed EventRecord; no match -> no event

`count_distinct` then counts EVENTS of one predicate, not digits. That is the whole point.

Control arm `frequency_only` drops the gauge substitution of entities and the content requirement, to
decide whether gauge invariance contributes anything beyond counting frequent n-grams.

No gold value, answer vocabulary, model, endpoint or API takes part in induction or selection. The
official development split is never opened.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import run_v84_clonal_selection_d24 as D24  # noqa: E402
from run_v84_operator_expressibility_d23 import classify as operator_charge  # noqa: E402

SOURCE_MANIFEST = ROOT / "lab/datasets/manifests/v84-drop-source-v1.json"
OUTPUT = ROOT / "lab/results/qhdre-v84-induced-predicate-d26-dev-v1.json"
IMPLEMENTATION = Path(__file__)

# --- frozen induction parameters, declared before measurement --------------------------------------
MIN_PASSAGES = 50      # a skeleton earns predicate status only by recurring across this many passages
NGRAM_SIZES = (3, 4)
MAX_EVENTS = 60        # per passage; beyond this the paragraph abstains

_NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\w])")
_CAP = re.compile(r"\b[A-Z][a-zA-Z'’-]+(?:\s+[A-Z][a-zA-Z'’-]+)*")
_SENTENCE_START = re.compile(
    r"^(The|A|An|In|On|At|With|After|Before|During|However|But|And|He|They|This|That|It|His|Their)\b")

# Function words. Not a domain list: a skeleton made only of these plus slots carries no event.
_STOP = frozenset("""a an the of to in on at for with by from as is are was were be been being and or
but if then than that this these those it its his her their they he she we you i not no nor so such
who whom which what when where how had has have will would could should may might can do does did
after before during while over under between into out up down off again more most other some any""".split())


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def gauge(sentence: str, *, substitute_entities: bool) -> str:
    text = _NUM.sub("#", sentence)
    if substitute_entities:
        text = _CAP.sub(lambda m: m.group() if _SENTENCE_START.match(m.group()) else "@", text)
    return " ".join(text.lower().split())


def skeletons(sentence: str, *, substitute_entities: bool, require_content: bool):
    tokens = re.findall(r"[a-z@#]+", gauge(sentence, substitute_entities=substitute_entities))
    for size in NGRAM_SIZES:
        for index in range(len(tokens) - size + 1):
            gram = tokens[index:index + size]
            if not any(token in ("#", "@") for token in gram):
                continue
            if require_content and not any(
                    token not in _STOP and token not in ("#", "@") for token in gram):
                continue
            yield " ".join(gram)


def induce(dataset, *, substitute_entities: bool, require_content: bool) -> frozenset[str]:
    """Predicate status is a corpus property: recurrence across distinct passages, never one instance."""
    passages_with: defaultdict[str, set[str]] = defaultdict(set)
    for passage_id, record in dataset.items():
        seen = set()
        for start, end in D24.sentences(record["passage"]):
            for gram in skeletons(record["passage"][start:end],
                                  substitute_entities=substitute_entities,
                                  require_content=require_content):
                seen.add(gram)
        for gram in seen:
            passages_with[gram].add(passage_id)
    return frozenset(gram for gram, ids in passages_with.items() if len(ids) >= MIN_PASSAGES)


def events(passage: str, predicates: frozenset[str], *, substitute_entities: bool,
           require_content: bool):
    """(predicate, value, start, end, sentence_index) — a typed event, or nothing at all."""
    out = []
    spans = D24.sentences(passage)
    for index, (start, end) in enumerate(spans):
        text = passage[start:end]
        matched = {gram for gram in skeletons(text, substitute_entities=substitute_entities,
                                              require_content=require_content)
                   if gram in predicates}
        if not matched:
            continue                       # no induced predicate -> no event; fail closed
        predicate = max(matched, key=lambda g: (len(g), g))
        for match in _NUM.finditer(text):
            raw = match.group(1).replace(",", "")
            try:
                value = Decimal(raw)
            except Exception:  # noqa: BLE001
                continue
            out.append((predicate, value, start + match.start(1), start + match.end(1), index))
    return out, spans


def evaluate(question: str, passage: str, selector, predicates: frozenset[str], *,
             substitute_entities: bool, require_content: bool) -> dict:
    operator = operator_charge(question)
    if operator is None:
        return {"abstain": "no_operator_charge"}
    typed, spans = events(passage, predicates, substitute_entities=substitute_entities,
                          require_content=require_content)
    if not typed:
        return {"abstain": "no_typed_event"}
    if len(typed) > MAX_EVENTS:
        return {"abstain": "event_budget"}

    pairs = selector.obligations(question)
    keys = tuple(key for key, _ in pairs)
    per_sentence = [selector.closed_by(passage[s:e], keys) for s, e in spans]
    lexical_keys = {key for key in keys
                    if key.partition(":")[0] not in D24.EXCLUDED_FROM_BINDING}

    # The question selects ONE predicate: the induced predicate whose surface the question closes best.
    scored: dict[str, float] = {}
    for predicate, _value, _s, _e, index in typed:
        tokens = {token for token in predicate.split() if token not in ("#", "@")}
        folded = D24.normalise(question)
        overlap = sum(1 for token in tokens if token in folded)
        scored[predicate] = max(scored.get(predicate, 0.0), overlap / max(1, len(tokens)))
    best_score = max(scored.values())
    chosen = [predicate for predicate, value in scored.items() if value == best_score]
    if best_score <= 0.0:
        return {"abstain": "question_selects_no_predicate"}
    if len(chosen) != 1:
        return {"abstain": "degenerate_predicate_shell"}
    predicate = chosen[0]

    selected = [item for item in typed if item[0] == predicate]
    if not selected:
        return {"abstain": "no_event_of_predicate"}

    indices = {item[4] for item in selected}
    closed: set[str] = set()
    for index in indices:
        closed |= per_sentence[index]
    support = len(closed & lexical_keys) / len(lexical_keys) if lexical_keys else 1.0

    if operator == "count_distinct":
        value = Decimal(len({(item[2], item[3]) for item in selected}))
        spans_used = tuple((item[2], item[3]) for item in selected)
    elif operator == "sum":
        value = sum((item[1] for item in selected), Decimal(0))
        spans_used = tuple((item[2], item[3]) for item in selected)
    elif operator in ("argmax", "argmin"):
        pick = (max if operator == "argmax" else min)(selected, key=lambda item: item[1])
        value, spans_used = pick[1], ((pick[2], pick[3]),)
    elif operator == "diff":
        if len(selected) < 2:
            return {"abstain": "insufficient_operands"}
        hi = max(selected, key=lambda item: item[1])
        lo = min(selected, key=lambda item: item[1])
        if hi[1] == lo[1]:
            return {"abstain": "degenerate_operands"}
        value, spans_used = hi[1] - lo[1], ((hi[2], hi[3]), (lo[2], lo[3]))
    else:
        return {"abstain": "operator_out_of_numeric_scope"}

    return {"operator": operator, "predicate": predicate, "value": D24.canonical(value),
            "spans": spans_used, "support": support}


def run_arm(dataset, selector, *, substitute_entities: bool, require_content: bool) -> dict:
    predicates = induce(dataset, substitute_entities=substitute_entities,
                        require_content=require_content)
    resolved = correct = total = max_bytes = 0
    by_operator: Counter[str] = Counter()
    abstentions: Counter[str] = Counter()
    unique: dict[tuple[str, str], tuple[str, set[str]]] = {}
    for passage_id, record in dataset.items():
        passage = record["passage"]
        for qa in record["qa_pairs"]:
            gold = D24.gold_numbers(qa)
            if not gold:
                continue
            total += 1
            out = evaluate(qa["question"], passage, selector, predicates,
                           substitute_entities=substitute_entities,
                           require_content=require_content)
            if "abstain" in out:
                abstentions[out["abstain"]] += 1
                continue
            resolved += 1
            by_operator[out["operator"]] += 1
            correct += out["value"] in gold
            max_bytes = max(max_bytes, sum(e - s for s, e in out["spans"]))
            unique[(passage_id, D24.normalise(qa["question"]))] = (out["value"], gold)
    unique_resolved = len(unique)
    unique_correct = sum(1 for value, gold in unique.values() if value in gold)
    return {
        "induced_predicates": len(predicates),
        "numeric_questions": total, "resolved": resolved, "correct": correct,
        "selective_precision": correct / resolved if resolved else 0.0,
        "coverage": resolved / total if total else 0.0,
        "unique_resolved": unique_resolved, "unique_correct": unique_correct,
        "unique_selective_precision": unique_correct / unique_resolved if unique_resolved else 0.0,
        "by_operator": dict(sorted(by_operator.items())),
        "abstentions": dict(sorted(abstentions.items())),
        "maximum_active_proof_source_bytes": max_bytes,
        "sample_predicates": sorted(predicates)[:25],
    }


def main() -> None:
    manifest = json.loads(SOURCE_MANIFEST.read_text())
    train = manifest["splits"]["train"]
    path = ROOT / train["path"]
    if sha(path) != train["sha256"]:
        raise ValueError("V84 DROP training split digest mismatch")
    dataset = json.loads(path.read_text())
    selector = D24.Selector()

    arms = {
        "induced_gauge": run_arm(dataset, selector, substitute_entities=True, require_content=True),
        "control_frequency_only": run_arm(dataset, selector, substitute_entities=False,
                                          require_content=False),
    }
    full = arms["induced_gauge"]
    artifact = {
        "schema": "qhdre.v84.induced-predicate-d26-dev.v1",
        "split": "official_train_development_only",
        "source_manifest_sha256": sha(SOURCE_MANIFEST),
        "implementation_sha256": sha(IMPLEMENTATION),
        "train_sha256": train["sha256"],
        "execution": "offline_cpu_no_model_no_network_no_api_no_holdout_access",
        "holdout_state": "QUARANTINED_UNREAD",
        "scope": "numeric_answer_region_only",
        "frozen_parameters": {"min_passages": MIN_PASSAGES, "ngram_sizes": list(NGRAM_SIZES),
                              "max_events": MAX_EVENTS},
        "arms": arms,
        "baselines": {
            "hand_carved_families_d14": {"coverage": 0.005361, "selective_precision": 0.997590},
            "clonal_selection_d24": {"coverage": 0.464331, "selective_precision": 0.078401},
            "polyphonic_d25": {"coverage": 0.164967, "selective_precision": 0.084398},
        },
        "prior_art": (
            "mechanically this is pattern mining with slot abstraction (open information extraction, "
            "Hearst-style patterns); gauge invariance justifies the normalisation, it does not invent "
            "the mining. The candidate contribution is feeding induced predicates to a fail-closed "
            "verifier that recomputes from exact spans."
        ),
        "context_budget_pass": full["maximum_active_proof_source_bytes"] <= 2048,
        "entry_gate_pass": (full["unique_resolved"] >= 1000
                            and full["unique_selective_precision"] >= 0.995),
        "claim_limit": "training-only induced-predicate acquisition over the DROP numeric region",
    }
    body = json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode()
    artifact["result_sha256"] = hashlib.sha256(body).hexdigest()
    OUTPUT.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(json.dumps({name: {"predicates": arm["induced_predicates"],
                             "resolved": arm["resolved"],
                             "coverage": round(arm["coverage"], 6),
                             "precision": round(arm["selective_precision"], 6),
                             "unique_precision": round(arm["unique_selective_precision"], 6)}
                      for name, arm in arms.items()}, indent=2, sort_keys=True))
    print("result_sha256", artifact["result_sha256"])


if __name__ == "__main__":
    main()
