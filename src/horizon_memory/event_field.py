# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""HFEF V29 — incremental typed event field and domain-independent query algebra.

This module intentionally contains no natural-language patterns.  A local model or another compiler may
propose EventRecord and QueryProgram values, but deterministic validation and execution live here.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal


Operator = Literal["project", "exists", "argmax", "argmin", "count_distinct", "sum", "diff"]
Polarity = Literal["positive", "negative"]


def _pairs(values: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(values))


@dataclass(frozen=True)
class Quantity:
    kind: str
    value: Decimal
    unit: str

    def __post_init__(self) -> None:
        if not self.kind or not self.unit or not self.value.is_finite():
            raise ValueError("quantity requires finite value, kind and unit")


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    scope: str
    predicate: str
    roles: tuple[tuple[str, str], ...]
    fact_id: int
    parent_sha256: str
    exact_span: tuple[int, int]
    event_time: int | None = None
    report_time: int | None = None
    version: int = 1
    polarity: Polarity = "positive"
    modality: str = "asserted"
    quantities: tuple[Quantity, ...] = ()
    surface_predicate: str | None = None
    transport_fact_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.event_id or not self.scope or not self.predicate or self.fact_id < 0:
            raise ValueError("event identity, scope, predicate and non-negative fact_id are required")
        if self.roles != _pairs(self.roles) or len(dict(self.roles)) != len(self.roles):
            raise ValueError("roles must be unique and canonically sorted")
        if self.version < 1 or self.polarity not in ("positive", "negative"):
            raise ValueError("invalid version or polarity")
        if self.event_time is not None and not isinstance(self.event_time, int):
            raise ValueError("event_time must be an integer clock")
        if self.report_time is not None and not isinstance(self.report_time, int):
            raise ValueError("report_time must be an integer clock")
        if len(self.parent_sha256) != 64 or any(c not in "0123456789abcdef"
                                                for c in self.parent_sha256):
            raise ValueError("parent_sha256 must be lowercase SHA-256")
        if len(self.exact_span) != 2 or self.exact_span[0] < 0 \
                or self.exact_span[1] < self.exact_span[0]:
            raise ValueError("invalid exact_span")
        keys = [quantity.kind for quantity in self.quantities]
        if len(keys) != len(set(keys)):
            raise ValueError("quantity kinds must be unique inside an event")
        if self.surface_predicate is not None and not self.surface_predicate:
            raise ValueError("surface_predicate cannot be empty")
        if any(fact_id < 0 for fact_id in self.transport_fact_ids):
            raise ValueError("transport FactIds must be non-negative")

    def role(self, name: str) -> str | None:
        return dict(self.roles).get(name)

    def quantity(self, kind: str) -> Quantity | None:
        return next((value for value in self.quantities if value.kind == kind), None)

    def charges(self) -> tuple[str, ...]:
        charges = {
            f"event:{self.event_id}", f"predicate:{self.predicate}",
            f"polarity:{self.polarity}", f"modality:{self.modality}",
            f"version:{self.version}",
        }
        charges.update(f"role:{role}={entity}" for role, entity in self.roles)
        charges.update(f"quantity:{q.kind}={q.value}:{q.unit}" for q in self.quantities)
        if self.surface_predicate is not None:
            charges.add(f"surface_predicate:{self.surface_predicate}")
        charges.update(f"transport_fact:{fact_id}" for fact_id in self.transport_fact_ids)
        if self.event_time is not None:
            charges.add(f"event_time:{self.event_time}")
        if self.report_time is not None:
            charges.add(f"report_time:{self.report_time}")
        return tuple(sorted(charges))


@dataclass(frozen=True, order=True)
class Constraint:
    field: str
    value: str

    def __post_init__(self) -> None:
        if not self.field or not self.value:
            raise ValueError("constraint field and value are required")


