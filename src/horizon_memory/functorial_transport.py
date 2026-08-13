# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scoped, temporal gauge connections for HFEF predicates and jargon."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace

from .event_field import EventRecord, QueryProgram


@dataclass(frozen=True, order=True)
class GaugeConnection:
    scope: str
    source: str
    target: str
    evidence_fact_id: int
    valid_from: int | None = None
    valid_until: int | None = None

    def __post_init__(self) -> None:
        if not self.scope or not self.source or not self.target or self.source == self.target:
            raise ValueError("connection requires distinct source/target and a scope")
        if self.evidence_fact_id < 0:
            raise ValueError("evidence_fact_id must be non-negative")
        if self.valid_from is not None and self.valid_until is not None \
                and self.valid_until < self.valid_from:
            raise ValueError("invalid validity interval")

    def active(self, clock: int | None) -> bool:
        if clock is None:
            return self.valid_from is None and self.valid_until is None
        return ((self.valid_from is None or self.valid_from <= clock) and
                (self.valid_until is None or clock <= self.valid_until))


@dataclass(frozen=True)
class TransportResolution:
    state: str  # resolved | identity | conflict | missing | depth_exceeded
    surface: str
    canonical: str | None
    path: tuple[str, ...]
    evidence_fact_ids: tuple[int, ...]
    alternatives: tuple[str, ...] = ()


class FunctorialTransportLedger:
    """A small directed connection graph; no synonym crosses scope or validity interval."""

    def __init__(self, connections=(), *, max_depth: int = 8) -> None:
        if max_depth < 1:
            raise ValueError("max_depth must be positive")
        self.max_depth = max_depth
        self._connections: set[GaugeConnection] = set()
        self._by_source: dict[tuple[str, str], set[GaugeConnection]] = {}
        for connection in connections:
            self.add(connection)

    def add(self, connection: GaugeConnection) -> None:
        if connection in self._connections:
            return
        self._connections.add(connection)
        self._by_source.setdefault((connection.scope, connection.source), set()).add(connection)

    def resolve(self, scope: str, surface: str, clock: int | None = None) -> TransportResolution:
        if not scope or not surface:
            raise ValueError("scope and surface are required")
        first = tuple(sorted(connection for connection in
                             self._by_source.get((scope, surface), ()) if connection.active(clock)))
        if not first:
            return TransportResolution("identity", surface, surface, (surface,), ())

        queue = deque([(surface, (surface,), ())])
        terminals: dict[str, tuple[tuple[str, ...], tuple[int, ...]]] = {}
        exceeded = False
        cycle_found = False
        while queue:
            node, path, evidence = queue.popleft()
            active = tuple(sorted(connection for connection in
                                  self._by_source.get((scope, node), ())
                                  if connection.active(clock)))
            outgoing = tuple(connection for connection in active if connection.target not in path)
            if not active:
                if node != surface:
                    previous = terminals.get(node)
                    candidate = (path, evidence)
                    if previous is None or candidate < previous:
                        terminals[node] = candidate
                continue
            if not outgoing:
                cycle_found = True
                continue
            if len(path) - 1 >= self.max_depth:
                exceeded = True
                continue
            for connection in outgoing:
                queue.append((connection.target, path + (connection.target,),
                              evidence + (connection.evidence_fact_id,)))

        if len(terminals) > 1:
            return TransportResolution("conflict", surface, None, (), (), tuple(sorted(terminals)))
        if len(terminals) == 1:
            canonical, (path, evidence) = next(iter(terminals.items()))
            return TransportResolution("resolved", surface, canonical, path, evidence)
        if exceeded:
            return TransportResolution("depth_exceeded", surface, None, (), ())
        return TransportResolution("conflict", surface, None, (), (),
                                   (surface,) if cycle_found else ())

    def holonomy_defect(self, scope: str, surface: str, clock: int | None = None) -> int:
        """Zero means one transport endpoint; nonzero counts incompatible endpoints."""
        result = self.resolve(scope, surface, clock)
        return max(0, len(result.alternatives) - 1) if result.state == "conflict" else 0

    def transport_event(self, event: EventRecord, clock: int | None = None) -> EventRecord:
        """Canonicalize an event without erasing its surface predicate or transport proof."""
        result = self.resolve(event.scope, event.predicate, clock)
        if result.state == "identity":
            return event
        if result.state != "resolved" or result.canonical is None:
            raise ValueError(f"predicate transport failed closed: {result.state}")
        return replace(event, predicate=result.canonical, surface_predicate=event.predicate,
                       transport_fact_ids=result.evidence_fact_ids)

    def transport_program(self, program: QueryProgram, clock: int | None = None) -> QueryProgram:
        """Apply the same connection to query programs, recursively preserving their algebra."""
        result = self.resolve(program.scope, program.predicate, clock)
        if result.state not in ("identity", "resolved") or result.canonical is None:
            raise ValueError(f"query transport failed closed: {result.state}")
        left = self.transport_program(program.left, clock) if program.left is not None else None
        right = self.transport_program(program.right, clock) if program.right is not None else None
        return replace(program, predicate=result.canonical, left=left, right=right)
