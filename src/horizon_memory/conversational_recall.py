# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic candidate transport for structured conversational memory.

This module moves only ``FactId`` values.  Speaker, session and observation clocks are
addressing coordinates; they never authorize content or prove an answer.  Every emitted
candidate must still pass :class:`~horizon_memory.routing.HorizonVerifier`.

The generator is deliberately opt-in.  Its defaults are the finite configuration measured on
consumed personal-recall development data, not new package-wide routing defaults.  Applications
must preserve ``RouteDocument.speaker``, ``session_id``, ``sequence`` and, when available,
``event_time`` instead of encoding metadata into source text.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
import math
import re
from typing import Iterable

from .hssd_query_compiler import StructuralHSSDQueryCompiler
from .materialized_proof_pressure_search import MaterializedIndependentHorizonSearchEngine
from .morphological_gauge import observe_gauge_lexical
from .proof_pressure_search import HorizonSearchEngine
from .raw_causal_channels import RawCausalDocument, observe_raw_text
from .routing import (
    Candidate, CandidateGenerator, CandidateList, CausalWeaveGenerator, QueryEnvelope,
    RouteDocument, RoutingIndex,
)


_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
_PLURAL_WH = re.compile(
    r"^\s*(?:what|which)\s+(?P<nominal>.+?)\s+"
    r"(?:has|have|had|does|do|did|is|are|was|were|can|could|will|would)\b",
    re.IGNORECASE,
)
_MONTH_NAMES = (
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December",
)
_MONTHS = {name.casefold(): number for number, name in enumerate(_MONTH_NAMES, 1)}
_MONTH = "|".join(_MONTH_NAMES)
_BLOCKED_HISTORY = re.compile(r"\b(?:as\s+of|since)\b", re.I)
_RANGE_MONTH_FIRST = re.compile(
    rf"\b(?P<m1>{_MONTH})\s+(?P<d1>[0-3]?\d)(?:st|nd|rd|th)?\s+"
    rf"(?:and|to|through|-)\s+(?:(?P<m2>{_MONTH})\s+)?"
    rf"(?P<d2>[0-3]?\d)(?:st|nd|rd|th)?[,]?\s+(?P<year>\d{{4}})\b", re.I)
_RANGE_DAY_FIRST = re.compile(
    rf"\b(?P<d1>[0-3]?\d)(?:st|nd|rd|th)?\s+(?P<m1>{_MONTH})\s+"
    rf"(?:and|to|through|-)\s+(?P<d2>[0-3]?\d)(?:st|nd|rd|th)?\s+"
    rf"(?:(?P<m2>{_MONTH})\s+)?[,]?\s*(?P<year>\d{{4}})\b", re.I)
_DATE_MONTH_FIRST = re.compile(
    rf"\b(?P<month>{_MONTH})\s+(?P<day>[0-3]?\d)(?:st|nd|rd|th)?[,]?\s+"
    rf"(?P<year>\d{{4}})\b", re.I)
_DATE_DAY_FIRST = re.compile(
    rf"\b(?P<day>[0-3]?\d)(?:st|nd|rd|th)?\s+(?P<month>{_MONTH})[,]?\s+"
    rf"(?P<year>\d{{4}})\b", re.I)
_MONTH_YEAR = re.compile(rf"\b(?P<month>{_MONTH})[,]?\s+(?P<year>\d{{4}})\b", re.I)
_MONTH_ONLY = re.compile(rf"\b(?P<month>{_MONTH})\b", re.I)


@dataclass(frozen=True)
class CalendarInterval:
    """One unambiguous query-visible Gregorian interval, expressed as ordinals."""

    start_day: int
    end_day: int
    precision: str
    source_span: tuple[int, int]

    def __post_init__(self) -> None:
        if (self.start_day < 1 or self.end_day < self.start_day
                or self.precision not in {"date", "month", "range"}
                or self.source_span[0] < 0 or self.source_span[1] <= self.source_span[0]):
            raise ValueError("invalid calendar interval")


