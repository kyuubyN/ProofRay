# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Materialized breathing boundary: mutations on inhale/exhale, O(candidates) readout."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from .breathing_interference import (
    BreathCertificate, InterferenceWave, ProvenancedInterferenceField,
)


@dataclass
class _Accumulator:
    base_amplitude: float = 0.0
    base_positive_count: int = 0
    base_negative_count: int = 0
    base_positive_witnesses: tuple[int, ...] = ()
    base_negative_witnesses: tuple[int, ...] = ()
    base_hard_negative_witnesses: tuple[int, ...] = ()
    base_origins: tuple[str, ...] = ()
    positive: dict[int, float] = field(default_factory=dict)
    negative: dict[int, float] = field(default_factory=dict)
    hard_negative: set[int] = field(default_factory=set)
    origins: set[str] = field(default_factory=set)

    def ingest(self, wave: InterferenceWave) -> None:
        target = self.positive if wave.amplitude > 0 else self.negative
        contribution = abs(wave.amplitude) / len(wave.evidence_fact_ids)
        for fact_id in wave.evidence_fact_ids:
            target[fact_id] = max(target.get(fact_id, 0.0), contribution)
            if wave.hard_negative:
                self.hard_negative.add(fact_id)
        self.origins.add(wave.origin)

    def retract(self, fact_id: int) -> None:
        self.positive.pop(fact_id, None)
        self.negative.pop(fact_id, None)
        self.hard_negative.discard(fact_id)

    @classmethod
    def from_boundary(cls, candidate: "BoundaryCandidate") -> "_Accumulator":
        return cls(
            base_amplitude=candidate.amplitude,
            base_positive_count=candidate.positive_witness_count,
            base_negative_count=candidate.negative_witness_count,
            base_positive_witnesses=candidate.positive_witnesses,
            base_negative_witnesses=candidate.negative_witnesses,
            base_hard_negative_witnesses=candidate.hard_negative_witnesses,
            base_origins=candidate.origins,
        )


@dataclass(frozen=True)
class BoundaryCandidate:
    canonical: str
    amplitude: float
    positive_witness_count: int
    negative_witness_count: int
    positive_witnesses: tuple[int, ...]
    negative_witnesses: tuple[int, ...]
    hard_negative_witnesses: tuple[int, ...]
    origins: tuple[str, ...]


@dataclass(frozen=True)
class InterferenceBoundary:
    scope: str
    generation: int
    seal_fact_id: int
    candidates: tuple[BoundaryCandidate, ...]
    sha256: str


@dataclass(frozen=True)
class BoundaryResolution:
    state: str
    canonical: str | None
    evidence_fact_ids: tuple[int, ...]
    reason: str
    boundary_sha256: str
    generation: int
    scanned_candidates: int


