# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Causal flow regulator: match observable challenge to verified skill without self-belief."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class ObservableChallenge:
    query_id: str
    issued_at: float
    deadline: float
    uncertainty: float
    novelty: float
    slot_pressure: float
    coordination_pressure: float

    def __post_init__(self) -> None:
        values = (self.uncertainty, self.novelty, self.slot_pressure,
                  self.coordination_pressure)
        if not self.query_id or self.issued_at < 0 or self.deadline < self.issued_at \
                or any(not 0 <= value <= 1 for value in values):
            raise ValueError("invalid observable challenge")


@dataclass(frozen=True, order=True)
class VerifiedSkillState:
    lineage_id: str
    niche: str
    reliability_lower_bound: float
    temporal_stability: float
    cost_efficiency: float
    verified_at: float
    verifier_fact_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        values = (self.reliability_lower_bound, self.temporal_stability,
                  self.cost_efficiency)
        if not self.lineage_id or not self.niche or any(not 0 <= value <= 1 for value in values) \
                or self.verified_at < 0 or not self.verifier_fact_ids \
                or self.verifier_fact_ids != tuple(sorted(set(self.verifier_fact_ids))):
            raise ValueError("invalid verified skill state")


@dataclass(frozen=True, order=True)
class FlowPulse:
    step: int
    observed_at: float
    slot_coverage: float
    prediction_error: float
    compute_fraction: float
    evidence_fact_ids: tuple[int, ...]
    hard_contradiction: bool = False

    def __post_init__(self) -> None:
        if self.step < 0 or self.observed_at < 0 \
                or any(not 0 <= value <= 1 for value in (
                    self.slot_coverage, self.prediction_error, self.compute_fraction)) \
                or self.evidence_fact_ids != tuple(sorted(set(self.evidence_fact_ids))):
            raise ValueError("invalid flow feedback pulse")


@dataclass(frozen=True)
class FlowRegime:
    state: str
    action: str
    challenge: float
    skill: float
    resonance: float
    core_width: int
    maximum_recruits: int
    evidence_fact_ids: tuple[int, ...]
    reason: str


class CausalFlowRegulator:
    """Fast structural feedback chooses solo, recruitment, cheap delegation or silence.

    Flow changes compute routing only. It cannot add evidence, update verified skill during
    a decision, or turn smooth execution into correctness.
    """

    def __init__(self, *, resonance_band: float = .12, closure_error: float = .2,
                 flow_core_width: int = 4, maximum_recruits: int = 3):
        if resonance_band < 0 or not 0 <= closure_error <= 1 \
                or flow_core_width < 1 or not 1 <= maximum_recruits <= 3:
            raise ValueError("invalid flow regulation boundary")
        self.resonance_band = resonance_band
        self.closure_error = closure_error
        self.flow_core_width = flow_core_width
        self.maximum_recruits = maximum_recruits

    @staticmethod
    def _challenge(item: ObservableChallenge) -> float:
        values = (item.uncertainty, item.novelty, item.slot_pressure,
                  item.coordination_pressure)
        return math.sqrt(sum(value * value for value in values) / len(values))

    @staticmethod
    def _skill(item: VerifiedSkillState) -> float:
        values = (max(item.reliability_lower_bound, 1e-12),
                  max(item.temporal_stability, 1e-12),
                  max(item.cost_efficiency, 1e-12))
        return math.prod(values) ** (1 / len(values))

    def regulate(self, challenge: ObservableChallenge, skill: VerifiedSkillState,
                 pulses: tuple[FlowPulse, ...]) -> FlowRegime:
        if skill.verified_at >= challenge.issued_at:
            raise ValueError("current-query feedback cannot rewrite verified skill")
        if not pulses or pulses != tuple(sorted(set(pulses))) \
                or tuple(pulse.step for pulse in pulses) != tuple(range(len(pulses))) \
                or any(not challenge.issued_at <= pulse.observed_at <= challenge.deadline
                       for pulse in pulses):
            raise ValueError("flow feedback must be a canonical in-decision sequence")
        evidence = tuple(sorted({fact_id for pulse in pulses for fact_id in pulse.evidence_fact_ids}))
        difficulty, capacity = self._challenge(challenge), self._skill(skill)
        resonance = 1 - abs(difficulty - capacity)
        contradiction = any(pulse.hard_contradiction for pulse in pulses)
        coverage_regressed = any(right.slot_coverage < left.slot_coverage
                                 for left, right in zip(pulses, pulses[1:]))
        error_grew = any(right.prediction_error > left.prediction_error
                         for left, right in zip(pulses, pulses[1:]))
        if contradiction:
            return FlowRegime("rupture", "abstain", difficulty, capacity, resonance,
                              1, 0, evidence, "hard contradiction breaks the trajectory")
        if coverage_regressed or error_grew:
            return FlowRegime("rupture", "recruit_or_abstain", difficulty, capacity,
                              resonance, 1, self.maximum_recruits, evidence,
                              "structural feedback stopped converging")
        if difficulty < capacity - self.resonance_band:
            return FlowRegime("boredom", "delegate_cheaper", difficulty, capacity,
                              resonance, 1, 0, evidence,
                              "verified capacity materially exceeds observable challenge")
        if difficulty > capacity + self.resonance_band:
            return FlowRegime("anxiety", "recruit_specialists", difficulty, capacity,
                              resonance, 1, self.maximum_recruits, evidence,
                              "observable challenge exceeds verified solo capacity")
        final = pulses[-1]
        if final.slot_coverage == 1 and final.prediction_error <= self.closure_error:
            action, reason = "commit_solo", "matched trajectory closed every proof slot"
        else:
            action, reason = "continue_solo", "matched trajectory is converging but incomplete"
        return FlowRegime("flow", action, difficulty, capacity, resonance,
                          self.flow_core_width, 0, evidence, reason)
