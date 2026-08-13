# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Responsible causal ego: one accountable center, many conserved alternatives."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class OwnedAbility:
    owner_fact_id: int
    layer: str
    strength: float
    witness_fact_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if self.owner_fact_id < 0 or not self.layer or not 0 <= self.strength <= 1 \
                or not self.witness_fact_ids \
                or self.witness_fact_ids != tuple(sorted(set(self.witness_fact_ids))):
            raise ValueError("invalid ego ability")


@dataclass(frozen=True, order=True)
class EgoClaim:
    fact_id: int
    intrinsic: float
    impact: float
    abilities: tuple[OwnedAbility, ...]
    hard_contradictions: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if self.fact_id < 0 or not 0 <= self.intrinsic <= 1 or self.impact < 0 \
                or self.abilities != tuple(sorted(set(self.abilities))) \
                or self.hard_contradictions != tuple(sorted(set(self.hard_contradictions))):
            raise ValueError("invalid ego claim")
        if any(item.owner_fact_id != self.fact_id for item in self.abilities):
            raise ValueError("a claim cannot borrow another candidate's ability")
        if len({item.layer for item in self.abilities}) != len(self.abilities):
            raise ValueError("each owned layer must appear once; duplicates do not create drive")


@dataclass(frozen=True)
class EgoDecision:
    state: str
    winner_fact_id: int | None
    drive: float
    runner_up_drive: float
    coverage: float
    evidence_fact_ids: tuple[int, ...]
    peripheral_fact_ids: tuple[int, ...]
    reason: str


class ResponsibleCausalEgo:
    """Winner takes responsibility, not evidence from its rivals.

    The center uses a geometric conjunction of required abilities, intrinsic ownership
    and impact.  Consequently one spectacular layer cannot compensate for a missing one.
    The halo is conserved as ordered counterfactuals but has no vote after commitment.
    """

    def __init__(self, required_layers: tuple[str, ...], *, minimum_coverage: float = 1.0,
                 minimum_drive: float = .1, minimum_margin: float = .05):
        if not required_layers or required_layers != tuple(sorted(set(required_layers))) \
                or not 0 < minimum_coverage <= 1 or minimum_drive < 0 or minimum_margin < 0:
            raise ValueError("invalid responsible ego boundary")
        self.required_layers = required_layers
        self.minimum_coverage = minimum_coverage
        self.minimum_drive = minimum_drive
        self.minimum_margin = minimum_margin

    def _measure(self, claim: EgoClaim) -> tuple[float, float, tuple[int, ...]]:
        if claim.hard_contradictions:
            return 0.0, 0.0, claim.hard_contradictions
        by_layer = {item.layer: item for item in claim.abilities}
        present = tuple(layer for layer in self.required_layers
                        if by_layer.get(layer) and by_layer[layer].strength > 0)
        coverage = len(present) / len(self.required_layers)
        if coverage < self.minimum_coverage:
            return 0.0, coverage, ()
        factors = (max(claim.intrinsic, 1e-12),
                   max(min(1.0, claim.impact), 1e-12),
                   *(max(by_layer[layer].strength, 1e-12)
                     for layer in self.required_layers))
        drive = math.prod(factors) ** (1 / len(factors))
        evidence = tuple(sorted({claim.fact_id} | {
            fact_id for layer in self.required_layers
            for fact_id in by_layer[layer].witness_fact_ids}))
        return drive, coverage, evidence

    def decide(self, claims: tuple[EgoClaim, ...], *, halo_limit: int = 3) -> EgoDecision:
        if claims != tuple(sorted(set(claims))) or len({item.fact_id for item in claims}) != len(claims) \
                or halo_limit < 1:
            raise ValueError("ego claims must be unique, canonical and have a halo")
        measured = tuple(sorted(((self._measure(claim), claim) for claim in claims),
                                key=lambda item: (-item[0][0], item[1].fact_id)))
        peripheral = tuple(item[1].fact_id for item in measured[:halo_limit])
        if not measured or measured[0][0][0] < self.minimum_drive:
            return EgoDecision("abstain", None, 0.0,
                               measured[1][0][0] if len(measured) > 1 else 0.0,
                               measured[0][0][1] if measured else 0.0, (), peripheral,
                               "no claim owns enough complete ability to decide")
        (drive, coverage, evidence), winner = measured[0]
        runner_up = measured[1][0][0] if len(measured) > 1 else 0.0
        margin = (drive - runner_up) / max(drive, 1e-12)
        if margin < self.minimum_margin:
            return EgoDecision("contested", None, drive, runner_up, coverage, evidence,
                               peripheral, "rival retains comparable accountable drive")
        return EgoDecision("committed", winner.fact_id, drive, runner_up, coverage,
                           evidence, tuple(item for item in peripheral if item != winner.fact_id),
                           "one complete claim accepts responsibility for closure")