@dataclass(frozen=True)
class ConversationalRecallConfig:
    """Named finite budgets for the opt-in conversational candidate cascade."""

    depth: int = 32
    rrf_k: int = 60
    person_quota: int = 8
    person_reserve: int = 4
    session_quota: int = 8
    session_reserve: int = 2
    calendar_reserve: int = 4
    neighbor_seed_width: int = 8
    neighbor_radius: int = 2
    exhaustive_reserve: int = 14
    morphological_reserve: int = 4
    person_topic_reserve: int = 0

    def __post_init__(self) -> None:
        if self.depth < 1 or self.rrf_k < 1:
            raise ValueError("conversational recall needs positive depth and RRF constant")
        pairs = (
            (self.person_quota, self.person_reserve),
            (self.session_quota, self.session_reserve),
            (self.session_quota, self.calendar_reserve),
            (self.depth, self.exhaustive_reserve),
            (self.depth, self.morphological_reserve),
        )
        if any(quota < 1 or reserve < 0 or reserve >= quota for quota, reserve in pairs):
            raise ValueError("every conversational reserve must fit its positive quota")
        if not 1 <= self.neighbor_seed_width <= self.depth or self.neighbor_radius < 1:
            raise ValueError("invalid conversational neighbor budget")
        if not 0 <= self.person_topic_reserve < self.depth:
            raise ValueError("invalid person/topic reserve")


@dataclass(frozen=True)
class ConversationalRecallTrace:
    """Scorer-blind observations explaining which candidate stages were applied."""

    eligible_fact_ids: tuple[int, ...]
    calendar_interval: CalendarInterval | None
    calendar_applied: bool
    exhaustive_observation: str | None
    exhaustive_applied: bool
    unique_morphology_applied: bool
    person_topic_applied: bool
    final_order: tuple[int, ...]


def reciprocal_rank_fusion(*orders: Iterable[int], k: int = 60) -> tuple[int, ...]:
    """Fuse unique rankings with deterministic RRF, stable tie rules and no score authority."""
    if k < 1 or not orders:
        raise ValueError("RRF needs a positive constant and at least one ranking")
    normalized = tuple(tuple(order) for order in orders)
    if any(len(order) != len(set(order)) for order in normalized):
        raise ValueError("RRF rankings must be unique")
    ranks = tuple({value: rank for rank, value in enumerate(order, 1)}
                  for order in normalized)
    union = set().union(*normalized)
    sentinel = 10 ** 18
    return tuple(sorted(union, key=lambda value: (
        -sum(1.0 / (k + rank[value]) for rank in ranks if value in rank),
        min(rank.get(value, sentinel) for rank in ranks),
        *(rank.get(value, sentinel) for rank in ranks),
        value,
    )))


def protected_rank_merge(primary: tuple[int, ...], reserve_order: tuple[int, ...], *,
                         quota: int, reserve: int) -> tuple[int, ...]:
    """Protect a primary prefix, then a bounded independent endpoint, then refill."""
    if (quota < 1 or reserve < 0 or reserve >= quota
            or len(primary) != len(set(primary))
            or len(reserve_order) != len(set(reserve_order))):
        raise ValueError("protected merge requires unique rankings and a fitting reserve")
    primary_width = quota - reserve
    result = list(primary[:primary_width])
    for value in reserve_order:
        if len(result) >= quota:
            break
        if value not in result:
            result.append(value)
    for order in (primary, reserve_order):
        for value in order:
            if value not in result:
                result.append(value)
    return tuple(result)


def stable_speaker_partition(ranking: tuple[int, ...], query: str,
                             documents: tuple[RouteDocument, ...]) -> tuple[int, ...]:
    """Move exactly query-named speakers first without aliases or changing internal order."""
    if len(ranking) != len(set(ranking)):
        raise ValueError("speaker partition requires a unique ranking")
    by_id = {document.fact_id: document for document in documents}
    if not set(ranking) <= set(by_id):
        raise ValueError("speaker partition lacks an authoritative document")
    matched = _matched_speaker_ids(query, documents)
    if not matched:
        return ranking
    return tuple(sorted(ranking, key=lambda fact_id: fact_id not in matched))


def _matched_speaker_ids(query: str, documents: tuple[RouteDocument, ...]) -> frozenset[int]:
    query_terms = set(observe_raw_text(query, question=True).lexical)
    return frozenset(
        document.fact_id for document in documents if document.speaker is not None
        and query_terms.intersection(observe_raw_text(document.speaker).lexical)
    )


