# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Semantic symmetry breaking as proof-hyperedge projection, not a model vote."""
from __future__ import annotations

from dataclasses import dataclass

from .unified_causal_field import ProofHyperedge


_INVERTED_PRAGMATICS = frozenset(("ironic", "sarcastic", "negated"))


@dataclass(frozen=True, order=True)
class SemanticMode:
    """An authoritative canonical meaning observed through relational channels."""

    scope: str
    canonical: str
    channels: tuple[str, ...]
    fact_id: int
    observed_at: int

    def __post_init__(self) -> None:
        if not self.scope or not self.canonical or self.fact_id < 0 or self.observed_at < 0:
            raise ValueError("semantic mode requires scope, canonical, FactId and clock")
        if self.channels != tuple(sorted(set(self.channels))) or len(self.channels) < 2:
            raise ValueError("semantic mode requires at least two unique sorted channels")


@dataclass(frozen=True, order=True)
class SurfacePerformance:
    """One use of an unknown surface; it deliberately contains no canonical label."""

    scope: str
    surface: str
    channels: tuple[str, ...]
    fact_id: int
    observed_at: int
    pragmatic: str = "literal"

    def __post_init__(self) -> None:
        if not self.scope or not self.surface.strip() or self.fact_id < 0 or self.observed_at < 0:
            raise ValueError("surface performance requires scope, surface, FactId and clock")
        if self.channels != tuple(sorted(set(self.channels))) or len(self.channels) < 2:
            raise ValueError("surface performance requires at least two unique sorted channels")
        if not self.pragmatic:
            raise ValueError("pragmatic phase is required")


class SemanticInterferometer:
    """Project repeated uses onto canonical modes and emit only HUCF hyperedges.

    A spelling never scores.  A candidate gains one pulse for each causally visible,
    independently witnessed use whose relational channels cover a canonical mode.  Literal
    and inverted pragmatic phases use the same law with opposite signs.  The returned edges
    still need HUCF margin, witness, silence and boundary gates.
    """

    def __init__(self, modes: tuple[SemanticMode, ...], *, min_shared: int = 2,
                 min_coverage: float = 0.75, min_independent_performances: int = 2) -> None:
        if modes != tuple(sorted(set(modes))):
            raise ValueError("semantic modes must be unique and canonically sorted")
        if min_shared < 2 or not 0 < min_coverage <= 1 or min_independent_performances < 2:
            raise ValueError("invalid semantic interferometer gates")
        self._modes = modes
        self.min_shared = min_shared
        self.min_coverage = min_coverage
        self.min_independent_performances = min_independent_performances

    def project(self, scope: str, surface: str,
                performances: tuple[SurfacePerformance, ...], clock: int) -> tuple[ProofHyperedge, ...]:
        if not scope or not surface.strip() or clock < 0:
            raise ValueError("scope, surface and causal clock are required")
        if performances != tuple(sorted(set(performances))):
            raise ValueError("surface performances must be unique and canonically sorted")
        visible_modes = tuple(mode for mode in self._modes
                              if mode.scope == scope and mode.observed_at <= clock)
        visible_uses = tuple(item for item in performances
                             if item.scope == scope and item.surface == surface
                             and item.observed_at <= clock)
        projected = []
        for use in visible_uses:
            use_channels = set(use.channels)
            for mode in visible_modes:
                shared = tuple(sorted(use_channels.intersection(mode.channels)))
                coverage = len(shared) / len(mode.channels)
                if len(shared) < self.min_shared or coverage < self.min_coverage:
                    continue
                inverted = use.pragmatic in _INVERTED_PRAGMATICS
                channels = tuple(sorted(set(shared) | {f"mode:{mode.canonical}"}))
                projected.append((ProofHyperedge(
                    scope=scope, canonical=mode.canonical, channels=channels,
                    evidence_fact_ids=tuple(sorted((use.fact_id, mode.fact_id))),
                    observed_at=max(use.observed_at, mode.observed_at),
                    amplitude=-1.0 if inverted else 1.0,
                    origin="semantic_interferometry", hard_negative=inverted,
                ), use.fact_id))
        independent = {}
        for edge, use_fact_id in projected:
            independent.setdefault(edge.canonical, set()).add(use_fact_id)
        admitted = (edge for edge, _ in projected
                    if len(independent[edge.canonical]) >= self.min_independent_performances)
        return tuple(sorted(set(admitted)))
