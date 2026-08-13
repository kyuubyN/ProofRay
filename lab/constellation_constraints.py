"""Fail-closed constraint propagation for D29 question constellations.

Candidates are expected to have passed D28's training-only denotation filter before entering this
module.  D29 never ranks them.  Recurrent surface transformations constrain candidate program deltas
across independent worlds; unsupported candidates are removed until a fixed point is reached.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


ProgramDelta = tuple[tuple[str, str, str], ...]


@dataclass(frozen=True)
class ProgramCandidate:
    candidate_id: str
    charges: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not self.candidate_id or self.charges != tuple(sorted(self.charges)):
            raise ValueError("candidate id and canonically sorted charges are required")
        if len(dict(self.charges)) != len(self.charges):
            raise ValueError("program charge fields must be unique")


@dataclass(frozen=True)
class ConstellationEdge:
    left_question_id: str
    right_question_id: str
    surface_generator: str

    def __post_init__(self) -> None:
        if not self.left_question_id or not self.right_question_id or not self.surface_generator:
            raise ValueError("edge endpoints and generator are required")
        if self.left_question_id == self.right_question_id:
            raise ValueError("a contrast requires two different questions")


@dataclass(frozen=True)
class PropagationResult:
    state: str  # resolved | open | conflict
    question_domains: tuple[tuple[str, tuple[str, ...]], ...]
    generator_domains: tuple[tuple[str, tuple[ProgramDelta, ...]], ...]
    iterations: int
    reason: str


def program_delta(left: ProgramCandidate, right: ProgramCandidate) -> ProgramDelta:
    """Covariant charge change under an oriented surface transformation."""
    left_charges = dict(left.charges)
    right_charges = dict(right.charges)
    return tuple(
        (field, left_charges.get(field, "-"), right_charges.get(field, "-"))
        for field in sorted(set(left_charges) | set(right_charges))
        if left_charges.get(field) != right_charges.get(field)
    )


def propagate_constellation_constraints(
    domains: Mapping[str, tuple[ProgramCandidate, ...]],
    edges: tuple[ConstellationEdge, ...],
) -> PropagationResult:
    """Enforce recurrent-generator and endpoint arc consistency to a fixed point.

    This is deliberately not a score or a tie-breaker.  A fixed point with more than one candidate is
    `open`; an empty intersection is `conflict`; only singleton questions and generators are resolved.
    """
    if not domains or not edges:
        raise ValueError("non-empty question domains and constellation edges are required")
    mutable = {question_id: tuple(sorted(candidates, key=lambda item: item.candidate_id))
               for question_id, candidates in domains.items()}
    if any(not question_id or not candidates or
           len({candidate.candidate_id for candidate in candidates}) != len(candidates)
           for question_id, candidates in mutable.items()):
        raise ValueError("every question needs uniquely identified candidates")
    if any(edge.left_question_id not in mutable or edge.right_question_id not in mutable
           for edge in edges):
        raise ValueError("every edge endpoint must have a candidate domain")

    generators: dict[str, tuple[ProgramDelta, ...]] = {}
    iterations = 0
    while True:
        iterations += 1
        before = {key: tuple(item.candidate_id for item in value) for key, value in mutable.items()}

        # The same surface generator must denote one program transformation in every independent world.
        by_generator: dict[str, list[set[ProgramDelta]]] = {}
        for edge in edges:
            possible = {
                program_delta(left, right)
                for left in mutable[edge.left_question_id]
                for right in mutable[edge.right_question_id]
            }
            by_generator.setdefault(edge.surface_generator, []).append(possible)
        generators = {}
        for generator, populations in by_generator.items():
            shared = set.intersection(*populations)
            if not shared:
                return _result("conflict", mutable, {}, iterations,
                               f"generator {generator} has no covariant program delta")
            generators[generator] = tuple(sorted(shared))

        # A candidate survives only if every incident contrast has a compatible candidate at the other
        # endpoint under a still-valid recurrent transformation.
        for question_id, candidates in tuple(mutable.items()):
            survivors = []
            incident = tuple(edge for edge in edges
                             if question_id in (edge.left_question_id, edge.right_question_id))
            for candidate in candidates:
                supported = True
                for edge in incident:
                    allowed = set(generators[edge.surface_generator])
                    if question_id == edge.left_question_id:
                        peers = mutable[edge.right_question_id]
                        compatible = any(program_delta(candidate, peer) in allowed for peer in peers)
                    else:
                        peers = mutable[edge.left_question_id]
                        compatible = any(program_delta(peer, candidate) in allowed for peer in peers)
                    if not compatible:
                        supported = False
                        break
                if supported:
                    survivors.append(candidate)
            if not survivors:
                return _result("conflict", mutable, generators, iterations,
                               f"question {question_id} has no globally supported program")
            mutable[question_id] = tuple(survivors)

        after = {key: tuple(item.candidate_id for item in value) for key, value in mutable.items()}
        if after == before:
            break

    resolved = (all(len(candidates) == 1 for candidates in mutable.values())
                and all(len(deltas) == 1 for deltas in generators.values()))
    return _result("resolved" if resolved else "open", mutable, generators, iterations,
                   "unique covariant constellation" if resolved
                   else "multiple globally consistent constellations remain")


def _result(state: str, domains: Mapping[str, tuple[ProgramCandidate, ...]],
            generators: Mapping[str, tuple[ProgramDelta, ...]], iterations: int,
            reason: str) -> PropagationResult:
    return PropagationResult(
        state=state,
        question_domains=tuple(sorted(
            (question_id, tuple(item.candidate_id for item in candidates))
            for question_id, candidates in domains.items()
        )),
        generator_domains=tuple(sorted(generators.items())),
        iterations=iterations,
        reason=reason,
    )
