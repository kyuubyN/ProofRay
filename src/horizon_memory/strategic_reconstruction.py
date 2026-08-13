# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Counterfactual strategist that rebuilds tactics from invariant equations."""
from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations


@dataclass(frozen=True, order=True)
class ConstantEquation:
    equation_id: str
    required_abilities: tuple[str, ...]
    maximum_compute: float
    maximum_context: int
    require_provenance: bool = True
    require_contradiction_gate: bool = True

    def __post_init__(self) -> None:
        if not self.equation_id or not self.required_abilities \
                or self.required_abilities != tuple(sorted(set(self.required_abilities))) \
                or self.maximum_compute <= 0 or self.maximum_context < 1:
            raise ValueError("invalid constant strategic equation")


@dataclass(frozen=True, order=True)
class StrategicComponent:
    component_id: str
    abilities: tuple[str, ...]
    compute_cost: float
    context_cost: int
    provenance_preserving: bool
    contradiction_gated: bool

    def __post_init__(self) -> None:
        if not self.component_id or not self.abilities \
                or self.abilities != tuple(sorted(set(self.abilities))) \
                or self.compute_cost < 0 or self.context_cost < 0:
            raise ValueError("invalid strategic component")


@dataclass(frozen=True, order=True)
class PlausibleWorld:
    world_id: str
    probability: float
    impact: float
    behavioral_signature: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.world_id or not 0 <= self.probability <= 1 or self.impact < 0 \
                or not self.behavioral_signature \
                or self.behavioral_signature != tuple(sorted(set(self.behavioral_signature))):
            raise ValueError("invalid plausible strategic world")


@dataclass(frozen=True, order=True)
class VerifiedTacticOutcome:
    tactic_components: tuple[str, ...]
    world_id: str
    correct: int
    wrong: int
    false_accepts: int
    missed: int
    verified_at: float
    verifier_fact_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.tactic_components \
                or self.tactic_components != tuple(sorted(set(self.tactic_components))) \
                or not self.world_id or min(self.correct, self.wrong,
                                             self.false_accepts, self.missed) < 0 \
                or self.verified_at < 0 or not self.verifier_fact_ids \
                or self.verifier_fact_ids != tuple(sorted(set(self.verifier_fact_ids))):
            raise ValueError("invalid verified tactic outcome")


@dataclass(frozen=True)
class StrategicPlan:
    state: str
    component_ids: tuple[str, ...]
    covered_abilities: tuple[str, ...]
    robust_utility: float
    expected_utility: float
    compute_cost: float
    context_cost: int
    reconstruction_distance: int
    reason: str


class CounterfactualStrategist:
    """Search a bounded component grammar; remain loyal only to invariants.

    A tactic is selected by delayed outcome ledgers across declared plausible worlds.
    Worst-world utility dominates expectation, preventing a flashy average strategy from
    hiding catastrophic regimes. Rebuilding changes future structure, never current facts.
    """

    def __init__(self, *, maximum_components: int = 3, robustness_weight: float = .7,
                 complexity_rate: float = .02, reconstruction_rate: float = .01,
                 false_accept_weight: float = 3.0):
        if maximum_components < 1 or not 0 <= robustness_weight <= 1 \
                or min(complexity_rate, reconstruction_rate) < 0 \
                or false_accept_weight < 1:
            raise ValueError("invalid counterfactual strategist")
        self.maximum_components = maximum_components
        self.robustness_weight = robustness_weight
        self.complexity_rate = complexity_rate
        self.reconstruction_rate = reconstruction_rate
        self.false_accept_weight = false_accept_weight

    def _utility(self, outcome: VerifiedTacticOutcome) -> float:
        total = outcome.correct + outcome.wrong + outcome.false_accepts + outcome.missed
        if total == 0:
            return -1.0
        return (outcome.correct - outcome.wrong
                - self.false_accept_weight * outcome.false_accepts
                - outcome.missed) / total

    def reconstruct(self, equation: ConstantEquation,
                    components: tuple[StrategicComponent, ...],
                    worlds: tuple[PlausibleWorld, ...],
                    outcomes: tuple[VerifiedTacticOutcome, ...], *,
                    issued_at: float, incumbent: tuple[str, ...] = ()) -> StrategicPlan:
        if components != tuple(sorted(set(components))) or not components \
                or worlds != tuple(sorted(set(worlds))) or not worlds \
                or outcomes != tuple(sorted(set(outcomes))) \
                or incumbent != tuple(sorted(set(incumbent))) \
                or issued_at < 0 or any(outcome.verified_at >= issued_at for outcome in outcomes):
            raise ValueError("strategy inputs must be canonical and verified ex ante")
        by_id = {component.component_id: component for component in components}
        if len(by_id) != len(components) or any(item not in by_id for item in incumbent):
            raise ValueError("unknown or duplicate strategic component")
        outcome_map = {(outcome.tactic_components, outcome.world_id): outcome
                       for outcome in outcomes}
        if len(outcome_map) != len(outcomes):
            raise ValueError("duplicate tactic/world verification")
        candidates = []
        required = set(equation.required_abilities)
        for width in range(1, min(self.maximum_components, len(components)) + 1):
            for selected in combinations(components, width):
                ids = tuple(sorted(component.component_id for component in selected))
                covered = set().union(*(set(component.abilities) for component in selected))
                compute = sum(component.compute_cost for component in selected)
                context = sum(component.context_cost for component in selected)
                if not required <= covered or compute > equation.maximum_compute \
                        or context > equation.maximum_context \
                        or equation.require_provenance and not all(
                            component.provenance_preserving for component in selected) \
                        or equation.require_contradiction_gate and not all(
                            component.contradiction_gated for component in selected):
                    continue
                world_utilities = []
                complete = True
                for world in worlds:
                    outcome = outcome_map.get((ids, world.world_id))
                    if outcome is None:
                        complete = False
                        break
                    world_utilities.append((world, self._utility(outcome)))
                if not complete:
                    continue
                worst = min(utility * world.impact for world, utility in world_utilities)
                probability_mass = sum(world.probability for world, _ in world_utilities)
                expected = sum(world.probability * world.impact * utility
                               for world, utility in world_utilities) / max(probability_mass, 1e-12)
                reconstruction = len(set(ids) ^ set(incumbent))
                robust = (self.robustness_weight * worst
                          + (1 - self.robustness_weight) * expected
                          - self.complexity_rate * (compute + context / equation.maximum_context)
                          - self.reconstruction_rate * reconstruction)
                candidates.append((robust, expected, -compute, -context, ids,
                                   tuple(sorted(covered)), reconstruction))
        if not candidates:
            return StrategicPlan("abstain", (), (), -math.inf, -math.inf, 0, 0, 0,
                                 "no verified reconstruction satisfies every constant equation")
        robust, expected, neg_compute, neg_context, ids, covered, distance = max(candidates)
        return StrategicPlan("planned", ids, covered, robust, expected, -neg_compute,
                             -neg_context, distance,
                             "bounded counterfactual reconstruction maximized robust verified utility")