def expand_session_neighbors(ranking: tuple[int, ...], documents: tuple[RouteDocument, ...], *,
                             seed_width: int, radius: int,
                             max_results: int) -> tuple[int, ...]:
    """Add +/- adjacency inside each seed's session, then refill from the frozen ranking."""
    if (min(seed_width, radius, max_results) < 1 or seed_width > max_results
            or len(ranking) != len(set(ranking))):
        raise ValueError("invalid session-neighbor expansion")
    by_id = {document.fact_id: document for document in documents}
    if not set(ranking) <= set(by_id):
        raise ValueError("neighbor expansion lacks an authoritative document")
    sessions: dict[str, list[RouteDocument]] = {}
    for document in documents:
        sessions.setdefault(document.session_id, []).append(document)
    positions: dict[int, tuple[str, int]] = {}
    by_position: dict[tuple[str, int], int] = {}
    for session_id, members in sessions.items():
        ordered = sorted(members, key=lambda item: (
            item.sequence is None,
            item.sequence if item.sequence is not None else 2 ** 63 - 1,
            item.fact_id,
        ))
        for position, document in enumerate(ordered):
            positions[document.fact_id] = (session_id, position)
            by_position[(session_id, position)] = document.fact_id
    seeds = ranking[:seed_width]
    selected = list(seeds)
    seen = set(seeds)
    for offset in range(1, radius + 1):
        for fact_id in seeds:
            session_id, position = positions[fact_id]
            for neighbor_position in (position - offset, position + offset):
                neighbor = by_position.get((session_id, neighbor_position))
                if neighbor is not None and neighbor not in seen:
                    selected.append(neighbor)
                    seen.add(neighbor)
                    if len(selected) == max_results:
                        return tuple(selected)
    for fact_id in ranking:
        if fact_id not in seen:
            selected.append(fact_id)
            seen.add(fact_id)
            if len(selected) == max_results:
                break
    return tuple(selected)


def _day(year: int, month: str, day_number: int) -> int | None:
    try:
        return date(year, _MONTHS[month.casefold()], day_number).toordinal()
    except (KeyError, ValueError):
        return None


def _month_interval(year: int, month: str, span: tuple[int, int]) -> CalendarInterval | None:
    number = _MONTHS.get(month.casefold())
    if number is None:
        return None
    start = date(year, number, 1)
    end = date(year, number, calendar.monthrange(year, number)[1])
    return CalendarInterval(start.toordinal(), end.toordinal(), "month", span)


def _range_interval(match: re.Match) -> CalendarInterval | None:
    year = int(match.group("year"))
    first_month = match.group("m1")
    second_month = match.group("m2") or first_month
    start = _day(year, first_month, int(match.group("d1")))
    end = _day(year, second_month, int(match.group("d2")))
    if start is None or end is None or end < start:
        return None
    return CalendarInterval(start, end, "range", match.span())


def compile_explicit_calendar_interval(
        query: str, observed_days: Iterable[int] = ()) -> CalendarInterval | None:
    """Compile one explicit English date/month/range; ambiguity fails closed."""
    if not isinstance(query, str) or not query.strip() or _BLOCKED_HISTORY.search(query):
        return None
    ranges = tuple(_RANGE_MONTH_FIRST.finditer(query)) + tuple(
        _RANGE_DAY_FIRST.finditer(query))
    if len(ranges) > 1:
        return None
    if ranges:
        chosen = ranges[0]
        remainder = query[:chosen.start()] + " " * (chosen.end() - chosen.start()) + query[chosen.end():]
        if (_DATE_MONTH_FIRST.search(remainder) or _DATE_DAY_FIRST.search(remainder)
                or _MONTH_YEAR.search(remainder)):
            return None
        return _range_interval(chosen)
    dates = tuple(_DATE_MONTH_FIRST.finditer(query)) + tuple(_DATE_DAY_FIRST.finditer(query))
    if len(dates) != 1:
        if dates:
            return None
    else:
        match = dates[0]
        value = _day(int(match.group("year")), match.group("month"), int(match.group("day")))
        return None if value is None else CalendarInterval(value, value, "date", match.span())
    months = tuple(_MONTH_YEAR.finditer(query))
    if len(months) == 1:
        match = months[0]
        return _month_interval(int(match.group("year")), match.group("month"), match.span())
    if months:
        return None
    bare = tuple(_MONTH_ONLY.finditer(query))
    if len(bare) != 1:
        return None
    month_number = _MONTHS[bare[0].group("month").casefold()]
    valid_days = tuple(day for day in observed_days if isinstance(day, int) and day > 0)
    years = {
        date.fromordinal(day).year for day in valid_days
        if date.fromordinal(day).month == month_number
    }
    if len(years) != 1:
        return None
    return _month_interval(next(iter(years)), bare[0].group("month"), bare[0].span())


def observe_exhaustive_recall(query: str) -> str | None:
    """Observe finite completeness pressure from HSSD or productive English morphology."""
    if not isinstance(query, str) or not query.strip() or len(query) > 4096:
        raise ValueError("query must be bounded non-empty text")
    plan = StructuralHSSDQueryCompiler().compile(query)
    if plan.state == "compiled" and plan.require_complete:
        return "hssd_require_complete"
    match = _PLURAL_WH.search(query)
    if match is None:
        return None
    words = _WORD.findall(match.group("nominal"))
    if not words:
        return None
    head = words[-1].casefold()
    return "productive_plural_wh" if len(head) > 3 and head.endswith("s") \
        and not head.endswith("ss") else None


