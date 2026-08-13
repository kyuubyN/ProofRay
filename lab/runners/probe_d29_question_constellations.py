#!/usr/bin/env python3
"""D29 feasibility probe: recurrent natural question contrasts inside one DROP world.

Pair construction is gold-free. Answers are inspected only after edges are frozen to describe whether
the selected natural interventions preserve or change the numeric denotation. Official DROP dev is never
opened.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from lab.denotational_generalization import abstract_question  # noqa: E402


SOURCE_MANIFEST = ROOT / "lab/datasets/manifests/v84-drop-source-v1.json"
OUTPUT = ROOT / "lab/results/qhdre-v84-question-constellation-d29-probe-v1.json"
IMPLEMENTATION = Path(__file__)
MIN_RECURRENT_PASSAGES = 3


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def numeric_gold(qa: dict) -> frozenset[str]:
    values = set()
    for answer in (qa.get("answer", {}), *qa.get("validated_answers", [])):
        value = answer.get("number", "")
        if value != "":
            values.add(str(value))
    return frozenset(values)


def tokens(question: str) -> tuple[str, ...]:
    return tuple(abstract_question(question).split())


def edit_distance(left: tuple[str, ...], right: tuple[str, ...]) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_token in enumerate(left, 1):
        current = [left_index]
        for right_index, right_token in enumerate(right, 1):
            current.append(min(
                current[-1] + 1,
                previous[right_index] + 1,
                previous[right_index - 1] + (left_token != right_token),
            ))
        previous = current
    return previous[-1]


def delta_signature(left: tuple[str, ...], right: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Canonical surface change; direction is removed->added after lexical orientation."""
    if right < left:
        left, right = right, left
    removed: list[str] = []
    added: list[str] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(a=left, b=right, autojunk=False).get_opcodes():
        if tag in {"delete", "replace"}:
            removed.extend(left[i1:i2])
        if tag in {"insert", "replace"}:
            added.extend(right[j1:j2])
    return tuple(removed), tuple(added)


def mutual_unique_nearest(items: tuple[tuple[str, ...], ...]) -> tuple[tuple[int, int, int], ...]:
    nearest: dict[int, tuple[int, int]] = {}
    for index, item in enumerate(items):
        distances = [(edit_distance(item, other), other_index)
                     for other_index, other in enumerate(items) if other_index != index and other != item]
        if not distances:
            continue
        best_distance = min(distance for distance, _ in distances)
        peers = [other_index for distance, other_index in distances if distance == best_distance]
        if len(peers) == 1:
            nearest[index] = (peers[0], best_distance)
    edges = set()
    for left, (right, distance) in nearest.items():
        if nearest.get(right) == (left, distance):
            edges.add((min(left, right), max(left, right), distance))
    return tuple(sorted(edges))


def run(dataset: dict) -> dict:
    total_numeric = passages_with_two = raw_edges = 0
    edge_rows = []
    passages_by_delta: defaultdict[tuple, set[str]] = defaultdict(set)
    for passage_id, record in dataset.items():
        qas = tuple(qa for qa in record["qa_pairs"] if numeric_gold(qa))
        total_numeric += len(qas)
        if len(qas) < 2:
            continue
        passages_with_two += 1
        surfaces = tuple(tokens(qa["question"]) for qa in qas)
        for left, right, distance in mutual_unique_nearest(surfaces):
            signature = delta_signature(surfaces[left], surfaces[right])
            if not signature[0] and not signature[1]:
                continue
            raw_edges += 1
            passages_by_delta[signature].add(passage_id)
            edge_rows.append((passage_id, left, right, distance, signature,
                              numeric_gold(qas[left]), numeric_gold(qas[right])))

    recurrent = {signature for signature, passage_ids in passages_by_delta.items()
                 if len(passage_ids) >= MIN_RECURRENT_PASSAGES}
    recurrent_rows = [row for row in edge_rows if row[4] in recurrent]
    covered = {(passage_id, index) for passage_id, left, right, *_ in recurrent_rows
               for index in (left, right)}
    changed = sum(left_gold.isdisjoint(right_gold)
                  for *_prefix, left_gold, right_gold in recurrent_rows)
    preserved = len(recurrent_rows) - changed
    recurrent_passages = {row[0] for row in recurrent_rows}
    frequencies = Counter(row[4] for row in recurrent_rows)

    return {
        "numeric_questions": total_numeric,
        "passages_with_at_least_two_numeric_questions": passages_with_two,
        "mutual_unique_nearest_edges": raw_edges,
        "recurrent_delta_types": len(recurrent),
        "recurrent_edges": len(recurrent_rows),
        "recurrent_passages": len(recurrent_passages),
        "questions_covered_by_recurrent_edges": len(covered),
        "question_coverage": len(covered) / total_numeric if total_numeric else 0.0,
        "recurrent_edges_with_changed_denotation": changed,
        "recurrent_edges_with_preserved_denotation": preserved,
        "top_recurrent_deltas": [
            {"removed": list(signature[0]), "added": list(signature[1]), "edges": count,
             "passages": len(passages_by_delta[signature])}
            for signature, count in frequencies.most_common(25)
        ],
        "probe_gate_pass": (
            len(recurrent_rows) >= 500
            and len(covered) / total_numeric >= 0.05
            and len(recurrent_passages) >= 50
            and changed > 0 and preserved > 0
        ),
    }


def main() -> None:
    manifest = json.loads(SOURCE_MANIFEST.read_text())
    train = manifest["splits"]["train"]
    path = ROOT / train["path"]
    if sha(path) != train["sha256"]:
        raise ValueError("V84 DROP training split digest mismatch")
    dataset = json.loads(path.read_text())
    metrics = run(dataset)
    artifact = {
        "schema": "qhdre.v84.question-constellation-d29-probe.v1",
        "split": "official_train_diagnostic_only",
        "train_sha256": train["sha256"],
        "source_manifest_sha256": sha(SOURCE_MANIFEST),
        "implementation_sha256": sha(IMPLEMENTATION),
        "execution": "offline_cpu_no_model_no_network_no_api_no_holdout_access",
        "holdout_state": "QUARANTINED_UNREAD",
        "frozen_parameters": {"minimum_recurrent_passages": MIN_RECURRENT_PASSAGES,
                              "pairing": "unique_mutual_nearest_token_edit_no_threshold"},
        "metrics": metrics,
        "claim_limit": "structural feasibility of natural question constellations; no decoder result",
    }
    body = json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode()
    artifact["result_sha256"] = hashlib.sha256(body).hexdigest()
    OUTPUT.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(json.dumps(metrics, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
