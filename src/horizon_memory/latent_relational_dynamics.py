# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Latent mediators and observed separation pressure for relational cold-path proposals.

Cosmological language is intentionally absent from the API.  The testable mechanisms are:

* a latent mediator: an unobserved-at-query companion required by multiple independent,
  causally prior co-activities;
* separation pressure: an authoritative boundary that invalidates an otherwise short path.

This is a bounded graph closure, not a learned embedding and not evidence that an unseen
surface has a particular meaning.  It may only propose a previously authoritative predicate.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from .relational_music import RelationalPerformance


@dataclass(frozen=True, order=True)
class RelationalSeparation:
    scope: str
    left: str
    right: str
    fact_id: int
    observed_at: int
    valid_until: int | None = None
    reason: str = "boundary"

    def __post_init__(self) -> None:
        if not self.scope or not self.left or not self.right or self.left >= self.right:
            raise ValueError("separation endpoints must be non-empty and canonically ordered")
        if self.fact_id < 0 or self.observed_at < 0:
            raise ValueError("fact_id and observed_at must be non-negative")
        if self.valid_until is not None and self.valid_until < self.observed_at:
            raise ValueError("separation validity cannot end before observation")
        if not self.reason:
            raise ValueError("separation reason is required")

    def active(self, clock: int) -> bool:
        return self.observed_at <= clock \
            and (self.valid_until is None or clock <= self.valid_until)


@dataclass(frozen=True)
class LatentPath:
    left: str
    mediator: str
    right: str
    evidence_fact_ids: tuple[int, ...]


@dataclass(frozen=True)
class LatentCandidate:
    canonical: str
    pair_coverage: float
    independent_paths: int
    score: float
    mediators: tuple[str, ...]
    evidence_fact_ids: tuple[int, ...]
    paths: tuple[LatentPath, ...]


@dataclass(frozen=True)
class LatentResolution:
    state: str  # resolved | abstain
    surface: str
    canonical: str | None
    evidence_fact_ids: tuple[int, ...]
    separation_fact_ids: tuple[int, ...]
    reason: str
    candidates: tuple[LatentCandidate, ...]


class LatentRelationalField:
    """Infer only bounded, independently witnessed two-hop relational closure."""

    def __init__(self, performances: tuple[RelationalPerformance, ...],
                 separations: tuple[RelationalSeparation, ...] = (), *,
                 min_pair_coverage: float = 2 / 3, min_independent_paths: int = 2,
                 min_margin: float = 0.20) -> None:
        if performances != tuple(sorted(set(performances))):
            raise ValueError("performances must be unique and canonically sorted")
        if separations != tuple(sorted(set(separations))):
            raise ValueError("separations must be unique and canonically sorted")
        if not 0 < min_pair_coverage <= 1 or min_independent_paths < 2 \
                or not 0 <= min_margin <= 1:
            raise ValueError("invalid latent relational gates")
        self._performances = performances
        self._separations = separations
        self.min_pair_coverage = min_pair_coverage
        self.min_independent_paths = min_independent_paths
        self.min_margin = min_margin

    @staticmethod
    def _edge_key(left: str, right: str) -> tuple[str, str]:
        return (left, right) if left < right else (right, left)

    def listen(self, scope: str, surface: str, companions: tuple[str, ...], clock: int) \
            -> LatentResolution:
        if not scope or not surface.strip() or clock < 0:
            raise ValueError("scope, surface and causal clock are required")
        if companions != tuple(sorted(set(companions))) or len(companions) < 3:
            raise ValueError("latent closure requires at least three unique sorted companions")

        visible = tuple(item for item in self._performances
                        if item.scope == scope and item.observed_at <= clock
                        and item.pragmatic == "literal")
        forbidden = {
            (item.left, item.right): item.fact_id for item in self._separations
            if item.scope == scope and item.active(clock)
        }
        query = set(companions)
        pair_count = len(companions) * (len(companions) - 1) // 2
        candidates = []
        rejected_separation_ids: set[int] = set()
        for canonical in sorted({item.canonical for item in visible}):
            observations = tuple(item for item in visible if item.canonical == canonical)
            edges: dict[tuple[str, str], set[int]] = {}
            for item in observations:
                for left, right in combinations(item.companions, 2):
                    edges.setdefault(self._edge_key(left, right), set()).add(item.fact_id)

            paths = []
            covered_pairs = set()
            canonical_separation_ids = set()
            nodes = {node for edge in edges for node in edge}
            for left, right in combinations(companions, 2):
                pair = self._edge_key(left, right)
                if pair in forbidden:
                    canonical_separation_ids.add(forbidden[pair])
                    continue
                for mediator in sorted(nodes - query):
                    edge_a = self._edge_key(left, mediator)
                    edge_b = self._edge_key(mediator, right)
                    if edge_a not in edges or edge_b not in edges:
                        continue
                    if edge_a in forbidden or edge_b in forbidden:
                        if edge_a in forbidden:
                            canonical_separation_ids.add(forbidden[edge_a])
                        if edge_b in forbidden:
                            canonical_separation_ids.add(forbidden[edge_b])
                        continue
                    # Two different performances are mandatory: one repeated chord cannot
                    # manufacture a hidden variable from its own clique expansion.
                    witnesses = next(((a, b) for a in sorted(edges[edge_a])
                                      for b in sorted(edges[edge_b]) if a != b), None)
                    if witnesses is None:
                        continue
                    paths.append(LatentPath(left, mediator, right, tuple(sorted(witnesses))))
                    covered_pairs.add(pair)
            if canonical_separation_ids:
                rejected_separation_ids.update(canonical_separation_ids)
                continue
            independent_evidence = {fact_id for path in paths for fact_id in path.evidence_fact_ids}
            coverage = len(covered_pairs) / pair_count
            if coverage < self.min_pair_coverage \
                    or len(paths) < self.min_independent_paths \
                    or len(independent_evidence) < 3:
                continue
            score = coverage * min(1.0, len(independent_evidence) / len(companions))
            candidates.append(LatentCandidate(
                canonical, coverage, len(paths), score,
                tuple(sorted({path.mediator for path in paths})),
                tuple(sorted(independent_evidence)), tuple(paths),
            ))
        ranked = tuple(sorted(candidates, key=lambda item: (-item.score, item.canonical)))
        if not ranked:
            reason = ("observed separation blocks latent closure" if rejected_separation_ids
                      else "no independently witnessed latent closure")
            return LatentResolution("abstain", surface, None, (),
                                    tuple(sorted(rejected_separation_ids)), reason, ())
        runner_up = ranked[1].score if len(ranked) > 1 else 0.0
        if ranked[0].score - runner_up < self.min_margin:
            return LatentResolution("abstain", surface, None, (), (),
                                    "multiple latent structures explain the chord", ranked)
        winner = ranked[0]
        return LatentResolution("resolved", surface, winner.canonical,
                                winner.evidence_fact_ids, (),
                                "unique independently witnessed latent mediator", ranked)
