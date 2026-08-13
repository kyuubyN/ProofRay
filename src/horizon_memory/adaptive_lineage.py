# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verified survival pressure, solo glory and bounded between-generation adaptation."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class CompetitiveLineage:
    lineage_id: str
    ability: str
    generation: int
    parent_id: str | None
    inherited_dimensions: tuple[str, ...]
    compute_budget: float

    def __post_init__(self) -> None:
        if not self.lineage_id or not self.ability or self.generation < 0 \
                or self.inherited_dimensions != tuple(sorted(set(self.inherited_dimensions))) \
                or not self.inherited_dimensions or self.compute_budget <= 0 \
                or (self.generation == 0) != (self.parent_id is None):
            raise ValueError("invalid competitive lineage")


@dataclass(frozen=True, order=True)
class LineageOutcome:
    query_id: str
    lineage_id: str
    phase: str  # solo | composite
    state: str  # correct | wrong | abstain | withheld
    should_answer: bool
    impact: float
    compute_cost: float
    decided_at: float
    verified_at: float
    verifier_fact_ids: tuple[int, ...]
    published_fact_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.query_id or not self.lineage_id or self.phase not in ("solo", "composite") \
                or self.state not in ("correct", "wrong", "abstain", "withheld") \
                or self.impact < 0 or self.compute_cost < 0 or self.decided_at < 0 \
                or self.verified_at <= self.decided_at or not self.verifier_fact_ids \
                or self.verifier_fact_ids != tuple(sorted(set(self.verifier_fact_ids))) \
                or self.published_fact_ids != tuple(sorted(set(self.published_fact_ids))) \
                or (self.state in ("correct", "wrong") and not self.published_fact_ids) \
                or (self.state in ("abstain", "withheld") and self.published_fact_ids) \
                or (self.state == "correct" and not self.should_answer):
            raise ValueError("invalid delayed lineage outcome")


@dataclass(frozen=True, order=True)
class MutationChallenge:
    challenge_id: str
    parent_id: str
    ability: str
    generation: int
    changed_dimensions: tuple[str, ...]
    requested_compute_budget: float
    proposed_at: float

    def __post_init__(self) -> None:
        if not self.challenge_id or not self.parent_id or not self.ability \
                or self.generation < 1 or not self.changed_dimensions \
                or self.changed_dimensions != tuple(sorted(set(self.changed_dimensions))) \
                or len(self.changed_dimensions) > 2 or self.requested_compute_budget <= 0 \
                or self.proposed_at < 0:
            raise ValueError("invalid bounded mutation challenge")


@dataclass(frozen=True)
class LineageScore:
    lineage_id: str
    ability: str
    fitness: float
    solo_correct: int
    composite_assists: int
    first_verified_glories: int
    false_accepts: int
    rival_losses: int
    withheld: int
    allocation: float
    extinction_risk: float
    status: str


@dataclass(frozen=True)
class SurvivalGeneration:
    champions: tuple[tuple[str, str], ...]
    scores: tuple[LineageScore, ...]
    accepted_challenges: tuple[str, ...]
    residual_query_ids: tuple[str, ...]


