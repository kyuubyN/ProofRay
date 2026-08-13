# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Gold-free finite query hypotheses derived only from an authoritative event-field schema."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from .event_compiler import decode_query_proposal
from .semantic_tomography import SemanticHypothesis


@dataclass(frozen=True)
class PredicateSchema:
    predicate: str
    role_values: tuple[tuple[str, tuple[str, ...]], ...]
    quantity_kinds: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not self.predicate or self.role_values != tuple(sorted(self.role_values)):
            raise ValueError("predicate and canonically sorted role values are required")
        if len(dict(self.role_values)) != len(self.role_values):
            raise ValueError("roles must be unique")
        for role, values in self.role_values:
            if not role or not values or values != tuple(sorted(set(values))):
                raise ValueError("role values must be sorted, unique and non-empty")
        if self.quantity_kinds != tuple(sorted(self.quantity_kinds)) \
                or len(dict(self.quantity_kinds)) != len(self.quantity_kinds):
            raise ValueError("quantity kinds must be unique and canonically sorted")


def _program(operator: str, predicate: str, constraints: list[dict], **fields) -> dict:
    result = {"operator": operator, "predicate": predicate, "constraints": constraints}
    result.update(fields)
    return result


def _charges(program: dict | None) -> tuple[tuple[str, str], ...]:
    if program is None:
        return (("clock", "-"), ("constraint_signature", "-"), ("distinct_by", "-"),
                ("operator", "unsupported"), ("predicate", "-"), ("project", "-"),
                ("quantity", "-"))
    constraints = ";".join(f'{item["field"]}={item["value"]}'
                           for item in program.get("constraints", ())) or "-"
    return tuple(sorted({
        "operator": str(program["operator"]), "predicate": str(program["predicate"]),
        "constraint_signature": constraints, "project": str(program.get("project", "-")),
        "distinct_by": str(program.get("distinct_by", "-")),
        "clock": str(program.get("clock", "-")),
        "quantity": f'{program.get("quantity_kind", "-")}:{program.get("unit", "-")}',
    }.items()))


def generate_query_hypotheses(question: str, schemas: tuple[PredicateSchema, ...],
                              max_hypotheses: int = 256) -> tuple[SemanticHypothesis, ...]:
    """Enumerate the closed DSL from observed schema, never from an answer or benchmark gold.

    Exact entity mentions in the question become authoritative constraints.  Alias/pronoun transport
    belongs to the separate gauge layer; this baseline deliberately does not guess it.
    """
    if not question.strip() or not schemas or schemas != tuple(sorted(schemas, key=lambda item: item.predicate)):
        raise ValueError("question and predicate-sorted schemas are required")
    if not 2 <= max_hypotheses <= 4096:
        raise ValueError("max_hypotheses must be inside 2..4096")
    folded = question.casefold()
    candidates: list[dict | None] = [None]
    for schema in schemas:
        constraints = [
            {"field": f"role:{role}", "value": value}
            for role, values in schema.role_values for value in values
            if value.casefold() in folded
        ]
        constraints.sort(key=lambda item: (item["field"], item["value"]))
        roles = tuple(role for role, _ in schema.role_values)
        candidates.append(_program("exists", schema.predicate, constraints))
        for role in roles:
            candidates.append(_program("project", schema.predicate, constraints,
                                       project=f"role:{role}"))
            candidates.append(_program("count_distinct", schema.predicate, constraints,
                                       distinct_by=f"role:{role}", require_complete=True))
            for operator in ("argmax", "argmin"):
                for clock in ("event_time", "report_time"):
                    candidates.append(_program(operator, schema.predicate, constraints,
                                               project=f"role:{role}", clock=clock,
                                               require_complete=True))
        for kind, unit in schema.quantity_kinds:
            candidates.append(_program("sum", schema.predicate, constraints,
                                       quantity_kind=kind, unit=unit, require_complete=True))
    encoded: dict[str | None, dict | None] = {}
    for candidate in candidates:
        payload = None if candidate is None else json.dumps(
            candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if payload is not None:
            decode_query_proposal(payload, "candidate_validation")
        encoded[payload] = candidate
    if len(encoded) > max_hypotheses:
        raise ValueError("query hypothesis population exceeds the configured limit")
    hypotheses = []
    for payload, candidate in sorted(encoded.items(), key=lambda item: item[0] or ""):
        digest = hashlib.sha256((payload or "unsupported").encode()).hexdigest()[:16]
        hypotheses.append(SemanticHypothesis(f"q:{digest}", _charges(candidate), payload))
    return tuple(hypotheses)
