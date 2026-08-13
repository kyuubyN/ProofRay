# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verified evolutionary reward for useful originality, never self-confidence."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Competitor:
    competitor_id: str
    structural_channels: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.competitor_id or not self.structural_channels \
                or self.structural_channels != tuple(sorted(set(self.structural_channels))):
            raise ValueError("invalid evolutionary competitor")


@dataclass(frozen=True, order=True)
class VerifiedOutcome:
    query_id: str
    competitor_id: str
    state: str  # correct | wrong | abstain
    should_answer: bool
    impact: float
    compute_cost: float
    context_cost: float
    decided_at: float
    verified_at: float
    verifier_fact_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.query_id or not self.competitor_id \
                or self.state not in ("correct", "wrong", "abstain") \
                or self.impact < 0 or self.compute_cost < 0 or self.context_cost < 0 \
                or self.decided_at < 0 or self.verified_at <= self.decided_at \
                or not self.verifier_fact_ids \
                or self.verifier_fact_ids != tuple(sorted(set(self.verifier_fact_ids))):
            raise ValueError("reward requires a delayed external verification proof")


@dataclass(frozen=True)
class EvolutionScore:
    competitor_id: str
    fitness: float
    verified_correct: int
    false_accepts: int
    honest_abstentions: int
    missed_opportunities: int
    unique_solves: int
    first_solves: int
    originality_credit: float
    metabolic_cost: float
    weight: float


@dataclass(frozen=True)
class EvolutionGeneration:
    champion_id: str
    scores: tuple[EvolutionScore, ...]
    residual_query_ids: tuple[str, ...]
    promoted_specialists: tuple[str, ...]


class VerifiedEvolution:
    """Reward marginal verified contribution and preserve residual specialists.

    Novel structure has no value by itself.  Originality credit is released only for a
    correct answer on a query no rival solved.  Replicator weights express future resource
    allocation; they never rewrite the verification ledger or turn fitness into truth.
    """

    def __init__(self, *, correct_reward: float = 1.0, originality_reward: float = 2.0,
                 wrong_answer_penalty: float = 1.0, false_accept_penalty: float = 3.0,
                 missed_opportunity_penalty: float = 1.0,
                 cost_rate: float = .01, temperature: float = 1.0):
        if min(correct_reward, originality_reward, wrong_answer_penalty, false_accept_penalty,
               missed_opportunity_penalty, cost_rate) < 0 or temperature <= 0:
            raise ValueError("invalid evolutionary reward law")
        self.correct_reward = correct_reward
        self.originality_reward = originality_reward
        self.wrong_answer_penalty = wrong_answer_penalty
        self.false_accept_penalty = false_accept_penalty
        self.missed_opportunity_penalty = missed_opportunity_penalty
        self.cost_rate = cost_rate
        self.temperature = temperature

    def evaluate(self, competitors: tuple[Competitor, ...],
                 outcomes: tuple[VerifiedOutcome, ...]) -> EvolutionGeneration:
        if competitors != tuple(sorted(set(competitors))) or not competitors \
                or len({item.competitor_id for item in competitors}) != len(competitors) \
                or outcomes != tuple(sorted(set(outcomes))):
            raise ValueError("competitors and verified outcomes must be canonical")
        ids = {item.competitor_id for item in competitors}
        if any(item.competitor_id not in ids for item in outcomes):
            raise ValueError("outcome references an unknown competitor")
        by_query = {}
        by_competitor = {item.competitor_id: [] for item in competitors}
        for outcome in outcomes:
            by_query.setdefault(outcome.query_id, []).append(outcome)
            by_competitor[outcome.competitor_id].append(outcome)
        unique = {item.competitor_id: set() for item in competitors}
        first = {item.competitor_id: set() for item in competitors}
        residual = []
        for query_id, rows in by_query.items():
            correct = [row for row in rows if row.should_answer and row.state == "correct"]
            if len(correct) == 1:
                unique[correct[0].competitor_id].add(query_id)
            if correct:
                first_verified_at = min(row.verified_at for row in correct)
                first_rows = [row for row in correct if row.verified_at == first_verified_at]
                # A simultaneous result has no defensible author: strict chronology is
                # the verifier's proof that a solution was actually first.
                if len(first_rows) == 1:
                    first[first_rows[0].competitor_id].add(query_id)
            if not correct and any(row.should_answer for row in rows):
                residual.append(query_id)

        raw = []
        for competitor in competitors:
            rows = by_competitor[competitor.competitor_id]
            correct = sum(row.state == "correct" and row.should_answer for row in rows)
            false = sum(row.state == "wrong" for row in rows)
            honest = sum(row.state == "abstain" and not row.should_answer for row in rows)
            missed = sum(row.state == "abstain" and row.should_answer for row in rows)
            unique_rows = unique[competitor.competitor_id]
            first_rows = first[competitor.competitor_id]
            # Structural novelty matters only after unique verified utility exists.
            rivals = [set(item.structural_channels) for item in competitors
                      if item.competitor_id != competitor.competitor_id]
            own = set(competitor.structural_channels)
            nearest_similarity = max((len(own & rival) / len(own | rival)
                                      for rival in rivals), default=0.0)
            structural_novelty = 1.0 - nearest_similarity
            # First verified discovery is immutable. A later correct copy gains utility,
            # never retrospective originality.
            originality = sum(next(row.impact for row in rows if row.query_id == query_id)
                              for query_id in first_rows) * structural_novelty
            metabolic = sum(row.compute_cost + row.context_cost for row in rows)
            fitness = (self.correct_reward * sum(row.impact for row in rows
                                                  if row.state == "correct" and row.should_answer)
                       + self.originality_reward * originality
                       - self.wrong_answer_penalty * sum(row.impact for row in rows
                                                        if row.state == "wrong" and
                                                        row.should_answer)
                       - self.false_accept_penalty * sum(row.impact for row in rows
                                                         if row.state == "wrong" and
                                                         not row.should_answer)
                       - self.missed_opportunity_penalty * sum(row.impact for row in rows
                                                               if row.state == "abstain" and
                                                               row.should_answer)
                       - self.cost_rate * metabolic)
            raw.append((competitor, fitness, correct, false, honest, missed,
                        len(unique_rows), len(first_rows), originality, metabolic))
        maximum = max(item[1] for item in raw)
        exponentials = [math.exp((item[1] - maximum) / self.temperature) for item in raw]
        denominator = sum(exponentials)
        scores = tuple(sorted((EvolutionScore(
            item[0].competitor_id, item[1], item[2], item[3], item[4], item[5], item[6],
            item[7], item[8], item[9], exponential / denominator)
            for item, exponential in zip(raw, exponentials)),
            key=lambda score: (-score.fitness, score.competitor_id)))
        specialists = tuple(sorted(score.competitor_id for score in scores
                                   if score.unique_solves > 0 or score.first_solves > 0))
        return EvolutionGeneration(scores[0].competitor_id, scores,
                                   tuple(sorted(residual)), specialists)
