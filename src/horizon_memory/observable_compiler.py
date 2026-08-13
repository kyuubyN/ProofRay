# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compile query programs from authoritative observable gauge charges, without an LLM."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .event_compiler import decode_query_proposal
from .event_field import QueryProgram
from .query_hypotheses import PredicateSchema


_CHARGES = frozenset(("operator", "predicate", "target_role", "clock",
                      "quantity_kind", "unit"))
_OPERATORS = frozenset(("project", "exists", "argmax", "argmin", "count_distinct",
                        "sum", "unsupported"))


@dataclass(frozen=True, order=True)
class GaugeMarker:
    charge: str
    value: str
    surface: str
    fact_id: int

    def __post_init__(self) -> None:
        if self.charge not in _CHARGES or not self.value or not self.surface.strip() \
                or self.fact_id < 0:
            raise ValueError("invalid observable gauge marker")
        if self.charge == "operator" and self.value not in _OPERATORS:
            raise ValueError("unknown observable operator")


@dataclass(frozen=True)
class ChargeObservation:
    charge: str
    value: str
    fact_ids: tuple[int, ...]
    surfaces: tuple[str, ...]


@dataclass(frozen=True)
class ObservableCompileResult:
    state: str
    program: QueryProgram | None
    marker_fact_ids: tuple[int, ...]
    reason: str
    observations: tuple[ChargeObservation, ...]


class ObservableGaugeCatalog:
    def __init__(self, markers: tuple[GaugeMarker, ...]):
        if not markers or markers != tuple(sorted(set(markers))):
            raise ValueError("markers must be unique and canonically sorted")
        self._markers = markers

    @staticmethod
    def _present(question: str, surface: str) -> bool:
        # Unicode-aware alphanumeric boundaries; phrases remain exact and auditable.
        pattern = rf"(?<!\w){re.escape(surface.casefold())}(?!\w)"
        return re.search(pattern, question.casefold()) is not None

    def observe(self, question: str) -> tuple[ChargeObservation, ...]:
        if not question.strip() or len(question) > 4096:
            raise ValueError("question is empty or oversized")
        buckets: dict[tuple[str, str], list[GaugeMarker]] = {}
        for marker in self._markers:
            if self._present(question, marker.surface):
                buckets.setdefault((marker.charge, marker.value), []).append(marker)
        return tuple(ChargeObservation(
            charge, value, tuple(sorted({item.fact_id for item in found})),
            tuple(sorted({item.surface for item in found})),
        ) for (charge, value), found in sorted(buckets.items()))


class ObservableQueryCompiler:
    """A generic charge reducer; language-specific surfaces live only in the catalog."""

    def __init__(self, schemas: tuple[PredicateSchema, ...], catalog: ObservableGaugeCatalog):
        if not schemas or schemas != tuple(sorted(schemas, key=lambda item: item.predicate)):
            raise ValueError("predicate-sorted schemas are required")
        self._schemas = {schema.predicate: schema for schema in schemas}
        self._catalog = catalog

    @staticmethod
    def _unique(observations: tuple[ChargeObservation, ...], charge: str) -> str | None:
        values = {item.value for item in observations if item.charge == charge}
        return next(iter(values)) if len(values) == 1 else None

    @staticmethod
    def _operator(observations: tuple[ChargeObservation, ...]) -> str | None:
        """Reduce auxiliary-question markers under an explicit intent dominance lattice."""
        values = {item.value for item in observations if item.charge == "operator"}
        if "unsupported" in values:
            return "unsupported"
        semantic = values - {"exists", "project"}
        if len(semantic) == 1:
            return next(iter(semantic))
        if len(semantic) > 1:
            return None
        if "project" in values:
            return "project"
        return "exists" if values == {"exists"} else None

    def _infer_predicate_from_entities(self, question: str) -> str | None:
        folded = question.casefold()
        candidates = []
        for schema in self._schemas.values():
            if any(value.casefold() in folded for _, values in schema.role_values for value in values):
                candidates.append(schema.predicate)
        return candidates[0] if len(candidates) == 1 else None

    def compile(self, question: str, scope: str) -> ObservableCompileResult:
        if not scope:
            raise ValueError("authoritative scope is required")
        observations = self._catalog.observe(question)
        fact_ids = tuple(sorted({fact_id for item in observations for fact_id in item.fact_ids}))

        operator = self._operator(observations)
        if operator is None:
            return ObservableCompileResult("abstain", None, fact_ids,
                                           "operator charge is absent or conflicting", observations)
        if operator == "unsupported":
            return ObservableCompileResult("unsupported", None, fact_ids,
                                           "observable intent is outside the closed DSL", observations)

        predicate_values = {item.value for item in observations if item.charge == "predicate"}
        predicate = next(iter(predicate_values)) if len(predicate_values) == 1 else None
        if predicate is None and not predicate_values:
            predicate = self._infer_predicate_from_entities(question)
        if predicate not in self._schemas:
            return ObservableCompileResult("abstain", None, fact_ids,
                                           "predicate charge is absent or conflicting", observations)
        schema = self._schemas[predicate]
        folded = question.casefold()
        constraints = [
            {"field": f"role:{role}", "value": value}
            for role, values in schema.role_values for value in values if value.casefold() in folded
        ]
        constraints.sort(key=lambda item: (item["field"], item["value"]))
        data: dict = {"operator": operator, "predicate": predicate, "constraints": constraints}

        if operator in ("project", "argmax", "argmin", "count_distinct"):
            role = self._unique(observations, "target_role")
            if role not in dict(schema.role_values):
                return ObservableCompileResult("abstain", None, fact_ids,
                                               "target role is absent, conflicting or invalid", observations)
            if operator == "count_distinct":
                data.update(distinct_by=f"role:{role}", require_complete=True)
            else:
                data["project"] = f"role:{role}"
        if operator in ("argmax", "argmin"):
            clock = self._unique(observations, "clock")
            if clock not in ("event_time", "report_time"):
                return ObservableCompileResult("abstain", None, fact_ids,
                                               "clock charge is absent or conflicting", observations)
            data.update(clock=clock, require_complete=True)
        if operator == "sum":
            kind = self._unique(observations, "quantity_kind")
            unit = self._unique(observations, "unit")
            if (kind, unit) not in schema.quantity_kinds:
                return ObservableCompileResult("abstain", None, fact_ids,
                                               "quantity charge is absent or invalid", observations)
            data.update(quantity_kind=kind, unit=unit, require_complete=True)

        import json
        program = decode_query_proposal(json.dumps(data, sort_keys=True, separators=(",", ":")), scope)
        return ObservableCompileResult("resolved", program, fact_ids,
                                       "unique observable charge syndrome", observations)
