# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Temporary ideal coalitions that can depose a dominant champion without forming a cartel."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class IdealChallenger:
    lineage_id: str
    ideal_id: str
    abilities: tuple[str, ...]
    solo_strength: float
    verified_reliability: float
    compute_cost: float
    evidence_fact_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.lineage_id or not self.ideal_id or not self.abilities \
                or self.abilities != tuple(sorted(set(self.abilities))) \
                or not 0 <= self.solo_strength <= 1 \
                or not 0 <= self.verified_reliability <= 1 or self.compute_cost < 0 \
                or not self.evidence_fact_ids \
                or self.evidence_fact_ids != tuple(sorted(set(self.evidence_fact_ids))):
            raise ValueError("invalid ideal challenger")


@dataclass(frozen=True, order=True)
class DominantChampion:
    lineage_id: str
    ideal_id: str
    strength: float
    required_abilities: tuple[str, ...]
    verified_at: float
    verifier_fact_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.lineage_id or not self.ideal_id or not 0 <= self.strength <= 1 \
                or not self.required_abilities \
                or self.required_abilities != tuple(sorted(set(self.required_abilities))) \
                or self.verified_at < 0 or not self.verifier_fact_ids \
                or self.verifier_fact_ids != tuple(sorted(set(self.verifier_fact_ids))):
            raise ValueError("invalid dominant champion")


@dataclass(frozen=True, order=True)
class CoalitionVerification:
    coalition_id: str
    member_ids: tuple[str, ...]
    state: str  # correct | wrong
    verified_strength: float
    member_contributions: tuple[tuple[str, float], ...]
    decided_at: float
    verified_at: float
    verifier_fact_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        ids = tuple(member for member, _ in self.member_contributions)
        if not self.coalition_id or len(self.member_ids) != 2 \
                or self.member_ids != tuple(sorted(set(self.member_ids))) \
                or self.state not in ("correct", "wrong") \
                or not 0 <= self.verified_strength <= 1 \
                or ids != self.member_ids \
                or any(not 0 <= contribution <= 1 for _, contribution
                       in self.member_contributions) \
                or self.decided_at < 0 or self.verified_at <= self.decided_at \
                or not self.verifier_fact_ids \
                or self.verifier_fact_ids != tuple(sorted(set(self.verifier_fact_ids))):
            raise ValueError("invalid coalition verification")


@dataclass(frozen=True)
class CoalitionResult:
    state: str
    coalition_id: str | None
    member_ids: tuple[str, ...]
    successor_id: str | None
    coauthor_ids: tuple[str, ...]
    covered_abilities: tuple[str, ...]
    evidence_fact_ids: tuple[int, ...]
    reason: str


class IdealCoalitionTournament:
    """Allow exactly two complementary losers to challenge, then dissolve the alliance."""

    def __init__(self, *, maximum_joint_cost: float = 3.0,
                 minimum_complementarity: float = .5, victory_margin: float = .01):
        if maximum_joint_cost <= 0 or not 0 <= minimum_complementarity <= 1 \
                or victory_margin < 0:
            raise ValueError("invalid ideal coalition law")
        self.maximum_joint_cost = maximum_joint_cost
        self.minimum_complementarity = minimum_complementarity
        self.victory_margin = victory_margin

    @staticmethod
    def _complementarity(left: IdealChallenger, right: IdealChallenger) -> float:
        a, b = set(left.abilities), set(right.abilities)
        return len(a ^ b) / max(1, len(a | b))

    def challenge(self, champion: DominantChampion,
                  challengers: tuple[IdealChallenger, ...],
                  verification: CoalitionVerification | None) -> CoalitionResult:
        if challengers != tuple(sorted(set(challengers))) \
                or len({item.lineage_id for item in challengers}) != len(challengers):
            raise ValueError("challengers must be unique and canonical")
        candidates = []
        required = set(champion.required_abilities)
        for index, left in enumerate(challengers):
            for right in challengers[index + 1:]:
                if left.ideal_id != champion.ideal_id or right.ideal_id != champion.ideal_id:
                    continue
                # An alliance is exceptional: members that already beat the champion solo
                # must fight alone, not borrow another lineage's credit.
                if left.solo_strength >= champion.strength or right.solo_strength >= champion.strength:
                    continue
                union = set(left.abilities) | set(right.abilities)
                if not required <= union or left.compute_cost + right.compute_cost > self.maximum_joint_cost:
                    continue
                complementarity = self._complementarity(left, right)
                if complementarity < self.minimum_complementarity:
                    continue
                potential = math.sqrt(max(left.solo_strength * right.solo_strength, 0)) \
                    * (1 + complementarity) \
                    * math.sqrt(left.verified_reliability * right.verified_reliability)
                candidates.append((potential, (left.lineage_id, right.lineage_id), left, right))
        if not candidates:
            return CoalitionResult("no_coalition", None, (), champion.lineage_id, (), (), (),
                                   "no two solo losers share an ideal and complementary proof")
        _, member_ids, left, right = sorted(candidates,
                                            key=lambda item: (-item[0], item[1]))[0]
        member_ids = tuple(sorted(member_ids))
        if verification is None:
            return CoalitionResult("awaiting_verification", None, member_ids, None, (),
                                   tuple(sorted(set(left.abilities) | set(right.abilities))),
                                   tuple(sorted(set(left.evidence_fact_ids)
                                                | set(right.evidence_fact_ids))),
                                   "eligible alliance must publish a delayed joint proof")
        if verification.member_ids != member_ids \
                or verification.decided_at <= champion.verified_at:
            raise ValueError("verification does not belong to the selected post-champion alliance")
        evidence = tuple(sorted(set(left.evidence_fact_ids) | set(right.evidence_fact_ids)))
        abilities = tuple(sorted(set(left.abilities) | set(right.abilities)))
        if verification.state != "correct" \
                or verification.verified_strength < champion.strength + self.victory_margin:
            return CoalitionResult("champion_retained", verification.coalition_id, member_ids,
                                   champion.lineage_id, (), abilities, evidence,
                                   "the externally verified alliance did not surpass the champion")
        contributions = sorted(verification.member_contributions,
                               key=lambda item: (-item[1], item[0]))
        successor = contributions[0][0]
        if len(contributions) > 1 and contributions[0][1] == contributions[1][1]:
            return CoalitionResult("coalition_won_contested_succession",
                                   verification.coalition_id, member_ids, None,
                                   member_ids, abilities, evidence,
                                   "alliance won but equal contributions forbid invented supremacy")
        coauthors = tuple(member for member in member_ids if member != successor)
        return CoalitionResult("coalition_won_and_dissolved", verification.coalition_id,
                               member_ids, successor, coauthors, abilities, evidence,
                               "joint ideal defeated the champion; highest verified contributor succeeds")
