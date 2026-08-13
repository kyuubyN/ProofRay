# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Telic observer ego: one declared end attracts a minimal proof from many pieces."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class DecisionIntent:
    goal_id: str
    scope: str
    subject: str
    predicate: str
    issued_at: float
    deadline: float
    required_slots: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.goal_id or not self.scope or not self.subject or not self.predicate \
                or self.issued_at < 0 or self.deadline < self.issued_at \
                or not self.required_slots \
                or self.required_slots != tuple(sorted(set(self.required_slots))):
            raise ValueError("invalid telic decision intent")


@dataclass(frozen=True, order=True)
class PreparedGoalCandidate:
    candidate_id: str
    hypothesis: str
    prepared_at: float
    prior_probability: float
    impact: float

    def __post_init__(self) -> None:
        if not self.candidate_id or not self.hypothesis or self.prepared_at < 0 \
                or not 0 <= self.prior_probability <= 1 or self.impact < 0:
            raise ValueError("invalid prepared goal candidate")


@dataclass(frozen=True, order=True)
class TelicPuzzlePiece:
    fact_id: int
    candidate_id: str
    scope: str
    subject: str
    predicate: str
    slot: str
    strength: float
    observed_at: float
    expires_at: float
    hard_negative: bool = False

    def __post_init__(self) -> None:
        if self.fact_id < 0 or not self.candidate_id or not self.scope or not self.subject \
                or not self.predicate or not self.slot or not 0 <= self.strength <= 1 \
                or self.observed_at < 0 or self.expires_at < self.observed_at:
            raise ValueError("invalid telic puzzle piece")


@dataclass(frozen=True)
class TelicClosure:
    state: str
    candidate_id: str | None
    hypothesis: str | None
    drive: float
    coverage: float
    evidence_fact_ids: tuple[int, ...]
    missing_slots: tuple[str, ...]
    alternatives: tuple[str, ...]
    reason: str


class TelicObserverEgo:
    """A goal decides what must be closed; pieces collaborate without losing identity."""

    def __init__(self, *, maximum_candidates: int = 3, minimum_margin: float = .05,
                 minimum_drive: float = .05):
        if maximum_candidates < 1 or minimum_margin < 0 or minimum_drive < 0:
            raise ValueError("invalid telic ego boundary")
        self.maximum_candidates = maximum_candidates
        self.minimum_margin = minimum_margin
        self.minimum_drive = minimum_drive

    @staticmethod
    def _measure(intent: DecisionIntent, candidate: PreparedGoalCandidate,
                 pieces: tuple[TelicPuzzlePiece, ...]):
        if not candidate.prepared_at < intent.issued_at:
            return 0.0, 0.0, (), intent.required_slots, "candidate was not prepared ex ante"
        relevant = tuple(piece for piece in pieces
                         if piece.candidate_id == candidate.candidate_id
                         and piece.scope == intent.scope and piece.subject == intent.subject
                         and piece.predicate == intent.predicate
                         and piece.observed_at <= intent.issued_at <= piece.expires_at)
        if any(piece.hard_negative for piece in relevant):
            evidence = tuple(sorted(piece.fact_id for piece in relevant if piece.hard_negative))
            return 0.0, 0.0, evidence, intent.required_slots, "hard repulsion reached goal"
        by_slot = {}
        for piece in relevant:
            previous = by_slot.get(piece.slot)
            if previous is None or (piece.strength, -piece.fact_id) > \
                    (previous.strength, -previous.fact_id):
                by_slot[piece.slot] = piece
        missing = tuple(slot for slot in intent.required_slots
                        if slot not in by_slot or by_slot[slot].strength <= 0)
        coverage = 1 - len(missing) / len(intent.required_slots)
        if missing:
            return 0.0, coverage, tuple(sorted(piece.fact_id for piece in by_slot.values())), \
                missing, "goal remains open"
        factors = (max(candidate.prior_probability, 1e-12),
                   max(min(1.0, candidate.impact), 1e-12),
                   *(max(by_slot[slot].strength, 1e-12) for slot in intent.required_slots))
        drive = math.prod(factors) ** (1 / len(factors))
        evidence = tuple(sorted({by_slot[slot].fact_id for slot in intent.required_slots}))
        return drive, coverage, evidence, (), "complete route"

    def close(self, intent: DecisionIntent, candidates: tuple[PreparedGoalCandidate, ...],
              pieces: tuple[TelicPuzzlePiece, ...]) -> TelicClosure:
        if candidates != tuple(sorted(set(candidates))) or not candidates \
                or len(candidates) > self.maximum_candidates \
                or len({item.candidate_id for item in candidates}) != len(candidates) \
                or pieces != tuple(sorted(set(pieces))):
            raise ValueError("telic candidates/pieces must be bounded, unique and canonical")
        measured = tuple(sorted(((self._measure(intent, candidate, pieces), candidate)
                                 for candidate in candidates),
                                key=lambda item: (-item[0][0], item[1].candidate_id)))
        alternatives = tuple(item[1].candidate_id for item in measured)
        best_measure, best = measured[0]
        drive, coverage, evidence, missing, reason = best_measure
        if drive < self.minimum_drive:
            return TelicClosure("abstain", None, None, drive, coverage, evidence, missing,
                                alternatives, reason)
        runner_up = measured[1][0][0] if len(measured) > 1 else 0.0
        if (drive - runner_up) / max(drive, 1e-12) < self.minimum_margin:
            return TelicClosure("contested", None, None, drive, coverage, evidence, (),
                                alternatives, "two complete futures remain plausible")
        return TelicClosure("committed", best.candidate_id, best.hypothesis, drive, coverage,
                            evidence, (), tuple(item for item in alternatives
                                                if item != best.candidate_id),
                            "goal attracted a complete accountable proof")
