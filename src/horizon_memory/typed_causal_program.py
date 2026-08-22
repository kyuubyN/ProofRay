# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Domain-neutral exact programs over a typed causal event boundary.

This module is deliberately independent of natural-language compilation.  It is the
small deterministic kernel that a host, parser or verified ingest adapter may target.
Evidence identity, event orbits, clocks, polarity and units remain explicit; ambiguity
never becomes a score.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from bisect import bisect_right
import re


_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, order=True)
class TypedCausalFact:
    fact_id: int
    scope: str
    subject: str
    predicate: str
    value: str
    observed_at: int
    event_time: int
    version: int = 1
    unit: str = ""
    polarity: int = 1
    asserted: bool = True
    event_id: str = ""
    causes: tuple[int, ...] = ()
    source_id: str = ""
    source_sha256: str = ""
    source_span: tuple[int, int] = (0, 0)

    def __post_init__(self) -> None:
        if self.fact_id < 0 or not self.scope or not self.subject or not self.predicate:
            raise ValueError("typed causal facts need identity, scope, subject and predicate")
        if self.observed_at < 0 or self.event_time < 0 or self.version < 1:
            raise ValueError("typed causal clocks and versions must be non-negative")
        if self.polarity not in (-1, 1):
            raise ValueError("typed causal polarity must be -1 or +1")
        if tuple(sorted(set(self.causes))) != self.causes or self.fact_id in self.causes:
            raise ValueError("cause FactIds must be canonical and cannot contain the effect")
        if not self.source_id or not _SHA256.fullmatch(self.source_sha256):
            raise ValueError("typed causal facts require a source id and canonical SHA-256")
        if self.source_span[0] < 0 or self.source_span[1] <= self.source_span[0]:
            raise ValueError("typed causal facts require a non-empty source span")

    @property
    def orbit(self) -> str:
        return self.event_id or f"fact:{self.fact_id}"


@dataclass(frozen=True, order=True)
class CausalSelector:
    subject: str
    predicate: str
    value: str | None = None

    def __post_init__(self) -> None:
        if not self.subject or not self.predicate:
            raise ValueError("causal selectors need subject and predicate")


@dataclass(frozen=True)
class TypedCausalProgram:
    operator: str
    selector: CausalSelector
    operands: tuple[CausalSelector, ...] = ()
    unit: str = ""
    at_time: int | None = None
    closed_world: bool = False

    def __post_init__(self) -> None:
        allowed = {"LOOKUP", "COUNT_DISTINCT", "SUM", "INTERVAL", "DURATION",
                   "EXPLAIN_CAUSE"}
        if self.operator not in allowed:
            raise ValueError(f"unsupported typed causal operator: {self.operator}")
        if self.at_time is not None and self.at_time < 0:
            raise ValueError("program time must be non-negative")


@dataclass(frozen=True)
class TypedCausalResult:
    state: str
    value: str | None
    unit: str
    fact_ids: tuple[int, ...]
    reason: str
    proofs: tuple["TypedCausalProof", ...] = ()
    examined_fact_ids: tuple[int, ...] = ()


@dataclass(frozen=True, order=True)
class TypedCausalProof:
    fact_id: int
    source_id: str
    source_sha256: str
    source_span: tuple[int, int]


