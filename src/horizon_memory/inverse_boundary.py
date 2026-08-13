# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Inverse-boundary emission: causal negative constraints for relational readout.

The forward horizon retains/coarse-grains admissible state.  This inverse operator never
reconstructs that state and never chooses an answer; it emits a minimal, provenanced set of
currently forbidden relational paths (a nogood certificate) before candidate scoring.
"""
from __future__ import annotations

from dataclasses import dataclass

from .latent_relational_dynamics import RelationalSeparation


@dataclass(frozen=True)
class ExcludedEdge:
    left: str
    right: str
    reason: str
    fact_id: int
    valid_until: int | None


@dataclass(frozen=True)
class ExclusionCertificate:
    scope: str
    clock: int
    excluded_edges: tuple[ExcludedEdge, ...]
    evidence_fact_ids: tuple[int, ...]

    def forbids(self, left: str, right: str) -> bool:
        edge = (left, right) if left < right else (right, left)
        return any((item.left, item.right) == edge for item in self.excluded_edges)


class InverseBoundaryField:
    """Emit active exclusions without deleting the positive relational history."""

    def __init__(self, separations: tuple[RelationalSeparation, ...]):
        if separations != tuple(sorted(set(separations))):
            raise ValueError("separations must be unique and canonically sorted")
        self._separations = separations

    def emit(self, scope: str, clock: int, visible_nodes: tuple[str, ...]) -> ExclusionCertificate:
        if not scope or clock < 0:
            raise ValueError("scope and a non-negative causal clock are required")
        if visible_nodes != tuple(sorted(set(visible_nodes))):
            raise ValueError("visible nodes must be unique and canonically sorted")
        nodes = set(visible_nodes)
        emitted = tuple(sorted(
            (ExcludedEdge(item.left, item.right, item.reason,
                          item.fact_id, item.valid_until)
             for item in self._separations
             if item.scope == scope and item.active(clock)
             and (item.left in nodes or item.right in nodes)),
            key=lambda item: (item.left, item.right, item.fact_id),
        ))
        return ExclusionCertificate(scope, clock, emitted,
                                    tuple(sorted({item.fact_id for item in emitted})))