class VerifiedSurvivalPressure:
    """Competitors seek solo verified glory but cannot profit from secrecy.

    Outcomes are immutable within a generation. Adaptation is admitted only after every
    outcome used by selection has been externally verified, and takes effect in the next
    generation. The pressure changes compute allocation, never truth or provenance.
    """

    def __init__(self, *, solo_reward: float = 1.5, composite_reward: float = .35,
                 first_glory_reward: float = 2.0, wrong_penalty: float = 1.0,
                 false_accept_penalty: float = 3.0, rival_loss_penalty: float = .5,
                 withholding_penalty: float = 4.0, missed_penalty: float = .75,
                 compute_rate: float = .01, survivors_per_ability: int = 2,
                 temperature: float = 1.0):
        values = (solo_reward, composite_reward, first_glory_reward, wrong_penalty,
                  false_accept_penalty, rival_loss_penalty, withholding_penalty,
                  missed_penalty, compute_rate)
        if min(values) < 0 or survivors_per_ability < 1 or temperature <= 0:
            raise ValueError("invalid survival pressure law")
        self.solo_reward = solo_reward
        self.composite_reward = composite_reward
        self.first_glory_reward = first_glory_reward
        self.wrong_penalty = wrong_penalty
        self.false_accept_penalty = false_accept_penalty
        self.rival_loss_penalty = rival_loss_penalty
        self.withholding_penalty = withholding_penalty
        self.missed_penalty = missed_penalty
        self.compute_rate = compute_rate
        self.survivors_per_ability = survivors_per_ability
        self.temperature = temperature

    def evaluate(self, lineages: tuple[CompetitiveLineage, ...],
                 outcomes: tuple[LineageOutcome, ...],
                 challenges: tuple[MutationChallenge, ...] = ()) -> SurvivalGeneration:
        if not lineages or lineages != tuple(sorted(set(lineages))) \
                or outcomes != tuple(sorted(set(outcomes))) \
                or challenges != tuple(sorted(set(challenges))):
            raise ValueError("lineages, outcomes and challenges must be canonical")
        by_id = {lineage.lineage_id: lineage for lineage in lineages}
        if len(by_id) != len(lineages) or any(row.lineage_id not in by_id for row in outcomes):
            raise ValueError("unknown or duplicate lineage")
        generations = {lineage.generation for lineage in lineages}
        if len(generations) != 1:
            raise ValueError("one generation must fight at a time")
        generation = next(iter(generations))
        by_ability = {}
        for lineage in lineages:
            by_ability.setdefault(lineage.ability, []).append(lineage)
        outcome_map = {(row.query_id, row.lineage_id, row.phase): row for row in outcomes}
        if len(outcome_map) != len(outcomes):
            raise ValueError("duplicate lineage outcome")

        glory = {lineage.lineage_id: set() for lineage in lineages}
        rival_losses = {lineage.lineage_id: set() for lineage in lineages}
        residual = set()
        for ability, ability_lineages in by_ability.items():
            query_ids = sorted({row.query_id for row in outcomes
                                if by_id[row.lineage_id].ability == ability})
            for query_id in query_ids:
                solos = [outcome_map.get((query_id, lineage.lineage_id, "solo"))
                         for lineage in ability_lineages]
                if any(row is None for row in solos):
                    raise ValueError("every lineage must publish a solo decision per niche query")
                correct = [row for row in solos if row.state == "correct"]
                if correct:
                    first_time = min(row.verified_at for row in correct)
                    first = [row for row in correct if row.verified_at == first_time]
                    if len(first) == 1:
                        glory[first[0].lineage_id].add(query_id)
                    for row in solos:
                        if row.state != "correct":
                            rival_losses[row.lineage_id].add(query_id)
                elif any(row.should_answer for row in solos):
                    composite = [row for row in outcomes if row.query_id == query_id
                                 and by_id[row.lineage_id].ability == ability
                                 and row.phase == "composite" and row.state == "correct"]
                    if not composite:
                        residual.add(query_id)

        raw_scores = {}
        counters = {}
        for lineage in lineages:
            rows = [row for row in outcomes if row.lineage_id == lineage.lineage_id]
            solo_correct = sum(row.phase == "solo" and row.state == "correct" for row in rows)
            assists = sum(row.phase == "composite" and row.state == "correct" for row in rows)
            false_accepts = sum(row.state == "wrong" and not row.should_answer for row in rows)
            wrong = sum(row.state == "wrong" and row.should_answer for row in rows)
            missed = sum(row.state == "abstain" and row.should_answer for row in rows)
            withheld = sum(row.state == "withheld" for row in rows)
            weighted = lambda predicate: sum(row.impact for row in rows if predicate(row))
            fitness = (
                self.solo_reward * weighted(lambda row: row.phase == "solo"
                                             and row.state == "correct")
                + self.composite_reward * weighted(lambda row: row.phase == "composite"
                                                    and row.state == "correct")
                + self.first_glory_reward * len(glory[lineage.lineage_id])
                - self.wrong_penalty * weighted(lambda row: row.state == "wrong"
                                                 and row.should_answer)
                - self.false_accept_penalty * weighted(lambda row: row.state == "wrong"
                                                        and not row.should_answer)
                - self.missed_penalty * weighted(lambda row: row.state == "abstain"
                                                  and row.should_answer)
                - self.withholding_penalty * weighted(lambda row: row.state == "withheld")
                - self.rival_loss_penalty * len(rival_losses[lineage.lineage_id])
                - self.compute_rate * sum(row.compute_cost for row in rows))
            raw_scores[lineage.lineage_id] = fitness
            counters[lineage.lineage_id] = (solo_correct, assists, len(glory[lineage.lineage_id]),
                                             false_accepts, len(rival_losses[lineage.lineage_id]),
                                             withheld)

        statuses, champions = {}, []
        for ability, ability_lineages in by_ability.items():
            ranked = sorted(ability_lineages,
                            key=lambda item: (-raw_scores[item.lineage_id], item.lineage_id))
            champions.append((ability, ranked[0].lineage_id))
            for index, lineage in enumerate(ranked):
                statuses[lineage.lineage_id] = ("champion" if index == 0 else
                                                "survivor" if index < self.survivors_per_ability
                                                else "eliminated")

        scores = []
        for ability, ability_lineages in by_ability.items():
            maximum = max(raw_scores[lineage.lineage_id] for lineage in ability_lineages)
            exponentials = {lineage.lineage_id:
                            math.exp((raw_scores[lineage.lineage_id] - maximum) / self.temperature)
                            for lineage in ability_lineages}
            denominator = sum(exponentials.values())
            for lineage in ability_lineages:
                fitness = raw_scores[lineage.lineage_id]
                difference = (maximum - fitness) / self.temperature
                risk = 0.0 if difference == 0 else 1 / (1 + math.exp(-min(difference, 700)))
                counts = counters[lineage.lineage_id]
                scores.append(LineageScore(
                    lineage.lineage_id, ability, fitness, *counts,
                    exponentials[lineage.lineage_id] / denominator, risk,
                    statuses[lineage.lineage_id]))

        last_verification = max((row.verified_at for row in outcomes), default=-1)
        accepted = []
        by_challenge_ability = {}
        for challenge in challenges:
            parent = by_id.get(challenge.parent_id)
            if parent is None or challenge.ability != parent.ability \
                    or challenge.generation != generation + 1 \
                    or challenge.proposed_at <= last_verification \
                    or statuses[parent.lineage_id] == "champion" \
                    or challenge.requested_compute_budget > parent.compute_budget * 1.25:
                continue
            by_challenge_ability.setdefault(challenge.ability, []).append(challenge)
        for ability, rows in by_challenge_ability.items():
            rows.sort(key=lambda item: (-raw_scores[item.parent_id], item.challenge_id))
            accepted.append(rows[0].challenge_id)
        return SurvivalGeneration(tuple(sorted(champions)),
                                  tuple(sorted(scores, key=lambda item: (item.ability,
                                                                        -item.fitness,
                                                                        item.lineage_id))),
                                  tuple(sorted(accepted)), tuple(sorted(residual)))