class MaterializedInterferenceField:
    """Stage evidence incrementally and publish an immutable compact read boundary."""

    def __init__(self, *, min_positive_witnesses: int = 2, min_margin: float = 0.5,
                 proof_width: int = 3):
        if min_positive_witnesses < 2 or min_margin < 0 or proof_width < 2:
            raise ValueError("invalid materialized interference gates")
        self.min_positive_witnesses = min_positive_witnesses
        self.min_margin = min_margin
        self.proof_width = proof_width
        self._staging: dict[tuple[str, str], _Accumulator] = {}
        self._fact_index: dict[tuple[str, int], set[str]] = {}
        self._boundaries: dict[str, InterferenceBoundary] = {}

    def inhale(self, scope: str, wave: InterferenceWave) -> None:
        if not scope:
            raise ValueError("scope is required")
        accumulator = self._staging.setdefault((scope, wave.canonical), _Accumulator())
        accumulator.ingest(wave)
        for fact_id in wave.evidence_fact_ids:
            self._fact_index.setdefault((scope, fact_id), set()).add(wave.canonical)

    def retract(self, scope: str, fact_id: int) -> None:
        if not scope or fact_id < 0:
            raise ValueError("scope and non-negative FactId are required")
        canonicals = self._fact_index.pop((scope, fact_id), set())
        if not canonicals:
            raise ValueError("closed-epoch fact requires an append-only compensating wave")
        for canonical in canonicals:
            self._staging[(scope, canonical)].retract(fact_id)

    def staged_fact_count(self, scope: str) -> int:
        return sum(len(accumulator.positive) + len(accumulator.negative)
                   for (item_scope, _), accumulator in self._staging.items()
                   if item_scope == scope)

    def inhale_certified_silence(self, certificate: BreathCertificate) -> None:
        for wave in ProvenancedInterferenceField.silence_waves(certificate):
            self.inhale(certificate.scope, wave)

    def exhale(self, scope: str, seal_fact_id: int) -> InterferenceBoundary:
        if not scope or seal_fact_id < 0:
            raise ValueError("scope and seal FactId are required")
        previous = self._boundaries.get(scope)
        generation = 1 if previous is None else previous.generation + 1
        candidates = []
        for (item_scope, canonical), accumulator in sorted(self._staging.items()):
            if item_scope != scope:
                continue
            positive_witnesses = tuple(sorted(set(accumulator.base_positive_witnesses) |
                                              set(accumulator.positive)))[:self.proof_width]
            negative_witnesses = tuple(sorted(set(accumulator.base_negative_witnesses) |
                                              set(accumulator.negative)))[:self.proof_width]
            hard_witnesses = tuple(sorted(set(accumulator.base_hard_negative_witnesses) |
                                          accumulator.hard_negative))[:self.proof_width]
            candidates.append(BoundaryCandidate(
                canonical=canonical,
                amplitude=(accumulator.base_amplitude + sum(accumulator.positive.values()) -
                           sum(accumulator.negative.values())),
                positive_witness_count=(accumulator.base_positive_count +
                                        len(accumulator.positive)),
                negative_witness_count=(accumulator.base_negative_count +
                                        len(accumulator.negative)),
                positive_witnesses=positive_witnesses,
                negative_witnesses=negative_witnesses,
                hard_negative_witnesses=hard_witnesses,
                origins=tuple(sorted(set(accumulator.base_origins) | accumulator.origins)),
            ))
        encoded = [{
            "canonical": item.canonical, "amplitude": item.amplitude,
            "positive_witness_count": item.positive_witness_count,
            "negative_witness_count": item.negative_witness_count,
            "positive_witnesses": item.positive_witnesses,
            "negative_witnesses": item.negative_witnesses,
            "hard_negative_witnesses": item.hard_negative_witnesses,
            "origins": item.origins,
        } for item in candidates]
        payload = {"scope": scope, "generation": generation,
                   "seal_fact_id": seal_fact_id, "candidates": encoded}
        digest = hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        boundary = InterferenceBoundary(scope, generation, seal_fact_id,
                                        tuple(candidates), digest)
        self._boundaries[scope] = boundary
        # The closed breath is replaced by its sufficient boundary statistic.  FactId
        # uniqueness remains the external authoritative Registry's responsibility; an old
        # correction is a new negative wave, never destructive history editing.
        for key in tuple(self._staging):
            if key[0] == scope:
                del self._staging[key]
        for candidate in candidates:
            self._staging[(scope, candidate.canonical)] = _Accumulator.from_boundary(candidate)
        for key in tuple(self._fact_index):
            if key[0] == scope:
                del self._fact_index[key]
        return boundary

    def resolve(self, scope: str) -> BoundaryResolution:
        boundary = self._boundaries.get(scope)
        if boundary is None:
            return BoundaryResolution("abstain", None, (), "no exhaled boundary", "", 0, 0)
        ranked = tuple(sorted(boundary.candidates,
                              key=lambda item: (-item.amplitude, item.canonical)))
        viable = tuple(item for item in ranked
                       if not item.hard_negative_witnesses
                       and item.positive_witness_count >= self.min_positive_witnesses
                       and item.amplitude > 0)
        if not viable:
            evidence = tuple(sorted({fact_id for item in ranked
                                     for fact_id in item.hard_negative_witnesses}))
            return BoundaryResolution(
                "abstain", None, evidence, "no constructive candidate after exhale",
                boundary.sha256, boundary.generation, len(ranked))
        runner_up = viable[1].amplitude if len(viable) > 1 else 0.0
        if viable[0].amplitude - runner_up < self.min_margin:
            return BoundaryResolution(
                "abstain", None, (), "materialized modes do not separate",
                boundary.sha256, boundary.generation, len(ranked))
        winner = viable[0]
        evidence = tuple(sorted(set(winner.positive_witnesses) |
                                set(winner.negative_witnesses) |
                                set(winner.hard_negative_witnesses)))
        return BoundaryResolution(
            "resolved", winner.canonical, evidence,
            "unique materialized constructive interference", boundary.sha256,
            boundary.generation, len(ranked))