class TypedCausalExecutor:
    """Execute exact programs without reconstructing or reranking raw history."""

    def __init__(self, facts: tuple[TypedCausalFact, ...], scope: str):
        if not facts or not scope:
            raise ValueError("typed causal executor needs a scope and facts")
        if tuple(fact.fact_id for fact in facts) != tuple(sorted({fact.fact_id for fact in facts})):
            raise ValueError("typed causal facts must be FactId-canonical")
        if any(fact.scope != scope for fact in facts):
            raise ValueError("facts from different scopes require separate executors")
        known = {fact.fact_id for fact in facts}
        if any(set(fact.causes) - known for fact in facts):
            raise ValueError("every causal edge must reference a fact in the same boundary")
        self.facts = facts
        self.scope = scope
        self.by_id = {fact.fact_id: fact for fact in facts}
        fibers: dict[tuple[str, str], list[TypedCausalFact]] = {}
        for fact in facts:
            fibers.setdefault((fact.subject, fact.predicate), []).append(fact)
        self.fibers = {key: tuple(value) for key, value in fibers.items()}
        self.lookup_prefix = {key: self._build_lookup_prefix(value)
                              for key, value in self.fibers.items()}

    @staticmethod
    def _build_lookup_prefix(facts: tuple[TypedCausalFact, ...]) \
            -> tuple[tuple[int, ...], tuple[tuple[TypedCausalFact, ...], ...]]:
        """Materialize the causal winner after each observation clock."""
        by_time: dict[int, list[TypedCausalFact]] = {}
        for fact in facts:
            by_time.setdefault(fact.observed_at, []).append(fact)
        times, snapshots = [], []
        best_clock: tuple[int, int, int] | None = None
        winners: list[TypedCausalFact] = []
        for observed_at in sorted(by_time):
            for fact in by_time[observed_at]:
                clock = (fact.version, fact.event_time, fact.observed_at)
                if best_clock is None or clock > best_clock:
                    best_clock, winners = clock, [fact]
                elif clock == best_clock:
                    winners.append(fact)
            times.append(observed_at)
            snapshots.append(tuple(sorted(winners, key=lambda fact: fact.fact_id)))
        return tuple(times), tuple(snapshots)

    def _lookup_selected(self, selector: CausalSelector, at_time: int | None,
                         examined: set[int]) -> tuple[TypedCausalFact, ...]:
        if selector.value is not None:
            selected = self._selected(selector, at_time, examined)
            return () if selected is None else selected
        times, snapshots = self.lookup_prefix.get((selector.subject, selector.predicate), ((), ()))
        position = len(times) - 1 if at_time is None else bisect_right(times, at_time) - 1
        if position < 0:
            return ()
        winners = snapshots[position]
        examined.update(fact.fact_id for fact in winners)
        return winners

    @staticmethod
    def _matches(fact: TypedCausalFact, selector: CausalSelector) -> bool:
        return (fact.subject == selector.subject and fact.predicate == selector.predicate and
                (selector.value is None or fact.value == selector.value))

    @staticmethod
    def _latest_orbits(facts: tuple[TypedCausalFact, ...]) \
            -> tuple[TypedCausalFact, ...] | None:
        """Collapse reports by event orbit; conflicting latest reports are inadmissible."""
        by_orbit: dict[str, list[TypedCausalFact]] = {}
        for fact in facts:
            by_orbit.setdefault(fact.orbit, []).append(fact)
        selected = []
        for orbit in sorted(by_orbit):
            rows = by_orbit[orbit]
            clock = max((fact.version, fact.observed_at) for fact in rows)
            latest = [fact for fact in rows if (fact.version, fact.observed_at) == clock]
            signatures = {(fact.value, fact.unit, fact.polarity, fact.asserted,
                           fact.event_time, fact.causes) for fact in latest}
            if len(signatures) != 1:
                return None
            selected.append(min(latest, key=lambda fact: fact.fact_id))
        return tuple(selected)

    @staticmethod
    def _format(value: Decimal) -> str:
        rendered = format(value, "f")
        return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered

    def _selected(self, selector: CausalSelector, at_time: int | None,
                  examined: set[int]) \
            -> tuple[TypedCausalFact, ...] | None:
        fiber = self.fibers.get((selector.subject, selector.predicate), ())
        examined.update(fact.fact_id for fact in fiber)
        # Select the latest fact per orbit *before* filtering by selector value, not after: an
        # entity that changed state (e.g. role "user" -> "admin") has an orbit whose true-latest
        # version may not match this selector's value. Filtering by value first would drop that
        # latest version and let an older, matching version pass `_latest_orbits` as "the latest
        # of its group" -- silently asserting a stale state as current (found via code review,
        # 2026-08-2x).
        timely = tuple(fact for fact in fiber if at_time is None or fact.observed_at <= at_time)
        latest = self._latest_orbits(timely)
        if latest is None:
            return None
        return tuple(fact for fact in latest if self._matches(fact, selector))

    def execute(self, program: TypedCausalProgram) -> TypedCausalResult:
        examined: set[int] = set()
        result = self._execute(program, examined)
        proofs = tuple(TypedCausalProof(
            fact_id, self.by_id[fact_id].source_id, self.by_id[fact_id].source_sha256,
            self.by_id[fact_id].source_span) for fact_id in result.fact_ids)
        return replace(result, proofs=proofs, examined_fact_ids=tuple(sorted(examined)))

    def _execute(self, program: TypedCausalProgram, examined: set[int]) -> TypedCausalResult:
        if program.operator == "LOOKUP":
            latest = self._lookup_selected(program.selector, program.at_time, examined)
            if not latest:
                return TypedCausalResult("abstain", None, "", (), "no_matching_fact")
            signatures = {(fact.value, fact.unit, fact.polarity, fact.asserted)
                          for fact in latest}
            if len(signatures) != 1:
                reason = ("conflicting_latest_orbit" if len({fact.orbit for fact in latest}) == 1
                          else "contested_latest_state")
                return TypedCausalResult("abstain", None, "", (), reason)
            value, unit, polarity, asserted = next(iter(signatures))
            if not asserted:
                return TypedCausalResult("abstain", None, "", (), "uncertain_fact")
            if polarity < 0:
                return TypedCausalResult("abstain", None, "", (), "latest_state_is_negative")
            witness = min(latest, key=lambda fact: fact.fact_id)
            return TypedCausalResult("resolved", value, unit, (witness.fact_id,),
                                     "unique_latest_state")

        selected = self._selected(program.selector, program.at_time, examined)
        if selected is None:
            return TypedCausalResult("abstain", None, "", (), "conflicting_latest_orbit")
        if not selected:
            return TypedCausalResult("abstain", None, "", (), "no_matching_fact")
        if any(not fact.asserted for fact in selected):
            return TypedCausalResult("abstain", None, "", (), "uncertain_fact")

        if program.operator in ("COUNT_DISTINCT", "SUM") and not program.closed_world:
            return TypedCausalResult("unsupported", None, "", (), "closed_world_required")

        active = tuple(fact for fact in selected if fact.polarity > 0)
        if program.operator == "COUNT_DISTINCT":
            values = {fact.value for fact in active}
            return TypedCausalResult("resolved", str(len(values)), "count",
                                     tuple(fact.fact_id for fact in active),
                                     "closed_world_distinct_count")

        if program.operator == "SUM":
            if not active:
                return TypedCausalResult("abstain", None, "", (), "no_positive_measurements")
            units = {fact.unit for fact in active}
            if not program.unit or units != {program.unit}:
                return TypedCausalResult("abstain", None, "", (), "unit_not_conserved")
            try:
                total = sum((Decimal(fact.value) for fact in active), Decimal(0))
            except InvalidOperation:
                return TypedCausalResult("abstain", None, "", (), "non_numeric_measurement")
            return TypedCausalResult("resolved", self._format(total), program.unit,
                                     tuple(fact.fact_id for fact in active),
                                     "closed_world_exact_sum")

        if program.operator in ("INTERVAL", "DURATION"):
            selectors = (program.selector,) + program.operands
            if len(selectors) != 2:
                return TypedCausalResult("unsupported", None, "", (),
                                         "two_temporal_operands_required")
            operands = []
            for selector in selectors:
                rows = self._selected(selector, program.at_time, examined)
                if rows is None or len(rows) != 1 or not rows[0].asserted or rows[0].polarity < 0:
                    return TypedCausalResult("abstain", None, "", (),
                                             "temporal_operand_not_unique")
                operands.append(rows[0])
            distance = abs(operands[1].event_time - operands[0].event_time)
            return TypedCausalResult("resolved", str(distance), program.unit or "tick",
                                     tuple(fact.fact_id for fact in operands),
                                     "exact_causal_clock_difference")

        if program.operator == "EXPLAIN_CAUSE":
            effects = tuple(fact for fact in selected if fact.polarity > 0)
            if len(effects) != 1 or not effects[0].causes:
                return TypedCausalResult("abstain", None, "", (), "unique_causal_edge_required")
            causes = tuple(self.by_id[fact_id] for fact_id in effects[0].causes)
            if any(program.at_time is not None and fact.observed_at > program.at_time
                   for fact in causes):
                return TypedCausalResult("abstain", None, "", (), "cause_outside_light_cone")
            if any(not fact.asserted or fact.polarity < 0 for fact in causes):
                return TypedCausalResult("abstain", None, "", (), "cause_not_authoritative")
            return TypedCausalResult("resolved", " | ".join(fact.value for fact in causes), "",
                                     (effects[0].fact_id,) + tuple(fact.fact_id for fact in causes),
                                     "explicit_causal_hyperedge")

        raise AssertionError("validated operator fell through")