@dataclass(frozen=True)
class QueryProgram:
    operator: Operator
    scope: str
    predicate: str
    constraints: tuple[Constraint, ...] = ()
    project: str | None = None
    distinct_by: str = "event_id"
    quantity_kind: str | None = None
    unit: str | None = None
    clock: Literal["event_time", "report_time"] = "event_time"
    left: "QueryProgram | None" = None
    right: "QueryProgram | None" = None
    require_complete: bool = False

    def __post_init__(self) -> None:
        if self.operator not in ("project", "exists", "argmax", "argmin", "count_distinct",
                                 "sum", "diff"):
            raise ValueError("unknown operator")
        if not self.scope or not self.predicate:
            raise ValueError("scope and predicate are required")
        if self.constraints != tuple(sorted(self.constraints)):
            raise ValueError("constraints must be canonically sorted")
        if self.operator == "project" and not self.project:
            raise ValueError("project operator requires a projection")
        if self.operator == "sum" and (not self.quantity_kind or not self.unit):
            raise ValueError("sum requires quantity_kind and unit")
        if self.operator in ("sum", "count_distinct", "argmax", "argmin") \
                and not self.require_complete:
            raise ValueError(f"{self.operator} requires a completeness certificate")
        if self.operator == "diff" and (self.left is None or self.right is None or not self.unit):
            raise ValueError("diff requires left, right and unit")
        if self.operator != "diff" and (self.left is not None or self.right is not None):
            raise ValueError("nested programs are only valid for diff")

    def canonical_sha256(self) -> str:
        def encode(program: QueryProgram | None):
            if program is None:
                return None
            return {
                "operator": program.operator, "scope": program.scope,
                "predicate": program.predicate,
                "constraints": [(c.field, c.value) for c in program.constraints],
                "project": program.project, "distinct_by": program.distinct_by,
                "quantity_kind": program.quantity_kind, "unit": program.unit,
                "clock": program.clock, "left": encode(program.left),
                "right": encode(program.right), "require_complete": program.require_complete,
            }
        payload = json.dumps(encode(self), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class CompletenessCertificate:
    scope: str
    predicate: str
    fiber_generation: int
    event_count: int
    event_ids_sha256: str


@dataclass(frozen=True)
class FieldResult:
    state: Literal["resolved", "abstain", "unsupported"]
    value: object | None
    fact_ids: tuple[int, ...]
    reason: str
    scanned_events: int
    program_sha256: str


class IncrementalEventField:
    """Materialized event fibers updated at ingestion and reused across queries."""

    _UNIT_TO_DAYS = {
        "day": Decimal(1), "days": Decimal(1),
        "week": Decimal(7), "weeks": Decimal(7),
    }

    def __init__(self) -> None:
        self._events: dict[str, EventRecord] = {}
        self._fact_ids: set[int] = set()
        self._by_fiber: dict[tuple[str, str], set[str]] = {}
        self._by_role: dict[tuple[str, str, str, str], set[str]] = {}
        self._fiber_generation: dict[tuple[str, str], int] = {}
        self._certificates: dict[tuple[str, str], CompletenessCertificate] = {}

    @property
    def event_count(self) -> int:
        return len(self._events)

    def ingest(self, event: EventRecord) -> None:
        if event.event_id in self._events or event.fact_id in self._fact_ids:
            raise ValueError("event_id and fact_id must be unique")
        self._events[event.event_id] = event
        self._fact_ids.add(event.fact_id)
        fiber = (event.scope, event.predicate)
        self._by_fiber.setdefault(fiber, set()).add(event.event_id)
        for role, entity in event.roles:
            self._by_role.setdefault((event.scope, event.predicate, role, entity), set()).add(
                event.event_id)
        self._fiber_generation[fiber] = self._fiber_generation.get(fiber, 0) + 1
        self._certificates.pop(fiber, None)

    def certify_complete(self, scope: str, predicate: str) -> CompletenessCertificate:
        fiber = (scope, predicate)
        ids = tuple(sorted(self._by_fiber.get(fiber, ())))
        digest = hashlib.sha256("\x1f".join(ids).encode()).hexdigest()
        certificate = CompletenessCertificate(
            scope, predicate, self._fiber_generation.get(fiber, 0), len(ids), digest)
        self._certificates[fiber] = certificate
        return certificate

    def _certificate_valid(self, scope: str, predicate: str) -> bool:
        fiber = (scope, predicate)
        certificate = self._certificates.get(fiber)
        return bool(certificate and
                    certificate.fiber_generation == self._fiber_generation.get(fiber, 0))

    @staticmethod
    def _matches(event: EventRecord, constraint: Constraint) -> bool:
        if constraint.field.startswith("role:"):
            return event.role(constraint.field.split(":", 1)[1]) == constraint.value
        if constraint.field == "polarity":
            return event.polarity == constraint.value
        if constraint.field == "modality":
            return event.modality == constraint.value
        if constraint.field == "event_id":
            return event.event_id == constraint.value
        return False

    def _candidates(self, program: QueryProgram) -> tuple[tuple[EventRecord, ...], int]:
        fiber = (program.scope, program.predicate)
        ids = set(self._by_fiber.get(fiber, ()))
        for constraint in program.constraints:
            if constraint.field == "event_id":
                ids &= {constraint.value} if constraint.value in self._events else set()
            if constraint.field.startswith("role:"):
                role = constraint.field.split(":", 1)[1]
                ids &= self._by_role.get((program.scope, program.predicate, role,
                                          constraint.value), set())
        events = tuple(self._events[event_id] for event_id in sorted(ids))
        return tuple(event for event in events
                     if all(self._matches(event, item) for item in program.constraints)), len(events)

    @staticmethod
    def _project(event: EventRecord, field: str) -> object | None:
        if field.startswith("role:"):
            return event.role(field.split(":", 1)[1])
        if field.startswith("quantity:"):
            return event.quantity(field.split(":", 1)[1])
        if field in ("event_id", "predicate", "event_time", "report_time", "version",
                     "polarity", "modality"):
            return getattr(event, field)
        return None

    @classmethod
    def _convert(cls, value: Decimal, source: str, target: str) -> Decimal | None:
        if source == target:
            return value
        if source in cls._UNIT_TO_DAYS and target in cls._UNIT_TO_DAYS:
            return value * cls._UNIT_TO_DAYS[source] / cls._UNIT_TO_DAYS[target]
        return None

    def _unique_event(self, program: QueryProgram) -> tuple[EventRecord | None, int, str]:
        events, scanned = self._candidates(program)
        if len(events) != 1:
            return None, scanned, "missing_unique_event" if not events else "ambiguous_event"
        return events[0], scanned, "unique_event"

    def execute(self, program: QueryProgram) -> FieldResult:
        digest = program.canonical_sha256()
        if program.operator == "diff":
            left, left_scanned, left_reason = self._unique_event(program.left)  # type: ignore[arg-type]
            right, right_scanned, right_reason = self._unique_event(program.right)  # type: ignore[arg-type]
            scanned = left_scanned + right_scanned
            if left is None or right is None:
                return FieldResult("abstain", None, (), f"{left_reason}:{right_reason}", scanned, digest)
            a, b = getattr(left, program.left.clock), getattr(right, program.right.clock)  # type: ignore[union-attr]
            if a is None or b is None or program.unit not in self._UNIT_TO_DAYS:
                return FieldResult("abstain", None, (), "missing_or_inexact_clock", scanned, digest)
            value = Decimal(b - a) / self._UNIT_TO_DAYS[program.unit]
            return FieldResult("resolved", value, (left.fact_id, right.fact_id),
                               "exact_clock_difference", scanned, digest)

        events, scanned = self._candidates(program)
        if program.require_complete and not self._certificate_valid(program.scope, program.predicate):
            return FieldResult("abstain", None, (), "missing_completeness_certificate",
                               scanned, digest)
        fact_ids = tuple(event.fact_id for event in events)
        if program.operator == "exists":
            if events:
                return FieldResult("resolved", True, fact_ids, "positive_witness_exists", scanned, digest)
            if not self._certificate_valid(program.scope, program.predicate):
                return FieldResult("abstain", None, (), "missing_negative_completeness_certificate",
                                   scanned, digest)
            return FieldResult("resolved", False, (), "closed_fiber_absence", scanned, digest)
        if program.operator == "count_distinct":
            values = {self._project(event, program.distinct_by) for event in events}
            if None in values:
                return FieldResult("abstain", None, fact_ids, "missing_distinct_key", scanned, digest)
            return FieldResult("resolved", len(values), fact_ids, "closed_fiber_count", scanned, digest)
        if program.operator == "sum":
            total = Decimal(0)
            for event in events:
                quantity = event.quantity(program.quantity_kind or "")
                converted = (self._convert(quantity.value, quantity.unit, program.unit or "")
                             if quantity else None)
                if converted is None:
                    return FieldResult("abstain", None, fact_ids, "missing_or_inexact_quantity",
                                       scanned, digest)
                total += converted
            return FieldResult("resolved", total, fact_ids, "closed_fiber_sum", scanned, digest)
        if program.operator in ("argmax", "argmin"):
            available = [event for event in events if getattr(event, program.clock) is not None]
            if not available:
                return FieldResult("abstain", None, fact_ids, "missing_clock", scanned, digest)
            key = lambda event: (getattr(event, program.clock), event.event_id)
            selected = (max if program.operator == "argmax" else min)(available, key=key)
            value = self._project(selected, program.project or "event_id")
            if value is None:
                return FieldResult("unsupported", None, (selected.fact_id,), "unknown_projection",
                                   scanned, digest)
            return FieldResult("resolved", value, (selected.fact_id,),
                               f"{program.operator}_{program.clock}", scanned, digest)
        if program.operator == "project":
            if len(events) != 1:
                return FieldResult("abstain", None, fact_ids,
                                   "missing_unique_event" if not events else "ambiguous_event",
                                   scanned, digest)
            value = self._project(events[0], program.project or "")
            if value is None:
                return FieldResult("unsupported", None, fact_ids, "unknown_projection", scanned, digest)
            return FieldResult("resolved", value, fact_ids, "unique_typed_projection", scanned, digest)
        return FieldResult("unsupported", None, (), "unknown_operator", scanned, digest)
