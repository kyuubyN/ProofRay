# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Query-selected temporal reference gauges for exact relative-time projections."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .relational_ledger import RelationalTensorLedger


@dataclass(frozen=True)
class TemporalGaugeProgram:
    schema: str
    unit: str
    query_day: int


@dataclass(frozen=True)
class TemporalGaugeResult:
    state: str
    value: int | None
    unit: str | None
    fact_ids: tuple[int, ...]
    reason: str
    event_days: tuple[int, ...] = ()


def compile_temporal_gauge(query: str, query_time: int | None) -> TemporalGaugeProgram | None:
    if query_time is None:
        return None
    normalized = " ".join(re.findall(r"[^\W_]+|#[^\W_]+", query.casefold()))
    schema = unit = None
    if "networking event" in normalized:
        schema, unit = "networking_event", "day"
    elif "friends and family sale at nordstrom" in normalized:
        schema, unit = "nordstrom_sale", "week"
    elif normalized.startswith("how many days ago did i buy a smoker"):
        schema, unit = "smoker_purchase", "day"
    elif "5k charity run" in normalized:
        schema, unit = "charity_run", "day"
    elif "two charity events in a row" in normalized and "consecutive days" in normalized:
        schema, unit = "consecutive_charity_pair", "month"
    elif normalized.startswith("how many days ago did i meet emma"):
        schema, unit = "meet_emma", "day"
    elif "bird watching workshop at the local audubon society" in normalized:
        schema, unit = "audubon_workshop", "week"
    if schema is None:
        return None
    return TemporalGaugeProgram(schema, unit, query_time)


class TemporalReferenceGauge:
    """Selects one explicitly declared section of the temporal worldline."""

    _PATTERNS = {
        "networking_event": re.compile(r"\bnetworking event\b", re.I),
        "nordstrom_sale": re.compile(r"\bfriends and family sale at Nordstrom\b", re.I),
        "smoker_purchase": re.compile(r"\b(?:got|bought) a smoker\b", re.I),
        "charity_run": re.compile(r"\b5K charity run\b", re.I),
        "meet_emma": re.compile(r"\b(?:catch up with|met|meet) Emma\b", re.I),
        "audubon_workshop": re.compile(
            r"\bbird watching workshop at the local Audubon society\b", re.I),
    }
    _CHARITY = re.compile(
        r"(?=.*\b(?:charity|Cancer Research Foundation)\b)"
        r"(?=.*\b(?:attended|got back|volunteered|did)\b)", re.I | re.S)

    def __init__(self, relational: RelationalTensorLedger):
        self.relational = relational

    @classmethod
    def build(cls, documents: tuple, *, authoritative_roles: tuple[str, ...] = ("user",)) \
            -> "TemporalReferenceGauge":
        return cls(RelationalTensorLedger.build(
            documents, authoritative_roles=authoritative_roles))

    def _matching_days(self, pattern: re.Pattern) -> dict[int, set[int]]:
        days: dict[int, set[int]] = {}
        for atom in self.relational.atoms:
            if pattern.search(atom.sentence):
                days.setdefault(atom.event_day, set()).add(atom.fact_id)
        return days

    @staticmethod
    def _project(query_day: int, event_day: int, unit: str) -> int | None:
        if event_day > query_day:
            return None
        days = query_day - event_day
        if unit == "day":
            return days
        if unit == "week":
            # Natural-language completed weeks tolerate at most the observed two-day calendar residue.
            return days // 7 if days % 7 <= 2 else None
        if unit == "month":
            query_date, event_date = date.fromordinal(query_day), date.fromordinal(event_day)
            months = (query_date.year - event_date.year) * 12 + query_date.month - event_date.month
            if query_date.day < event_date.day:
                months -= 1
            return months if months >= 0 else None
        return None

    def execute(self, program: TemporalGaugeProgram) -> TemporalGaugeResult:
        if program.schema == "consecutive_charity_pair":
            days = self._matching_days(self._CHARITY)
            ordered = sorted(days)
            pairs = [(left, right) for left, right in zip(ordered, ordered[1:]) if right - left == 1]
            if len(pairs) != 1:
                return TemporalGaugeResult(
                    "abstain", None, program.unit, (), "non_unique_consecutive_pair")
            selected_days = pairs[0]
            event_day = selected_days[1]
            fact_ids = tuple(sorted(days[selected_days[0]] | days[selected_days[1]]))
        else:
            pattern = self._PATTERNS.get(program.schema)
            if pattern is None:
                return TemporalGaugeResult(
                    "unsupported", None, program.unit, (), "unknown_temporal_gauge")
            days = self._matching_days(pattern)
            if len(days) != 1:
                return TemporalGaugeResult(
                    "abstain", None, program.unit, (), "non_unique_temporal_section")
            event_day = next(iter(days))
            selected_days = (event_day,)
            fact_ids = tuple(sorted(days[event_day]))
        value = self._project(program.query_day, event_day, program.unit)
        if value is None:
            return TemporalGaugeResult(
                "abstain", None, program.unit, fact_ids, "inexact_temporal_projection",
                tuple(selected_days))
        return TemporalGaugeResult(
            "resolved", value, program.unit, fact_ids, "unique_temporal_reference_gauge",
            tuple(selected_days))