def _raw_documents(documents: tuple[RouteDocument, ...]) -> tuple[RawCausalDocument, ...]:
    sessions = sorted({document.session_id for document in documents}, key=lambda session_id: (
        min((document.sequence for document in documents
             if document.session_id == session_id and document.sequence is not None),
            default=2 ** 63 - 1),
        session_id,
    ))
    session_index = {session_id: index for index, session_id in enumerate(sessions)}
    members: dict[str, list[RouteDocument]] = {session_id: [] for session_id in sessions}
    for document in documents:
        members[document.session_id].append(document)
    turns = {}
    for session_id, values in members.items():
        for turn, document in enumerate(sorted(values, key=lambda item: (
                item.sequence is None,
                item.sequence if item.sequence is not None else 2 ** 63 - 1,
                item.fact_id))):
            turns[document.fact_id] = turn
    return tuple(RawCausalDocument(
        document.fact_id, document.text, session_index[document.session_id],
        turns[document.fact_id], document.speaker or "",
    ) for document in sorted(documents, key=lambda item: item.fact_id))


def _person_topic_order(base: tuple[int, ...], cavity: tuple[int, ...], query: str,
                        documents: tuple[RouteDocument, ...], *, reserve: int) \
        -> tuple[tuple[int, ...], bool]:
    if reserve == 0 or observe_exhaustive_recall(query) is None:
        return base, False
    query_terms = frozenset(observe_raw_text(query, question=True).lexical)
    speaker_terms = {
        item.fact_id: frozenset(observe_raw_text(item.speaker or "").lexical)
        for item in documents
    }
    matched = frozenset(fid for fid, terms in speaker_terms.items()
                        if query_terms.intersection(terms))
    if not matched:
        return base, False
    named_terms = frozenset(term for fid in matched for term in speaker_terms[fid])
    plan = StructuralHSSDQueryCompiler().compile(query)
    topic = frozenset(plan.address_atoms.lexical).difference(named_terms)
    if not topic:
        return base, False
    overlaps = {
        item.fact_id: len(topic.intersection(observe_raw_text(item.text).lexical))
        for item in documents
    }
    proposal = tuple(sorted(
        (fid for fid in cavity if fid in matched and overlaps[fid] > 0),
        key=lambda fid: -overlaps[fid]))
    if not proposal:
        return base, False
    return protected_rank_merge(base, proposal, quota=max(len(base), reserve + 1),
                                reserve=reserve), True


def _morphological_proposal(query: str, documents: tuple[RouteDocument, ...], *,
                            gauge: bool) -> tuple[int, ...]:
    observe = observe_gauge_lexical if gauge else (
        lambda text: frozenset(observe_raw_text(text).lexical))
    query_terms = observe(query)
    if not query_terms:
        return ()
    document_terms = {document.fact_id: observe(document.text) for document in documents}
    population = max(1, len(document_terms))
    frequency = {term: sum(term in terms for terms in document_terms.values())
                 for term in query_terms}
    chronological = sorted(documents, key=lambda item: (
        item.sequence is None,
        item.sequence if item.sequence is not None else 2 ** 63 - 1,
        item.fact_id,
    ))
    positions = {document.fact_id: position for position, document in enumerate(chronological)}
    scored = []
    for fact_id, terms in document_terms.items():
        overlap = query_terms.intersection(terms)
        if not overlap:
            continue
        score = sum(math.log1p((population + 1) / (frequency[term] + 1))
                    for term in overlap) / math.sqrt(max(1, len(terms)))
        scored.append((score, fact_id))
    return tuple(fact_id for _, fact_id in sorted(
        scored, key=lambda item: (-item[0], positions[item[1]], item[1])))


