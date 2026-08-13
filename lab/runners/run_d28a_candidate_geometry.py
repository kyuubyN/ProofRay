#!/usr/bin/env python3
"""D28A diagnostic: reachability, ambiguity and code distance before semantic selection."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import statistics
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "lab/runners") not in sys.path:
    sys.path.insert(0, str(ROOT / "lab/runners"))

from lab.denotational_generalization import AbstractProgram, counterfactual_worlds  # noqa: E402
import run_v84_clonal_selection_d24 as D24  # noqa: E402
import run_v84_induced_predicate_d26 as D26  # noqa: E402


SOURCE_MANIFEST = ROOT / "lab/datasets/manifests/v84-drop-source-v1.json"
OUTPUT = ROOT / "lab/results/qhdre-v84-d28a-candidate-geometry-v1.json"
IMPLEMENTATION = Path(__file__)
MAX_LITERALS = 40
MAX_PROGRAMS = 4096
COUNTERFACTUAL_WORLDS = 7


@dataclass(frozen=True)
class Atom:
    identity: str
    predicate: str
    value: Decimal


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def untyped_atoms(passage: str) -> tuple[Atom, ...]:
    spans = D24.sentences(passage)
    return tuple(Atom(f"span:{start}:{end}", "*", value)
                 for value, start, end, _sentence in D24.literals(passage, spans))


def typed_atoms(passage: str, predicates: frozenset[str]) -> tuple[Atom, ...]:
    events, _spans = D26.events(passage, predicates, substitute_entities=True,
                                require_content=True)
    return tuple(Atom(f"span:{start}:{end}", predicate, value)
                 for predicate, value, start, end, _sentence in events)


def enumerate_programs(atoms: tuple[Atom, ...]) -> tuple[tuple[AbstractProgram, str], ...] | None:
    if not atoms or len(atoms) > MAX_LITERALS or len({atom.identity for atom in atoms}) != len(atoms):
        return None
    world = {atom.identity: atom.value for atom in atoms}
    by_predicate: defaultdict[str, list[Atom]] = defaultdict(list)
    for atom in atoms:
        by_predicate[atom.predicate].append(atom)
    programs: dict[tuple[str, str, tuple[str, ...]], AbstractProgram] = {}
    for predicate, group in by_predicate.items():
        identities = tuple(atom.identity for atom in group)
        for identity in identities:
            program = AbstractProgram("lookup", (identity,))
            programs[(predicate, program.operator, program.operands)] = program
        for operator in ("count", "sum", "argmax", "argmin"):
            program = AbstractProgram(operator, identities)
            programs[(predicate, operator, identities)] = program
        for left in identities:
            for right in identities:
                if left == right:
                    continue
                program = AbstractProgram("difference", (left, right))
                programs[(predicate, program.operator, program.operands)] = program
        if len(programs) > MAX_PROGRAMS:
            return None
    return tuple((program, predicate) for (predicate, _operator, _operands), program
                 in sorted(programs.items()))


def class_key(program: AbstractProgram, predicate: str,
              worlds: tuple[dict[str, Decimal], ...]) -> tuple[str, ...]:
    signature = tuple(program.execute(world) for world in worlds)
    signature_digest = hashlib.sha256("\x1f".join(signature).encode()).hexdigest()
    return program.operator, predicate, str(len(program.operands)), signature_digest


def minimum_distance(classes: tuple[tuple[str, ...], ...]) -> int:
    if len(classes) < 2:
        return 0
    return min(sum(left != right for left, right in zip(a, b))
               for index, a in enumerate(classes) for b in classes[index + 1:])


def summarise(dataset: dict, atom_builder) -> dict:
    total = reachable = identifiable = budget_failures = distance_three = 0
    consistent_counts = []
    class_counts = []
    distances = []
    for _passage_id, record in dataset.items():
        qas = tuple(qa for qa in record["qa_pairs"] if D24.gold_numbers(qa))
        total += len(qas)
        if not qas:
            continue
        atoms = atom_builder(record["passage"])
        candidates = enumerate_programs(atoms)
        if candidates is None:
            budget_failures += len(qas)
            continue
        world = {atom.identity: atom.value for atom in atoms}
        identities = tuple(world)
        worlds = counterfactual_worlds(identities, worlds=COUNTERFACTUAL_WORLDS)
        # Every question in the paragraph sees the same closed candidate population.  Execute and
        # fingerprint it once, then index by observed denotation; this changes no candidate or gate.
        by_denotation: defaultdict[str, list[tuple[AbstractProgram, str, tuple[str, ...]]]] = defaultdict(list)
        for program, predicate in candidates:
            value = program.execute(world)
            by_denotation[value].append((program, predicate, class_key(program, predicate, worlds)))
        for qa in qas:
            gold = D24.gold_numbers(qa)
            consistent = tuple(item for value in gold for item in by_denotation.get(value, ()))
            if not consistent:
                continue
            reachable += 1
            classes = tuple(sorted({item[2] for item in consistent}))
            consistent_counts.append(len(consistent))
            class_counts.append(len(classes))
            if len(classes) == 1:
                identifiable += 1
                continue
            distance = minimum_distance(classes)
            distances.append(distance)
            distance_three += distance >= 3

    def distribution(values):
        if not values:
            return {"count": 0, "median": 0, "p95": 0, "maximum": 0}
        ordered = sorted(values)
        return {
            "count": len(values),
            "median": statistics.median(ordered),
            "p95": ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))],
            "maximum": ordered[-1],
        }

    ambiguous = reachable - identifiable
    return {
        "numeric_questions": total,
        "candidate_budget_failures": budget_failures,
        "reachable": reachable,
        "candidate_reachability": reachable / total if total else 0.0,
        "identifiable": identifiable,
        "identifiable_given_reachable": identifiable / reachable if reachable else 0.0,
        "ambiguous_reachable": ambiguous,
        "ambiguous_with_minimum_distance_at_least_3": distance_three,
        "distance_3_rate_given_ambiguous": distance_three / ambiguous if ambiguous else 0.0,
        "gold_consistent_programs": distribution(consistent_counts),
        "counterfactual_classes": distribution(class_counts),
        "minimum_distance_among_ambiguous": distribution(distances),
        "reachability_gate_pass": reachable / total >= 0.95 if total else False,
        "identifiability_gate_pass": identifiable / reachable >= 0.90 if reachable else False,
        "distance_gate_pass": distance_three / ambiguous > 0.5 if ambiguous else False,
    }


def main() -> None:
    manifest = json.loads(SOURCE_MANIFEST.read_text())
    train = manifest["splits"]["train"]
    path = ROOT / train["path"]
    if sha(path) != train["sha256"]:
        raise ValueError("V84 DROP training split digest mismatch")
    dataset = json.loads(path.read_text())
    predicates = D26.induce(dataset, substitute_entities=True, require_content=True)
    arms = {
        "untyped_literal_universe": summarise(dataset, untyped_atoms),
        "typed_d26_predicates": summarise(dataset, lambda passage: typed_atoms(passage, predicates)),
    }
    artifact = {
        "schema": "qhdre.v84.d28a-candidate-geometry.v1",
        "split": "official_train_diagnostic_only",
        "train_sha256": train["sha256"],
        "source_manifest_sha256": sha(SOURCE_MANIFEST),
        "implementation_sha256": sha(IMPLEMENTATION),
        "execution": "offline_cpu_no_model_no_network_no_api_no_holdout_access",
        "holdout_state": "QUARANTINED_UNREAD",
        "frozen_parameters": {
            "max_literals": MAX_LITERALS,
            "max_programs": MAX_PROGRAMS,
            "counterfactual_worlds": COUNTERFACTUAL_WORLDS,
            "operators": ["lookup", "count", "sum", "argmax", "argmin", "difference"],
        },
        "induced_d26_predicates": len(predicates),
        "arms": arms,
        "claim_limit": "training-only candidate geometry diagnostic; gold-filtered oracle, no decoder",
    }
    body = json.dumps(artifact, sort_keys=True, separators=(",", ":")).encode()
    artifact["result_sha256"] = hashlib.sha256(body).hexdigest()
    OUTPUT.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
    print(json.dumps(arms, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
