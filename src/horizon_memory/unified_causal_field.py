# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""HUCF: one signed proof-hypergraph ingress and one materialized readout law."""
from __future__ import annotations

from dataclasses import dataclass

from .breathing_interference import (
    BreathCertificate, BreathingLedger, ChannelExpectation, InterferenceWave, ObservedPulse,
)
from .materialized_interference import (
    BoundaryResolution, InterferenceBoundary, MaterializedInterferenceField,
)


@dataclass(frozen=True, order=True)
class ProofHyperedge:
    scope: str
    canonical: str
    channels: tuple[str, ...]
    evidence_fact_ids: tuple[int, ...]
    observed_at: int
    amplitude: float
    origin: str
    hard_negative: bool = False

    def __post_init__(self) -> None:
        if not self.scope or not self.canonical or not self.origin or self.observed_at < 0:
            raise ValueError("hyperedge requires scope, canonical, origin and causal clock")
        if self.channels != tuple(sorted(set(self.channels))) or len(self.channels) < 2:
            raise ValueError("proof hyperedge requires at least two unique sorted channels")
        if self.evidence_fact_ids != tuple(sorted(set(self.evidence_fact_ids))) \
                or not self.evidence_fact_ids or any(value < 0 for value in self.evidence_fact_ids):
            raise ValueError("proof hyperedge requires canonical FactIds")
        if self.amplitude == 0 or (self.hard_negative and self.amplitude >= 0):
            raise ValueError("invalid signed amplitude")


@dataclass(frozen=True)
class UnifiedExhale:
    breath: BreathCertificate
    boundary: InterferenceBoundary


class HorizonUnifiedCausalField:
    """All mechanisms translate to signed hyperedges; none receives an independent vote."""

    def __init__(self, *, min_positive_witnesses: int = 2,
                 min_margin: float = 0.5, proof_width: int = 3):
        self._field = MaterializedInterferenceField(
            min_positive_witnesses=min_positive_witnesses,
            min_margin=min_margin, proof_width=proof_width)
        self._breaths: dict[str, BreathingLedger] = {}

    def begin_breath(self, scope: str, epoch: int,
                     expectations: tuple[ChannelExpectation, ...] = ()) -> None:
        if scope in self._breaths:
            raise ValueError("scope already has an open breath")
        self._breaths[scope] = BreathingLedger(scope, epoch, expectations)

    def inhale(self, edge: ProofHyperedge) -> None:
        breath = self._breaths.get(edge.scope)
        if breath is None:
            raise ValueError("begin_breath is required before hyperedge ingress")
        # Channels are observations of one indivisible event.  The first FactId is the
        # event authority; all FactIds still bind the signed proof amplitude.
        for channel in edge.channels:
            breath.inhale(ObservedPulse(channel, edge.evidence_fact_ids[0], edge.observed_at))
        self._field.inhale(edge.scope, InterferenceWave(
            edge.canonical, edge.amplitude, edge.evidence_fact_ids,
            edge.origin, hard_negative=edge.hard_negative))

    def exhale(self, scope: str, seal_fact_id: int) -> UnifiedExhale:
        breath = self._breaths.pop(scope, None)
        if breath is None:
            raise ValueError("scope has no open breath")
        certificate = breath.exhale(seal_fact_id)
        self._field.inhale_certified_silence(certificate)
        return UnifiedExhale(certificate, self._field.exhale(scope, seal_fact_id))

    def resolve(self, scope: str) -> BoundaryResolution:
        return self._field.resolve(scope)

    def staged_fact_count(self, scope: str) -> int:
        return self._field.staged_fact_count(scope)
