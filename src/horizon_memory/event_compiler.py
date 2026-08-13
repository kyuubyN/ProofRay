# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Strict trust boundary for model-proposed HFEF events and query programs.

Models propose a small JSON grammar.  Authority (scope, FactId, digest, exact source span) is supplied by
the caller and cannot be forged by model output.  This module performs no model invocation.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .event_field import Constraint, EventRecord, Quantity, QueryProgram


_NUMBER = re.compile(r"(?<![\w.])[+-]?\d+(?:\.\d+)?(?![\w.])")
_EVENT_KEYS = frozenset(("event_id", "predicate", "roles", "evidence", "polarity", "modality",
                         "quantities"))
_QUANTITY_KEYS = frozenset(("kind", "value", "unit"))
_PROGRAM_KEYS = frozenset(("operator", "predicate", "constraints", "project", "distinct_by",
                           "quantity_kind", "unit", "clock", "left", "right",
                           "require_complete"))
_CONSTRAINT_KEYS = frozenset(("field", "value"))
_BATCH_KEYS = frozenset(("events",))
_ROLES = frozenset(("agent", "patient", "experiencer", "recipient", "instrument", "location",
                    "source", "destination", "topic", "attribute"))
_MODALITIES = frozenset(("asserted", "reported", "hypothetical", "ironic", "uncertain"))
_PREDICATE = re.compile(r"^[a-z][a-z0-9_:-]*$")


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def strict_json(text: str) -> object:
    return json.loads(text, object_pairs_hook=_strict_object, parse_constant=lambda value: (
        _ for _ in ()).throw(ValueError(f"non-finite JSON constant: {value}")))


def _known(data: dict, allowed: frozenset[str], kind: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"unknown {kind} keys: {unknown}")


@dataclass(frozen=True)
class SourceAuthority:
    scope: str
    fact_id: int
    content: str
    event_time: int | None = None
    report_time: int | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if not self.scope or self.fact_id < 0:
            raise ValueError("scope and non-negative fact_id are required")
        if self.event_time is not None and not isinstance(self.event_time, int):
            raise ValueError("event_time must be an integer clock")
        if self.report_time is not None and not isinstance(self.report_time, int):
            raise ValueError("report_time must be an integer clock")
        if self.version < 1:
            raise ValueError("version must be positive")

    @property
    def parent_sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


def _decimal_present(value: Decimal, span: str) -> bool:
    try:
        return any(Decimal(token) == value for token in _NUMBER.findall(span))
    except InvalidOperation:
        return False


def _decode_event(data: object, authority: SourceAuthority) -> EventRecord:
    if not isinstance(data, dict):
        raise ValueError("event proposal must be an object")
    _known(data, _EVENT_KEYS, "event")
    exact = data.get("evidence")
    if not isinstance(exact, str) or not exact:
        raise ValueError("evidence must be a non-empty exact quote")
    if authority.content.count(exact) != 1:
        raise ValueError("evidence quote must occur exactly once in authoritative content")
    start = authority.content.index(exact)
    end = start + len(exact)
    roles = data.get("roles")
    if not isinstance(roles, dict) or not roles or not all(
            isinstance(key, str) and key and isinstance(value, str) and value
            for key, value in roles.items()):
        raise ValueError("roles must be a non-empty string map")
    if not set(roles) <= _ROLES:
        raise ValueError("unknown semantic role")
    if any(value not in exact for value in roles.values()):
        raise ValueError("role entity is not present in authoritative span")
    quantities = []
    raw_quantities = data.get("quantities", [])
    if not isinstance(raw_quantities, list):
        raise ValueError("quantities must be a list")
    for raw in raw_quantities:
        if not isinstance(raw, dict):
            raise ValueError("quantity must be an object")
        _known(raw, _QUANTITY_KEYS, "quantity")
        try:
            value = Decimal(str(raw["value"]))
            quantity = Quantity(str(raw["kind"]), value, str(raw["unit"]))
        except (KeyError, InvalidOperation) as exc:
            raise ValueError("invalid quantity") from exc
        if not _decimal_present(value, exact):
            raise ValueError("quantity value is not present in authoritative span")
        quantities.append(quantity)
    event_id = data.get("event_id")
    predicate = data.get("predicate")
    if not isinstance(event_id, str) or not event_id or not isinstance(predicate, str) or not predicate:
        raise ValueError("event_id and predicate strings are required")
    if not _PREDICATE.fullmatch(predicate):
        raise ValueError("predicate must be a lowercase lemma identifier")
    if data.get("modality", "asserted") not in _MODALITIES:
        raise ValueError("unknown modality")
    return EventRecord(
        f"{authority.fact_id}:{event_id}", authority.scope, predicate,
        tuple(sorted(roles.items())), authority.fact_id,
        authority.parent_sha256, (start, end),
        event_time=authority.event_time, report_time=authority.report_time,
        version=authority.version, polarity=data.get("polarity", "positive"),
        modality=data.get("modality", "asserted"), quantities=tuple(quantities),
    )


