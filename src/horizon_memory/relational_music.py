# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Causal relational readout for previously unseen predicate surfaces.

The V16 "music" result was about co-activity: an isolated item's frequency was weak, while
the activity of its goal companions was informative.  This module transfers only that
mechanism.  It proposes a canonical predicate when the *joint companion signature* of a new
surface matches one previously observed around authoritative predicates.

It is deliberately not a semantic oracle.  The surface spelling is never scored, future
observations are invisible, non-literal readings abstain, and every accepted proposal carries
the FactIds of the performances that supported it.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


_NON_LITERAL = frozenset(("ironic", "sarcastic", "hypothetical", "uncertain"))


@dataclass(frozen=True, order=True)
class RelationalPerformance:
    """One authoritative occurrence of a predicate inside a relational chord."""

    scope: str
    canonical: str
    surface: str
    companions: tuple[str, ...]
    fact_id: int
    observed_at: int
    pragmatic: str = "literal"

    def __post_init__(self) -> None:
        if not self.scope or not self.canonical or not self.surface.strip():
            raise ValueError("scope, canonical predicate and surface are required")
        if self.fact_id < 0 or self.observed_at < 0:
            raise ValueError("fact_id and observed_at must be non-negative")
        if self.companions != tuple(sorted(set(self.companions))) or len(self.companions) < 2:
            raise ValueError("a relational performance needs at least two unique sorted companions")
        if any(not item or item.startswith("predicate:") for item in self.companions):
            raise ValueError("companions cannot contain empty values or the predicate label")
        if not self.pragmatic:
            raise ValueError("pragmatic state is required")


@dataclass(frozen=True)
class MelodyCandidate:
    canonical: str
    coverage: float
    support: int
    score: float
    evidence_fact_ids: tuple[int, ...]
    shared_companions: tuple[str, ...]


@dataclass(frozen=True)
class MusicResolution:
    state: str  # resolved | abstain
    surface: str
    canonical: str | None
    evidence_fact_ids: tuple[int, ...]
    reason: str
    candidates: tuple[MelodyCandidate, ...]


class RelationalMusicField:
    """Listen to a whole relational chord instead of classifying an isolated word.

    Scores are weighted set coverage.  A companion occurring around many canonical
    predicates has little discriminative weight (IDF); a conjunction characteristic of one
    predicate has more.  `min_shared`, coverage and margin are safety gates rather than
    trainable probabilities.
    """

    def __init__(self, performances: tuple[RelationalPerformance, ...], *,
                 min_shared: int = 2, min_coverage: float = 0.60,
                 min_margin: float = 0.20) -> None:
        if performances != tuple(sorted(set(performances))):
            raise ValueError("performances must be unique and canonically sorted")
        if min_shared < 2 or not 0 < min_coverage <= 1 or not 0 <= min_margin <= 1:
            raise ValueError("invalid relational music gates")
        self._performances = performances
        self.min_shared = min_shared
        self.min_coverage = min_coverage
        self.min_margin = min_margin

    @staticmethod
    def _visible(item: RelationalPerformance, scope: str, clock: int) -> bool:
        return item.scope == scope and item.observed_at <= clock \
            and item.pragmatic not in _NON_LITERAL

    def listen(self, scope: str, surface: str, companions: tuple[str, ...], clock: int,
               *, pragmatic: str = "literal") -> MusicResolution:
        if not scope or not surface.strip() or clock < 0:
            raise ValueError("scope, surface and a non-negative causal clock are required")
        if companions != tuple(sorted(set(companions))) or len(companions) < self.min_shared:
            raise ValueError("query companions must be unique, sorted and relational")
        if pragmatic in _NON_LITERAL:
            return MusicResolution("abstain", surface, None, (),
                                   "non-literal performance has competing interpretations", ())

        visible = tuple(item for item in self._performances if self._visible(item, scope, clock))
        canonicals = tuple(sorted({item.canonical for item in visible}))
        if not canonicals:
            return MusicResolution("abstain", surface, None, (),
                                   "no causally visible performance in scope", ())

        by_canonical = {canonical: tuple(item for item in visible if item.canonical == canonical)
                        for canonical in canonicals}
        document_frequency = {
            companion: sum(any(companion in item.companions for item in by_canonical[canonical])
                           for canonical in canonicals)
            for companion in companions
        }
        weights = {
            companion: 1.0 + math.log((1.0 + len(canonicals)) /
                                      (1.0 + document_frequency[companion]))
            for companion in companions
        }
        total_weight = sum(weights.values())
        candidates = []
        query_set = set(companions)
        for canonical, observations in by_canonical.items():
            matching = []
            union_shared: set[str] = set()
            best_coverage = 0.0
            for item in observations:
                shared = query_set.intersection(item.companions)
                coverage = sum(weights[value] for value in shared) / total_weight
                if len(shared) >= self.min_shared:
                    matching.append(item)
                    union_shared.update(shared)
                    best_coverage = max(best_coverage, coverage)
            if not matching:
                continue
            # Repetition adds confidence sublinearly; it can never compensate for poor coverage.
            score = best_coverage * (1.0 + 0.05 * math.log1p(len(matching)))
            candidates.append(MelodyCandidate(
                canonical=canonical, coverage=best_coverage, support=len(matching), score=score,
                evidence_fact_ids=tuple(sorted({item.fact_id for item in matching})),
                shared_companions=tuple(sorted(union_shared)),
            ))
        ranked = tuple(sorted(candidates, key=lambda item: (-item.score, item.canonical)))
        if not ranked or ranked[0].coverage < self.min_coverage:
            return MusicResolution("abstain", surface, None, (),
                                   "no complete relational melody", ranked)
        runner_up = ranked[1].score if len(ranked) > 1 else 0.0
        if ranked[0].score - runner_up < self.min_margin:
            return MusicResolution("abstain", surface, None, (),
                                   "two relational melodies remain compatible", ranked)
        winner = ranked[0]
        return MusicResolution("resolved", surface, winner.canonical,
                               winner.evidence_fact_ids,
                               "unique causal relational melody", ranked)
