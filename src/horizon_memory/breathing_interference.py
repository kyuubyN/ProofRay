# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Event-driven breathing, certified silence and provenanced interference."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class ChannelExpectation:
    canonical: str
    channel: str
    evidence_fact_id: int

    def __post_init__(self) -> None:
        if not self.canonical or not self.channel or self.evidence_fact_id < 0:
            raise ValueError("expectation requires canonical, channel and FactId")


@dataclass(frozen=True, order=True)
class ObservedPulse:
    channel: str
    fact_id: int
    observed_at: int

    def __post_init__(self) -> None:
        if not self.channel or self.fact_id < 0 or self.observed_at < 0:
            raise ValueError("pulse requires channel, FactId and causal clock")


@dataclass(frozen=True)
class SilenceEvidence:
    canonical: str
    channel: str
    expectation_fact_id: int
    seal_fact_id: int


@dataclass(frozen=True)
class BreathCertificate:
    scope: str
    epoch: int
    observed_channels: tuple[str, ...]
    observed_fact_ids: tuple[int, ...]
    silence: tuple[SilenceEvidence, ...]
    seal_fact_id: int
    sha256: str


class BreathingLedger:
    """One open causal breath; only exhale can certify non-observation."""

    def __init__(self, scope: str, epoch: int,
                 expectations: tuple[ChannelExpectation, ...]):
        if not scope or epoch < 0 or expectations != tuple(sorted(set(expectations))):
            raise ValueError("scope, epoch and canonical expectations are required")
        self.scope = scope
        self.epoch = epoch
        self.expectations = expectations
        self._pulses: set[ObservedPulse] = set()
        self._sealed: BreathCertificate | None = None

    def inhale(self, pulse: ObservedPulse) -> None:
        if self._sealed is not None:
            raise ValueError("cannot inhale into an exhaled epoch")
        self._pulses.add(pulse)

    def silence_before_exhale(self) -> tuple[SilenceEvidence, ...]:
        return ()

    def exhale(self, seal_fact_id: int) -> BreathCertificate:
        if seal_fact_id < 0:
            raise ValueError("seal FactId must be non-negative")
        if self._sealed is not None:
            if self._sealed.seal_fact_id != seal_fact_id:
                raise ValueError("epoch was already sealed by another authority")
            return self._sealed
        observed = tuple(sorted({pulse.channel for pulse in self._pulses}))
        observed_ids = tuple(sorted({pulse.fact_id for pulse in self._pulses}))
        silence = tuple(SilenceEvidence(item.canonical, item.channel,
                                        item.evidence_fact_id, seal_fact_id)
                        for item in self.expectations if item.channel not in observed)
        payload = {
            "scope": self.scope, "epoch": self.epoch, "observed_channels": observed,
            "observed_fact_ids": observed_ids,
            "silence": [(item.canonical, item.channel, item.expectation_fact_id,
                         item.seal_fact_id) for item in silence],
            "seal_fact_id": seal_fact_id,
        }
        digest = hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        self._sealed = BreathCertificate(self.scope, self.epoch, observed, observed_ids,
                                         silence, seal_fact_id, digest)
        return self._sealed


@dataclass(frozen=True, order=True)
class InterferenceWave:
    canonical: str
    amplitude: float
    evidence_fact_ids: tuple[int, ...]
    origin: str
    hard_negative: bool = False

    def __post_init__(self) -> None:
        if not self.canonical or not self.origin or self.amplitude == 0 \
                or not self.evidence_fact_ids:
            raise ValueError("wave requires candidate, nonzero amplitude, origin and proof")
        if self.evidence_fact_ids != tuple(sorted(set(self.evidence_fact_ids))) \
                or any(fact_id < 0 for fact_id in self.evidence_fact_ids):
            raise ValueError("wave FactIds must be unique, sorted and non-negative")
        if self.hard_negative and self.amplitude >= 0:
            raise ValueError("hard negative must have negative amplitude")


@dataclass(frozen=True)
class InterferenceCandidate:
    canonical: str
    amplitude: float
    positive_fact_ids: tuple[int, ...]
    negative_fact_ids: tuple[int, ...]
    origins: tuple[str, ...]
    excluded: bool


@dataclass(frozen=True)
class InterferenceResolution:
    state: str
    canonical: str | None
    evidence_fact_ids: tuple[int, ...]
    reason: str
    candidates: tuple[InterferenceCandidate, ...]


class ProvenancedInterferenceField:
    """Signed evidence with FactId deduplication and constraint dominance."""

    def __init__(self, *, min_positive_witnesses: int = 2, min_margin: float = 0.5):
        if min_positive_witnesses < 2 or min_margin < 0:
            raise ValueError("invalid interference gates")
        self.min_positive_witnesses = min_positive_witnesses
        self.min_margin = min_margin

    @staticmethod
    def silence_waves(certificate: BreathCertificate) -> tuple[InterferenceWave, ...]:
        return tuple(InterferenceWave(
            item.canonical, -1.0,
            tuple(sorted((item.expectation_fact_id, item.seal_fact_id))),
            f"certified_silence:{item.channel}", hard_negative=True,
        ) for item in certificate.silence)

    def resolve(self, waves: tuple[InterferenceWave, ...]) -> InterferenceResolution:
        if waves != tuple(sorted(set(waves))):
            raise ValueError("waves must be unique and canonically sorted")
        candidates = []
        for canonical in sorted({wave.canonical for wave in waves}):
            group = tuple(wave for wave in waves if wave.canonical == canonical)
            excluded = any(wave.hard_negative for wave in group)
            positive: dict[int, float] = {}
            negative: dict[int, float] = {}
            for wave in group:
                target = positive if wave.amplitude > 0 else negative
                per_fact = abs(wave.amplitude) / len(wave.evidence_fact_ids)
                for fact_id in wave.evidence_fact_ids:
                    target[fact_id] = max(target.get(fact_id, 0.0), per_fact)
            amplitude = sum(positive.values()) - sum(negative.values())
            candidates.append(InterferenceCandidate(
                canonical, amplitude, tuple(sorted(positive)), tuple(sorted(negative)),
                tuple(sorted({wave.origin for wave in group})), excluded,
            ))
        ranked = tuple(sorted(candidates, key=lambda item: (-item.amplitude, item.canonical)))
        viable = tuple(item for item in ranked if not item.excluded
                       and len(item.positive_fact_ids) >= self.min_positive_witnesses
                       and item.amplitude > 0)
        if not viable:
            evidence = tuple(sorted({fact_id for item in ranked for fact_id in item.negative_fact_ids}))
            return InterferenceResolution("abstain", None, evidence,
                                          "silence, exclusion or destructive interference", ranked)
        runner_up = viable[1].amplitude if len(viable) > 1 else 0.0
        if viable[0].amplitude - runner_up < self.min_margin:
            return InterferenceResolution("abstain", None, (),
                                          "constructive modes do not separate", ranked)
        winner = viable[0]
        return InterferenceResolution(
            "resolved", winner.canonical,
            tuple(sorted(set(winner.positive_fact_ids) | set(winner.negative_fact_ids))),
            "unique constructive interference after certified silence", ranked)
