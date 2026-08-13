#!/usr/bin/env python3
"""V84-D24: clonal-selection acquisition on DROP numeric answers.

The inversion (see `lab/D24_CLONAL_SELECTION_CHARTER.md`): a paragraph does not PRODUCE structure, it
SELECTS structure. Instead of hand-carving one extractor per question family, we generate a repertoire
of candidate typed programs and let the paragraph select which one binds.

    question  -> obligations                       (the epitope)
    paragraph -> witnessed numeric operands         (the antigen surface)
    repertoire-> operator x witnessed operand set   (recombination)
    binding   -> a program binds iff every operand is independently witnessed by a sentence
                 that closes question obligations
    affinity  -> obligations closed / obligations required
    unique maximal binder -> answer; equal-affinity binders -> ABSTAIN (degenerate shell);
    no binder -> ABSTAIN with the residual recorded

This is NOT V71-V73. Those ranked a single typed span by lexical conservation and were refuted. Here the
unit of selection is a PROGRAM whose operands are each independently witnessed, and the value is
recomputed deterministically from the operands.

`sum` deliberately does not enumerate subsets: it sums the COMPLETE witnessed set. Enumerating subsets
would both explode combinatorially and let the mechanism manufacture a match. Completeness is a
precondition, not a convenience.

No gold value, answer vocabulary, qrel, model, endpoint or API takes part in selection. The official
development split is never opened.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from horizon_memory.proof_pressure_search import HorizonSearchEngine  # noqa: E402
from run_v84_operator_expressibility_d23 import classify as operator_charge  # noqa: E402

SOURCE_MANIFEST = ROOT / "lab/datasets/manifests/v84-drop-source-v1.json"
OUTPUT = ROOT / "lab/results/qhdre-v84-clonal-selection-d24-dev-v1.json"
IMPLEMENTATION = Path(__file__)

# --- frozen selection parameters, declared before measurement -------------------------------------
MAX_OPERANDS = 40          # numeric literals considered per paragraph; larger paragraphs abstain
MAX_PAIRS = 400            # ordered operand pairs for `diff`; beyond this the paragraph abstains
MIN_MARGIN = 0.05          # winner must exceed the runner-up by this lexical-support margin

# The epitope is the SPECIFIC channel only. An antibody binds a short discriminative motif, not the
# whole antigen surface. Binding is therefore all-or-nothing over these obligation kinds; there is no
# affinity threshold to tune.
SPECIFIC_KINDS = frozenset({"entity", "number", "temporal"})

# `relation` obligations are adjacent lexical bigrams ("chief>score") — word-order backbone, not
# semantics. Requiring them would require one particular paraphrase, which the gauge law forbids:
# a paraphrase must not change the address of a fact. They are excluded from binding by that law,
# not for convenience.
EXCLUDED_FROM_BINDING = frozenset({"relation", "answer", "polarity", "modality"})

_NUMBER = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\w])")
_SENTENCE = re.compile(r"[^.!?]+[.!?]?")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sentences(passage: str) -> list[tuple[int, int]]:
    """Authority-owned sentence spans; duplicate text stays distinguishable by position."""
    spans = []
    for match in _SENTENCE.finditer(passage):
        if match.group().strip():
            spans.append((match.start(), match.end()))
    return spans


def literals(passage: str, spans: list[tuple[int, int]]) -> list[tuple[Decimal, int, int, int]]:
    """(value, start, end, sentence_index) for every numeric literal, with its exact span."""
    out = []
    for index, (start, end) in enumerate(spans):
        text = passage[start:end]
        for match in _NUMBER.finditer(text):
            raw = match.group(1).replace(",", "")
            try:
                value = Decimal(raw)
            except Exception:  # noqa: BLE001 - malformed literal is simply not an operand
                continue
            out.append((value, start + match.start(1), start + match.end(1), index))
    return out


def normalise(text: str) -> str:
    return " ".join(text.casefold().split())


def gold_numbers(qa: dict) -> set[str]:
    values = set()
    for item in (qa["answer"], *qa.get("validated_answers", [])):
        number = item.get("number", "")
        if number != "":
            values.add(str(number))
    return values


def canonical(value: Decimal) -> str:
    quantised = value.normalize()
    text = format(quantised, "f")
    return text


class Selector:
    """Selection, not extraction. Obligations come from the question; the paragraph selects."""

    def __init__(self) -> None:
        self._engine = HorizonSearchEngine.__new__(HorizonSearchEngine)

    def obligations(self, question: str) -> tuple[tuple[str, float], ...]:
        compiled = HorizonSearchEngine.compile_obligations(self._engine, question)
        return tuple((item.key, item.weight) for item in compiled)

    @staticmethod
    def closed_by(sentence_text: str, obligation_keys: tuple[str, ...]) -> set[str]:
        folded = normalise(sentence_text)
        tokens = set(re.findall(r"[a-z0-9]+", folded))
        closed = set()
        for key in obligation_keys:
            kind, _, value = key.partition(":")
            if kind in EXCLUDED_FROM_BINDING:
                continue
            needle = normalise(value)
            if needle and (needle in tokens or needle in folded):
                closed.add(key)
        return closed


def evaluate(question: str, passage: str, selector: Selector) -> dict | None:
    """Return the selected program, or None when the paragraph selects nothing uniquely."""
    spans = sentences(passage)
    operands = literals(passage, spans)
    if not operands or len(operands) > MAX_OPERANDS:
        return None

    # The operator charge is part of the specificity, and it comes from the QUESTION, never from
    # binding. An antibody's specificity includes which reaction it triggers, not only where it sits.
    # Without this, `count_distinct` and `sum` share a witness set and collide as a degenerate shell
    # on every question, which is what the first execution measured.
    operator = operator_charge(question)
    if operator is None:
        return {"abstain": "no_operator_charge"}

    obligation_pairs = selector.obligations(question)
    keys = tuple(key for key, _ in obligation_pairs)
    epitope = {key for key in keys if key.partition(":")[0] in SPECIFIC_KINDS}
    lexical_keys = {key for key in keys
                    if key.partition(":")[0] not in SPECIFIC_KINDS
                    and key.partition(":")[0] not in EXCLUDED_FROM_BINDING}
    # An empty epitope has nothing to bind to. Fail closed rather than binding vacuously.
    if not epitope:
        return {"abstain": "empty_epitope"}

    # Which obligations does each sentence close? (the antigen surface)
    per_sentence = [selector.closed_by(passage[start:end], keys) for start, end in spans]

    # Binding is all-or-nothing over the epitope: the witness sentences must close it entirely.
    witness_sentences = {index for index, closed in enumerate(per_sentence) if closed & epitope}
    covered = set().union(*(per_sentence[index] for index in witness_sentences)) if witness_sentences else set()
    if not epitope <= covered:
        return {"abstain": "epitope_not_closed"}

    # Only operands sitting inside a binding sentence may enter a program.
    witnessed = [item for item in operands if item[3] in witness_sentences]
    if not witnessed:
        return {"abstain": "no_witnessed_operand"}

    def affinity(sentence_indices: set[int]) -> float:
        """Lexical support of the program's own witnesses. Used only for the margin, never to
        rescue a program whose epitope did not close."""
        if not lexical_keys:
            return 1.0
        closed: set[str] = set()
        for index in sentence_indices:
            closed |= per_sentence[index]
        return len(closed & lexical_keys) / len(lexical_keys)

    candidates: list[tuple[float, str, str, tuple]] = []

    # count_distinct over the complete witnessed set
    witness_indices = {item[3] for item in witnessed}
    score = affinity(witness_indices)
    candidates.append((score, "count_distinct", canonical(Decimal(len(witnessed))),
                       tuple((item[1], item[2]) for item in witnessed)))

    # sum over the COMPLETE witnessed set (no subset enumeration; completeness is a precondition)
    total = sum((item[0] for item in witnessed), Decimal(0))
    candidates.append((score, "sum", canonical(total),
                       tuple((item[1], item[2]) for item in witnessed)))

    # argmax / argmin over the witnessed set
    hi = max(witnessed, key=lambda item: item[0])
    lo = min(witnessed, key=lambda item: item[0])
    candidates.append((affinity({hi[3]}), "argmax", canonical(hi[0]), ((hi[1], hi[2]),)))
    candidates.append((affinity({lo[3]}), "argmin", canonical(lo[0]), ((lo[1], lo[2]),)))

    # diff over ordered operand pairs, each independently witnessed
    if len(witnessed) * (len(witnessed) - 1) <= MAX_PAIRS:
        for left in witnessed:
            for right in witnessed:
                if left is right:
                    continue
                if left[0] < right[0]:
                    continue
                candidates.append((affinity({left[3], right[3]}), "diff",
                                   canonical(left[0] - right[0]),
                                   ((left[1], left[2]), (right[1], right[2]))))

    # Only programs whose operator matches the question's declared charge may bind.
    admissible = [item for item in candidates if item[1] == operator]
    if not admissible:
        return {"abstain": "no_binder"}

    admissible.sort(key=lambda item: -item[0])
    best = admissible[0]
    # Degenerate shell: never tie-break equal affinity into a lucky winner.
    shell = [item for item in admissible if abs(item[0] - best[0]) < 1e-12]
    distinct_values = {item[2] for item in shell}
    if len(distinct_values) != 1:
        return {"abstain": "degenerate_shell", "shell_size": len(shell)}
    runner = next((item for item in admissible if item[2] != best[2]), None)
    if runner is not None and best[0] - runner[0] < MIN_MARGIN:
        return {"abstain": "insufficient_margin"}

    return {"operator": best[1], "value": best[2], "affinity": best[0], "spans": best[3]}


def main() -> None:
    manifest = json.loads(SOURCE_MANIFEST.read_text())
    train = manifest["splits"]["train"]
    path = ROOT / train["path"]
    if sha(path) != train["sha256"]:
        raise ValueError("V84 DROP training split digest mismatch")
    dataset = json.loads(path.read_text())

    selector = Selector()
    numeric_questions = resolved = correct = 0
    abstentions: Counter[str] = Counter()
    by_operator: Counter[str] = Counter()
    correct_by_operator: Counter[str] = Counter()
    max_bytes = 0
    unique: dict[tuple[str, str], tuple[str, set[str]]] = {}

    for passage_id, record in dataset.items():
        passage = record["passage"]
        for qa in record["qa_pairs"]:
            gold = gold_numbers(qa)
            if not gold:
                continue                     # numeric region only; other shapes are out of scope
            numeric_questions += 1
            outcome = evaluate(qa["question"], passage, selector)
            if outcome is None:
                abstentions["no_binder"] += 1
                continue
            if "abstain" in outcome:
                abstentions[outcome["abstain"]] += 1
                continue
            resolved += 1
            by_operator[outcome["operator"]] += 1
            hit = outcome["value"] in gold
            correct += hit
            correct_by_operator[outcome["operator"]] += hit
            max_bytes = max(max_bytes, sum(end - start for start, end in outcome["spans"]))
            unique[(passage_id, normalise(qa["question"]))] = (outcome["value"], gold)

    unique_resolved = len(unique)
    unique_correct = sum(1 for value, gold in unique.values() if value in gold)

    artifact = {
        "schema": "qhdre.v84.clonal-selection-d24-dev.v1",
        "split": "official_train_development_only",
        "source_manifest_sha256": sha(SOURCE_MANIFEST),
        "implementation_sha256": sha(IMPLEMENTATION),
        "train_sha256": train["sha256"],
        "execution": "offline_cpu_no_model_no_network_no_api_no_holdout_access",
        "holdout_state": "QUARANTINED_UNREAD",
        "frozen_parameters": {
            "max_operands": MAX_OPERANDS, "max_pairs": MAX_PAIRS,
            "min_margin": MIN_MARGIN, "binding_rule": "full closure of the specific channel; no affinity threshold",
        },
        "scope": "numeric_answer_region_only",
        "corpus": {"numeric_questions": numeric_questions},
        "raw_rows": {
            "resolved": resolved, "correct": correct,
            "selective_precision": correct / resolved if resolved else 0.0,
            "coverage": resolved / numeric_questions if numeric_questions else 0.0,
        },
        "unique_text_inputs": {
            "resolved": unique_resolved, "correct": unique_correct,
            "selective_precision": unique_correct / unique_resolved if unique_resolved else 0.0,
        },
        "by_operator": dict(sorted(by_operator.items())),
        "correct_by_operator": dict(sorted(correct_by_operator.items())),
        "abstentions": dict(sorted(abstentions.items())),
        "maximum_active_proof_source_bytes": max_bytes,
        "context_budget_pass": max_bytes <= 2048,
        "entry_gate_pass": (
            unique_resolved >= 1000
            and (unique_correct / unique_resolved if unique_resolved else 0.0) >= 0.995
        ),
        "claim_limit": (
            "training-only clonal-selection acquisition over the DROP numeric region; "
            "no holdout, no promotion, coverage and precision reported separately"
        ),
    }
    body = json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode()
    artifact["result_sha256"] = hashlib.sha256(body).hexdigest()
    OUTPUT.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "numeric_questions": numeric_questions,
        "resolved": resolved, "precision": artifact["raw_rows"]["selective_precision"],
        "coverage": artifact["raw_rows"]["coverage"],
        "unique_resolved": unique_resolved,
        "unique_precision": artifact["unique_text_inputs"]["selective_precision"],
        "by_operator": dict(sorted(by_operator.items())),
        "abstentions": dict(sorted(abstentions.items())),
        "result_sha256": artifact["result_sha256"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
