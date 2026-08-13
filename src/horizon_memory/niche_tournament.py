# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Case-specific competition: specialists fight locally and compose only after validation."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class AbilityRecord:
    competitor_id: str
    ability: str
    verified_correct: int
    wrong_positive: int
    false_accepts: int
    missed_opportunities: int
    first_verified_solves: int
    verified_at: float
    verifier_fact_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        counts = (self.verified_correct, self.wrong_positive, self.false_accepts,
                  self.missed_opportunities, self.first_verified_solves)
        if not self.competitor_id or not self.ability or min(counts) < 0 \
                or self.first_verified_solves > self.verified_correct \
                or self.verified_at < 0 or not self.verifier_fact_ids \
                or self.verifier_fact_ids != tuple(sorted(set(self.verifier_fact_ids))):
            raise ValueError("invalid verified ability record")


@dataclass(frozen=True, order=True)
class ArenaDemand:
    query_id: str
    required_abilities: tuple[str, ...]
    issued_at: float
    impact: float

    def __post_init__(self) -> None:
        if not self.query_id or not self.required_abilities \
                or self.required_abilities != tuple(sorted(set(self.required_abilities))) \
                or self.issued_at < 0 or self.impact < 0:
            raise ValueError("invalid niche demand")


@dataclass(frozen=True, order=True)
class AbilityBid:
    query_id: str
    competitor_id: str
    ability: str
    prepared_at: float
    strength: float
    slot_coverage: float
    compute_cost: float
    evidence_fact_ids: tuple[int, ...]
    hard_contradiction: bool = False

    def __post_init__(self) -> None:
        if not self.query_id or not self.competitor_id or not self.ability \
                or self.prepared_at < 0 or not 0 <= self.strength <= 1 \
                or not 0 <= self.slot_coverage <= 1 or self.compute_cost < 0 \
                or not self.evidence_fact_ids \
                or self.evidence_fact_ids != tuple(sorted(set(self.evidence_fact_ids))):
            raise ValueError("invalid ability bid")


@dataclass(frozen=True)
class ArenaScore:
    ability: str
    competitor_id: str
    reliability_lower_bound: float
    verified_originality: float
    advantage: float


@dataclass(frozen=True)
class TournamentClosure:
    state: str
    winners: tuple[tuple[str, str], ...]
    evidence_fact_ids: tuple[int, ...]
    missing_abilities: tuple[str, ...]
    scores: tuple[ArenaScore, ...]
    reason: str


class VerifiedNicheTournament:
    """Fight within abilities, then compose winners across required abilities.

    Reliability gates truth.  First-solve originality provides advantage only after the
    gate, so hunger can allocate challenges without manufacturing evidence.
    """

    def __init__(self, *, maximum_bidders_per_ability: int = 3,
                 minimum_reliability: float = .45, minimum_margin: float = .02,
                 false_accept_weight: float = 3.0, originality_rate: float = .15):
        if maximum_bidders_per_ability < 1 or not 0 <= minimum_reliability <= 1 \
                or minimum_margin < 0 or false_accept_weight < 1 \
                or originality_rate < 0:
            raise ValueError("invalid niche tournament law")
        self.maximum_bidders_per_ability = maximum_bidders_per_ability
        self.minimum_reliability = minimum_reliability
        self.minimum_margin = minimum_margin
        self.false_accept_weight = false_accept_weight
        self.originality_rate = originality_rate

    def _reliability(self, record: AbilityRecord) -> float:
        failures = (record.wrong_positive + record.missed_opportunities
                    + self.false_accept_weight * record.false_accepts)
        trials = record.verified_correct + failures
        if trials <= 0:
            return 0.0
        proportion = record.verified_correct / trials
        z = 1.96
        denominator = 1 + z * z / trials
        centre = proportion + z * z / (2 * trials)
        radius = z * math.sqrt((proportion * (1 - proportion) + z * z / (4 * trials)) / trials)
        return max(0.0, (centre - radius) / denominator)

    def close(self, demand: ArenaDemand, records: tuple[AbilityRecord, ...],
              bids: tuple[AbilityBid, ...]) -> TournamentClosure:
        if records != tuple(sorted(set(records))) or bids != tuple(sorted(set(bids))):
            raise ValueError("records and bids must be canonical")
        record_map = {(record.competitor_id, record.ability): record for record in records}
        if len(record_map) != len(records):
            raise ValueError("duplicate ability history")
        if any(record.verified_at >= demand.issued_at for record in records):
            raise ValueError("current or future verification cannot influence a fight")
        if any(bid.query_id != demand.query_id or bid.prepared_at >= demand.issued_at
               for bid in bids):
            raise ValueError("bids must be prepared ex ante for this query")

        winners = []
        evidence = set()
        missing = []
        all_scores = []
        contested = False
        for ability in demand.required_abilities:
            contenders = [bid for bid in bids if bid.ability == ability
                          and not bid.hard_contradiction]
            if len(contenders) > self.maximum_bidders_per_ability:
                raise ValueError("niche exceeded its bounded competitive aperture")
            scored = []
            for bid in contenders:
                record = record_map.get((bid.competitor_id, ability))
                if record is None:
                    continue
                reliability = self._reliability(record)
                if reliability < self.minimum_reliability or bid.slot_coverage < 1:
                    continue
                originality = record.first_verified_solves / max(1, record.verified_correct)
                advantage = (reliability * bid.strength * demand.impact
                             * (1 + self.originality_rate * originality)
                             / (1 + bid.compute_cost))
                score = ArenaScore(ability, bid.competitor_id, reliability,
                                   originality, advantage)
                scored.append((score, bid))
                all_scores.append(score)
            scored.sort(key=lambda item: (-item[0].advantage, item[0].competitor_id))
            if not scored:
                missing.append(ability)
                continue
            best_score, best_bid = scored[0]
            if len(scored) > 1 and (best_score.advantage - scored[1][0].advantage) \
                    / max(best_score.advantage, 1e-12) < self.minimum_margin:
                contested = True
                continue
            winners.append((ability, best_bid.competitor_id))
            evidence.update(best_bid.evidence_fact_ids)

        canonical_scores = tuple(sorted(all_scores,
                                        key=lambda item: (item.ability, -item.advantage,
                                                          item.competitor_id)))
        if missing:
            return TournamentClosure("abstain", tuple(sorted(winners)), tuple(sorted(evidence)),
                                     tuple(sorted(missing)), canonical_scores,
                                     "at least one required ability has no validated specialist")
        if contested or len(winners) != len(demand.required_abilities):
            return TournamentClosure("contested", tuple(sorted(winners)),
                                     tuple(sorted(evidence)), (), canonical_scores,
                                     "a local ability fight has no defensible winner")
        return TournamentClosure("committed", tuple(sorted(winners)), tuple(sorted(evidence)),
                                 (), canonical_scores,
                                 "validated local winners composed a complete proof")