def decode_event_proposal(text: str, authority: SourceAuthority) -> EventRecord | None:
    data = strict_json(text)
    if data == {"state": "abstain"}:
        return None
    return _decode_event(data, authority)


def decode_event_batch(text: str, authority: SourceAuthority, max_events: int = 8) \
        -> tuple[EventRecord, ...]:
    if not 1 <= max_events <= 32:
        raise ValueError("max_events must be inside 1..32")
    data = strict_json(text)
    if data == {"state": "abstain"}:
        return ()
    if not isinstance(data, dict):
        raise ValueError("event batch must be an object")
    _known(data, _BATCH_KEYS, "batch")
    raw = data.get("events")
    if not isinstance(raw, list) or not 1 <= len(raw) <= max_events:
        raise ValueError(f"events must contain 1..{max_events} proposals")
    events = tuple(_decode_event(item, authority) for item in raw)
    if [event.event_id for event in events] != [f"{authority.fact_id}:e{index}"
                                                for index in range(1, len(events) + 1)]:
        raise ValueError("event_ids must be e1,e2,... in span order")
    if len({event.event_id for event in events}) != len(events):
        raise ValueError("event_ids must be unique inside a batch")
    return events


def _decode_program(data: object, scope: str, depth: int = 0) -> QueryProgram:
    if depth > 2 or not isinstance(data, dict):
        raise ValueError("program nesting is invalid")
    _known(data, _PROGRAM_KEYS, "program")
    raw_constraints = data.get("constraints", [])
    if not isinstance(raw_constraints, list):
        raise ValueError("constraints must be a list")
    constraints = []
    for raw in raw_constraints:
        if not isinstance(raw, dict):
            raise ValueError("constraint must be an object")
        _known(raw, _CONSTRAINT_KEYS, "constraint")
        constraints.append(Constraint(str(raw.get("field", "")), str(raw.get("value", ""))))
    operator = data.get("operator")
    predicate = data.get("predicate")
    if not isinstance(operator, str) or not isinstance(predicate, str) or not predicate:
        raise ValueError("operator and predicate strings are required")
    return QueryProgram(
        operator, scope, predicate, tuple(sorted(constraints)), project=data.get("project"),
        distinct_by=data.get("distinct_by", "event_id"),
        quantity_kind=data.get("quantity_kind"), unit=data.get("unit"),
        clock=data.get("clock", "event_time"),
        left=_decode_program(data["left"], scope, depth + 1) if data.get("left") is not None else None,
        right=_decode_program(data["right"], scope, depth + 1) if data.get("right") is not None else None,
        require_complete=data.get("require_complete", False),
    )


def decode_query_proposal(text: str, scope: str) -> QueryProgram | None:
    if not scope:
        raise ValueError("authoritative scope is required")
    data = strict_json(text)
    if data == {"state": "unsupported"}:
        return None
    return _decode_program(data, scope)
