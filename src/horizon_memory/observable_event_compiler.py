# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Warm-path event compilation from authoritative schemas and gauge markers, without an LLM."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal

from .event_compiler import SourceAuthority
from .event_field import EventRecord, Quantity
from .observable_compiler import ObservableGaugeCatalog
from .query_hypotheses import PredicateSchema
from .semantic_tomography import microcitation_ledger


@dataclass(frozen=True, order=True)
class EventSurfaceMarker:
    charge: str
    value: str
    surface: str
    fact_id: int

    def __post_init__(self) -> None:
        allowed = {"polarity": {"positive", "negative"},
                   "modality": {"asserted", "reported", "hypothetical", "ironic", "uncertain"}}
        if self.charge not in allowed or self.value not in allowed[self.charge] \
                or not self.surface.strip() or self.fact_id < 0:
            raise ValueError("invalid event surface marker")


@dataclass(frozen=True)
class ObservableEventCompileResult:
    state: str
    events: tuple[EventRecord, ...]
    reason: str
    citation_count: int
    marker_fact_ids: tuple[int, ...]


class ObservableEventCompiler:
    """Compile known-field events; unknown/conflicting structure abstains atomically."""

    def __init__(self, schemas: tuple[PredicateSchema, ...], predicate_catalog: ObservableGaugeCatalog,
                 event_markers: tuple[EventSurfaceMarker, ...] = ()):
        if not schemas or schemas != tuple(sorted(schemas, key=lambda item: item.predicate)):
            raise ValueError("predicate-sorted schemas are required")
        if event_markers != tuple(sorted(set(event_markers))):
            raise ValueError("event markers must be unique and canonically sorted")
        self._schemas = {schema.predicate: schema for schema in schemas}
        self._predicate_catalog = predicate_catalog
        self._event_markers = event_markers

    @staticmethod
    def _present(text: str, surface: str) -> bool:
        return re.search(rf"(?<!\w){re.escape(surface.casefold())}(?!\w)", text.casefold()) is not None

    def _event_charge(self, text: str, charge: str, default: str) -> tuple[str | None, tuple[int, ...]]:
        found = [(marker.value, marker.fact_id) for marker in self._event_markers
                 if marker.charge == charge and self._present(text, marker.surface)]
        values = {value for value, _ in found}
        if len(values) > 1:
            return None, tuple(sorted({fact_id for _, fact_id in found}))
        return (next(iter(values)) if values else default,
                tuple(sorted({fact_id for _, fact_id in found})))

    @staticmethod
    def _quantities(text: str, schema: PredicateSchema) -> tuple[Quantity, ...] | None:
        quantities = []
        for kind, unit in schema.quantity_kinds:
            matches = re.findall(rf"(?<![\w.])([+-]?\d+(?:\.\d+)?)\s*{re.escape(unit)}(?!\w)",
                                 text, flags=re.IGNORECASE)
            if len(matches) > 1:
                return None
            if matches:
                quantities.append(Quantity(kind, Decimal(matches[0]), unit))
        return tuple(quantities)

    def compile(self, authority: SourceAuthority) -> ObservableEventCompileResult:
        citations = microcitation_ledger(authority.content)
        events = []
        all_marker_fact_ids = set()
        for citation in citations:
            observations = self._predicate_catalog.observe(citation.text)
            predicate_observations = [item for item in observations if item.charge == "predicate"]
            predicates = {item.value for item in predicate_observations}
            if not predicates:
                continue
            if len(predicates) != 1 or next(iter(predicates)) not in self._schemas:
                return ObservableEventCompileResult("abstain", (),
                    "predicate charge is conflicting or unknown", len(citations),
                    tuple(sorted(all_marker_fact_ids)))
            predicate = next(iter(predicates))
            schema = self._schemas[predicate]
            predicate_fact_ids = {fact_id for item in predicate_observations for fact_id in item.fact_ids}
            roles = tuple(sorted(
                (role, value) for role, values in schema.role_values for value in values
                if value.casefold() in citation.text.casefold()
            ))
            if not roles:
                return ObservableEventCompileResult("abstain", (),
                    "known predicate has no authoritative role binding", len(citations),
                    tuple(sorted(all_marker_fact_ids | predicate_fact_ids)))
            polarity, polarity_ids = self._event_charge(citation.text, "polarity", "positive")
            modality, modality_ids = self._event_charge(citation.text, "modality", "asserted")
            quantities = self._quantities(citation.text, schema)
            marker_ids = predicate_fact_ids | set(polarity_ids) | set(modality_ids)
            if polarity is None or modality is None or quantities is None:
                return ObservableEventCompileResult("abstain", (),
                    "event charge is conflicting", len(citations), tuple(sorted(all_marker_fact_ids | marker_ids)))
            all_marker_fact_ids.update(marker_ids)
            surface = predicate_observations[0].surfaces[0]
            events.append(EventRecord(
                event_id=f"{authority.fact_id}:e{len(events) + 1}", scope=authority.scope,
                predicate=predicate, roles=roles, fact_id=authority.fact_id,
                parent_sha256=hashlib.sha256(authority.content.encode()).hexdigest(),
                exact_span=(citation.start, citation.end), event_time=authority.event_time,
                report_time=authority.report_time, version=authority.version,
                polarity=polarity, modality=modality, quantities=quantities,
                surface_predicate=surface, transport_fact_ids=tuple(sorted(marker_ids)),
            ))
        if not events:
            return ObservableEventCompileResult("abstain", (), "no known event charge", len(citations),
                                                tuple(sorted(all_marker_fact_ids)))
        return ObservableEventCompileResult("resolved", tuple(events), "unique observable event syndrome",
                                            len(citations), tuple(sorted(all_marker_fact_ids)))