class ConversationalRecallGenerator(CandidateGenerator):
    """Opt-in zero-model personal-conversation candidate cascade."""

    channel = "conversational_recall"

    def __init__(self, config: ConversationalRecallConfig = ConversationalRecallConfig()):
        if not isinstance(config, ConversationalRecallConfig):
            raise TypeError("config must be ConversationalRecallConfig")
        self.config = config

    def rank(self, query: QueryEnvelope, index: RoutingIndex, *,
             same_session: bool = True) -> tuple[tuple[int, ...], ConversationalRecallTrace]:
        documents = tuple(index.eligible(query, same_session))
        eligible = tuple(sorted(document.fact_id for document in documents))
        if not documents:
            trace = ConversationalRecallTrace((), None, False, None, False, False, False, ())
            return (), trace
        raw = _raw_documents(documents)
        config = self.config
        materialized = MaterializedIndependentHorizonSearchEngine(raw)
        components = materialized.index.components(query.text)
        bm25 = tuple(row.fact_id for row in materialized.index.rank(
            components, (1.0, 0.0, 0.0, 0.0, 0.0, 0.0)) if row.lexical > 0)
        hpps = tuple(item.fact_id for item in materialized.search(
            query.text, max_results=config.depth,
            exploration_reserve=config.depth).admissions)
        fused = reciprocal_rank_fusion(bm25[:config.depth], hpps[:config.depth], k=config.rrf_k)
        topology = HorizonSearchEngine(raw, core_width=1, frontier_width=config.depth)
        cavity = tuple(item.fact_id for item in topology.cavity.rank(
            query.text, lexical_weight=1.0, sublexical_weight=.25,
            speaker_weight=1.0, role_weight=.2))
        semantic_cavity = reciprocal_rank_fusion(
            fused[:config.depth], cavity[:config.depth], k=config.rrf_k)
        speaker_order = stable_speaker_partition(cavity, query.text, documents)
        person = protected_rank_merge(
            semantic_cavity, speaker_order,
            quota=config.person_quota, reserve=config.person_reserve)

        local_index = RoutingIndex(documents)
        session_order = tuple(candidate.fact_id for candidate in CausalWeaveGenerator(
            boundary_fraction=0.0).generate(
                query, local_index, config.depth, same_session=False).candidates)
        person_session = protected_rank_merge(
            person, session_order,
            quota=config.session_quota, reserve=config.session_reserve)

        observed_days = tuple(document.event_time for document in documents
                              if document.event_time is not None)
        interval = compile_explicit_calendar_interval(query.text, observed_days)
        calendar_applied = False
        addressed = person_session
        if interval is not None:
            selected_sessions = {
                document.session_id for document in documents
                if document.event_time is not None
                and interval.start_day <= document.event_time <= interval.end_day
            }
            if selected_sessions:
                calendar_order = tuple(sorted(
                    person, key=lambda fact_id: index.by_id[fact_id].session_id
                    not in selected_sessions))
                addressed = protected_rank_merge(
                    person, calendar_order,
                    quota=config.session_quota, reserve=config.calendar_reserve)
                calendar_applied = True

        context = expand_session_neighbors(
            addressed, documents, seed_width=config.neighbor_seed_width,
            radius=config.neighbor_radius, max_results=config.depth)
        observation = observe_exhaustive_recall(query.text)
        exhaustive_applied = False
        exhaustive = context
        if observation is not None:
            named_cavity = stable_speaker_partition(cavity, query.text, documents)
            if _matched_speaker_ids(query.text, documents):
                exhaustive = protected_rank_merge(
                    context, named_cavity, quota=config.depth,
                    reserve=config.exhaustive_reserve)
                exhaustive_applied = True

        person_topic, person_topic_applied = _person_topic_order(
            exhaustive, cavity, query.text, documents,
            reserve=config.person_topic_reserve)
        gauge = _morphological_proposal(query.text, documents, gauge=True)
        surface = _morphological_proposal(query.text, documents, gauge=False)
        gauge_head = gauge[:config.morphological_reserve]
        surface_head = frozenset(surface[:config.morphological_reserve])
        unique_morphology = bool(gauge_head and any(
            fact_id not in surface_head for fact_id in gauge_head))
        final = protected_rank_merge(
            person_topic, gauge, quota=config.depth,
            reserve=config.morphological_reserve) if unique_morphology else person_topic
        trace = ConversationalRecallTrace(
            eligible, interval, calendar_applied, observation, exhaustive_applied,
            unique_morphology, person_topic_applied, final)
        return final, trace

    def generate(self, query: QueryEnvelope, index: RoutingIndex, limit: int,
                 same_session: bool = True) -> CandidateList:
        if limit < 1:
            raise ValueError("candidate limit must be positive")
        order, _ = self.rank(query, index, same_session=same_session)
        namespace = "scope_session" if same_session else "scope_fallback"
        return CandidateList(tuple(Candidate(
            fact_id, 1.0 / rank, self.channel, rank, namespace)
            for rank, fact_id in enumerate(order[:limit], 1)))


__all__ = [
    "CalendarInterval", "ConversationalRecallConfig", "ConversationalRecallGenerator",
    "ConversationalRecallTrace", "compile_explicit_calendar_interval",
    "expand_session_neighbors", "observe_exhaustive_recall", "protected_rank_merge",
    "reciprocal_rank_fusion", "stable_speaker_partition",
]
