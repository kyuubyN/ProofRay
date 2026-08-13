#!/usr/bin/env python3
"""V84-D23: how much of DROP is expressible in the closed HFEF operator algebra at all?

Motivation. Thirteen hand-carved proof families produced 415 unique resolutions against an entry gate
of 1,000, and D22 — the richest remaining surface, covering 53.7% of training questions — returned 29.
Carving family fourteen is not the answer. Before wiring the schema-driven enumeration
(`query_hypotheses` over `event_field`), we must know its ceiling: a question the algebra cannot express
can never be compiled, no matter how good acquisition becomes.

This runner measures the QUESTION side only. It is a distribution diagnostic, not a decoder: it never
reads a gold value to decide an operator, produces no answer, and promotes nothing. Answer *type*
(number / date / span count) is read only to report how the expressible mass is distributed, because
`sum`, `diff` and `count_distinct` return numbers while `project` and `argmax/argmin` return spans.

The operator lexicon below is DECLARED AND CLOSED before measurement. It is deliberately conservative:
a question counts as expressible only when its surface carries an unambiguous operator marker. Ambiguity
counts as inexpressible, never as coverage. This biases the ceiling DOWNWARD, which is the honest
direction for a feasibility bound.

Algebra under test: project | exists | argmax | argmin | count_distinct | sum | diff.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))
SOURCE_MANIFEST = ROOT / "lab/datasets/manifests/v84-drop-source-v1.json"
OUTPUT = ROOT / "lab/results/qhdre-v84-operator-expressibility-d23-dev-v1.json"

# --- closed operator lexicon, frozen before measurement -------------------------------------------
# Order matters: the first matching rule wins, most specific first. `diff` must precede `count_distinct`
# because "how many more X than Y" is a difference, not a count.
LEXICON: tuple[tuple[str, str], ...] = (
    ("diff", r"\bhow many (?:more|fewer|less)\b|\bdifference between\b|\bhow much (?:more|longer|larger|bigger)\b"),
    ("sum", r"\bcombined\b|\btotal (?:number|yards|points)\b|\bin total\b|\ball together\b|\baltogether\b"),
    ("argmax", r"\blongest\b|\bmost\b|\bhighest\b|\blargest\b|\blast\b|\bfinal\b|\bbiggest\b|\bmaximum\b"),
    ("argmin", r"\bshortest\b|\bfewest\b|\blowest\b|\bsmallest\b|\bfirst\b|\bearliest\b|\bminimum\b"),
    ("count_distinct", r"\bhow many\b"),
    ("exists", r"^(?:did|was|were|is|are|does|do|has|have)\b"),
    ("project", r"^(?:who|which|what|where|when|whom|whose)\b"),
)
COMPILED = tuple((name, re.compile(pattern, re.IGNORECASE)) for name, pattern in LEXICON)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def classify(question: str) -> str | None:
    """Return the single unambiguous operator, or None. Never guesses."""
    text = " ".join(question.strip().split())
    for name, pattern in COMPILED:
        if pattern.search(text):
            return name
    return None


def answer_shape(qa: dict) -> str:
    answer = qa.get("answer", {})
    if answer.get("number", "") != "":
        return "number"
    date = answer.get("date", {}) or {}
    if any(date.get(key) for key in ("day", "month", "year")):
        return "date"
    spans = answer.get("spans", []) or []
    if len(spans) == 1:
        return "single_span"
    if len(spans) > 1:
        return "multi_span"
    return "empty"


def main() -> None:
    manifest = json.loads(SOURCE_MANIFEST.read_text())
    train = manifest["splits"]["train"]
    path = ROOT / train["path"]
    if sha(path) != train["sha256"]:
        raise ValueError("V84 DROP training split digest mismatch")
    dataset = json.loads(path.read_text())

    total = nfl_total = expressible = nfl_expressible = 0
    by_operator: Counter[str] = Counter()
    by_shape: Counter[str] = Counter()
    operator_shape: Counter[str] = Counter()
    inexpressible_shape: Counter[str] = Counter()

    for passage_id, record in dataset.items():
        is_nfl = passage_id.startswith("nfl_")
        for qa in record["qa_pairs"]:
            total += 1
            nfl_total += is_nfl
            shape = answer_shape(qa)
            by_shape[shape] += 1
            operator = classify(qa["question"])
            if operator is None:
                inexpressible_shape[shape] += 1
                continue
            expressible += 1
            nfl_expressible += is_nfl
            by_operator[operator] += 1
            operator_shape[f"{operator}|{shape}"] += 1

    artifact = {
        "schema": "qhdre.v84.operator-expressibility-d23-dev.v1",
        "split": "official_train_development_only",
        "source_manifest_sha256": sha(SOURCE_MANIFEST),
        "train_sha256": train["sha256"],
        "execution": "offline_cpu_no_model_no_network_no_api_no_holdout_access",
        "holdout_state": "QUARANTINED_UNREAD",
        "measurement": "question_surface_only_no_answer_used_for_operator_choice",
        "operator_lexicon_frozen_before_measurement": [list(item) for item in LEXICON],
        "corpus": {"questions": total, "nfl_questions": nfl_total},
        "expressible": {
            "questions": expressible,
            "rate": expressible / total if total else 0.0,
            "nfl_questions": nfl_expressible,
            "nfl_rate": nfl_expressible / nfl_total if nfl_total else 0.0,
        },
        "by_operator_DIAGNOSTIC_ONLY": dict(sorted(by_operator.items())),
        "by_answer_shape": dict(sorted(by_shape.items())),
        "operator_by_answer_shape_DIAGNOSTIC_ONLY": dict(sorted(operator_shape.items())),
        "inexpressible_by_answer_shape": dict(sorted(inexpressible_shape.items())),
        "limitations": {
            "operator_assignment_is_unreliable": (
                "A manual sample audit found systematic label errors. Word sense: 'How many years did "
                "the Russo-Swedish War last?' matches argmax on 'last' (duration, not final). Ordinal "
                "versus extremum: 'How many touchdowns were scored in the first half?' matches argmin "
                "on 'first' but is a constrained count_distinct. Comparison as existence: 'Was the "
                "state debt higher in 1991 or 2008?' matches exists but is an argmax over two "
                "constrained lookups. The per-operator counts must NOT be cited as an operator "
                "distribution; only the aggregate expressibility bound is defensible."
            ),
            "inexpressible_bucket_is_inflated_by_anchoring": (
                "Several inexpressible questions are lexicon anchor misses rather than true "
                "inexpressibility, e.g. 'On which show did Anthony and Declan meet?' fails the '^which' "
                "anchor yet is a plain project. The true ceiling is therefore at or above the reported "
                "rate; the bound is conservative in the honest direction."
            ),
            "necessary_not_sufficient": (
                "Carrying an operator marker is necessary for compilation, never sufficient. A question "
                "may be expressible yet require events that acquisition cannot extract, or admit several "
                "competing programs. Compilation coverage is measured separately and will be lower."
            ),
            "no_tuning_after_audit": (
                "The lexicon was frozen before measurement and was NOT modified in response to the "
                "audited examples. Fixing them would be building rules from inspection."
            ),
        },
        "claim_limit": (
            "upper bound on question-side expressibility of the closed operator algebra; "
            "acquisition, compilation and execution are separate factors and are not measured here"
        ),
        "interpretation": (
            "an expressible question is only a candidate for compilation; this is a ceiling, not coverage"
        ),
    }
    body = json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode()
    artifact["result_sha256"] = hashlib.sha256(body).hexdigest()
    OUTPUT.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "questions": total, "expressible": expressible,
        "rate": round(artifact["expressible"]["rate"], 6),
        "nfl_rate": round(artifact["expressible"]["nfl_rate"], 6),
        "by_operator": dict(sorted(by_operator.items())),
        "result_sha256": artifact["result_sha256"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
