# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""HPC — Horizon Proof Codec: query-conditioned, extractive, proof-carrying evidence."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import quote, unquote

from .evidence import EvidenceItem, EvidencePack
from .boundary_ledger import BoundaryFiberLedger, BoundaryProgram, compile_boundary_program
from .constraint_ledger import (
    ConstraintClosureLedger, ConstraintProgram, compile_constraint_program,
)
from .measurement_ledger import (
    AggregateProgram, CountProgram, EventLedger, compile_aggregate_program, compile_count_program,
)
from .relational_ledger import (
    RelationalProgram, RelationalTensorLedger, compile_relational_program,
)
from .worldline_ledger import (
    StateWorldlineLedger, WorldlineProgram, compile_worldline_program,
)
from .temporal_gauge import (
    TemporalGaugeProgram, TemporalReferenceGauge, compile_temporal_gauge,
)


_TOKEN = re.compile(r"[^\W_]+|#[^\W_]+", re.UNICODE)
_BOUNDARY = re.compile(r"(?<=[.!?])(?:\s+|$)|\n+")
_NUMBER = re.compile(r"(?<!\w)[+-]?(?:[$€£]\s*)?\d+(?:[.,]\d+)*(?:%|\s*(?:days?|weeks?|months?|years?|dias?|semanas?|meses?|anos?))?", re.I)
_QUOTE = re.compile(r"[\"'`]([^\"'`]{1,100})[\"'`]")
# "no" deliberately excluded: it collides with the PT preposition "no" (em+o, e.g. "no sabado"),
# which every PT sentence using it as a preposition would otherwise flag as a spurious negation
# charge -- see `raw_causal_channels.py`'s own `_EN_NO_COLLOCATIONS` comment for the full history
# (2026-08-20). This module works on a bag of tokens with no position information, so the same
# collocation/context disambiguation used there isn't cheaply reusable here; the conservative fix
# is to not track "no" as a semantic charge at all rather than risk either false-negative English
# negation loss-tracking or false-positive PT compression-fidelity failures.
_NEGATION = frozenset(("not", "never", "without", "nao", "não", "nunca", "sem"))
_STOP = frozenset((
    "a", "an", "and", "are", "as", "at", "be", "by", "did", "do", "for", "from", "had",
    "has", "have", "how", "i", "in", "is", "it", "of", "on", "or", "the", "to", "was",
    "were", "what", "when", "where", "which", "who", "with", "you", "e", "o", "a", "os",
    "as", "de", "da", "do", "das", "dos", "em", "no", "na", "nos", "nas", "para", "por",
    "que", "um", "uma", "como", "qual", "quando", "onde", "eu", "voce", "você", "isso",
))
_UNIT = re.compile(r"\b(day|days|week|weeks|month|months|year|years|dia|dias|semana|semanas|mes|meses|ano|anos)\b", re.I)
_EXPLICIT_DATE = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2})(?:st|nd|rd|th)?\b", re.I)
_MONTHS = {name.casefold(): number for number, name in enumerate(
    ("January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"), 1)}
_HOLIDAYS = {"valentine's day": (2, 14), "valentines day": (2, 14)}


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(token.casefold() for token in _TOKEN.findall(text))


def _term_key(token: str) -> str:
    """Small deterministic morphology bridge; it is an address normalizer, not semantics."""
    token = token.casefold().removesuffix("'s")
    if len(token) > 5 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 5 and token.endswith("ing"):
        stem = token[:-3]
        return stem[:-1] if len(stem) > 3 and stem[-1:] == stem[-2:-1] else stem
    if len(token) > 4 and token.endswith("ed"):
        stem = token[:-2]
        return stem[:-1] + "y" if stem.endswith("i") else stem
    if len(token) > 4 and token.endswith("es") and not token.endswith(("ses", "xes")):
        return token[:-2]
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _terms(text: str) -> frozenset[str]:
    return frozenset(_term_key(token) for token in _tokens(text)
                     if token not in _STOP and len(token) > 1)


def semantic_charges(text: str) -> tuple[str, ...]:
    """Loss-sensitive atoms whose disappearance must remain measurable."""
    tokens = set(_tokens(text))
    charges = {f"neg:{token}" for token in tokens & _NEGATION}
    charges.update(f"num:{match.group(0).casefold().replace(' ', '')}"
                   for match in _NUMBER.finditer(text))
    charges.update(f"tag:{token}" for token in tokens if token.startswith("#"))
    charges.update(f"quote:{' '.join(_tokens(match.group(1)))}" for match in _QUOTE.finditer(text))
    return tuple(sorted(charge for charge in charges if not charge.endswith(":")))


def _units(text: str) -> tuple[tuple[int, int, str], ...]:
    result = []
    start = 0
    for match in _BOUNDARY.finditer(text):
        end = match.start()
        left, right = start, end
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        if right > left:
            result.append((left, right, text[left:right]))
        start = match.end()
    if start < len(text):
        left, right = start, len(text)
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        if right > left:
            result.append((left, right, text[left:right]))
    return tuple(result) or ((0, len(text), text),)


def _iso_day(ordinal: int | None) -> str:
    if ordinal is None:
        return "unknown"
    try:
        return date.fromordinal(ordinal).isoformat()
    except ValueError:
        return str(ordinal)


def compile_query_equation(query: str, query_time: int | None = None) -> str:
    """Compile a user question into a fixed, non-answer-bearing operation descriptor."""
    normalized = " ".join(_tokens(query))
    unit_match = _UNIT.search(query)
    unit = unit_match.group(1).casefold() if unit_match else "unspecified"
    duration_unit = r"(?:days?|weeks?|months?|years?|dias?|semanas?|meses?|anos?)"
    if normalized.startswith("when ") or normalized.startswith("quando "):
        operation = "event_date"
    elif re.search(r"\bhow many\b.*\bago\b|\bquant[oa]s?\b.*\batras\b", normalized):
        operation = "relative_time"
    elif re.search(rf"\bhow many {duration_unit} (?:had |have )?passed since\b", normalized) \
            and " when " not in normalized:
        operation = "relative_time"
    elif any(marker in normalized for marker in
             ("how many days passed", "how many days did it take", "how long",
              "days between", "weeks passed", "dias passaram", "quanto tempo")):
        operation = "interval"
    elif re.search(rf"\bhow many {duration_unit}\b.*(?:\bbetween\b|\bbefore\b.*\bdid\b|"
                   rf"\bpassed since\b.*\bwhen\b|\bhave i been\b.*\bwhen\b)", normalized):
        operation = "interval"
    elif re.search(rf"\bhow many {duration_unit} did i spend\b", normalized):
        operation = "sum"
    elif re.search(r"\bhow many years will i be when\b", normalized):
        operation = "age_at_event"
    elif any(marker in normalized for marker in
             ("how much total", "total distance", "total money", "in total", "em total",
              "quanto no total")):
        operation = "sum"
    elif re.search(r"\bhow much\b.*\b(?:raise|raised|spend|spent|pay|paid)\b", normalized):
        operation = "sum"
    elif any(marker in normalized for marker in ("current", "currently", "latest", "most recent",
                                                  "atualmente", "mais recente")):
        operation = "latest_state"
    elif normalized.startswith("how many") or normalized.startswith("quantos") \
            or normalized.startswith("quantas"):
        operation = "count_distinct"
    elif normalized.startswith(("did ", "do ", "does ", "is ", "was ", "were ",
                                "eu ", "foi ", "era ")):
        operation = "boolean_or_state"
    else:
        operation = "lookup"
    anchor = _iso_day(query_time) if query_time is not None else "unknown"
    return f"op={operation};unit={unit};query_date={anchor};use only cited operands;abstain if insufficient"


def _interval_anchors(query: str) -> tuple[str, str] | tuple[()]:
    """Extract the two event descriptions without deciding their dates or answer."""
    compact = " ".join(query.strip().split()).rstrip("?")
    patterns = (
        r"\bbetween\s+(.+?)\s+and\s+(.+)$",
        r"\bfrom\s+(.+?)\s+to\s+(.+)$",
        r"\bsince\s+(.+?)\s+when\s+(.+)$",
    )
    for pattern in patterns:
        match = re.search(pattern, compact, re.I)
        if match and all(_terms(group) for group in match.groups()):
            return match.group(1), match.group(2)
    match = re.search(r"\b(.+?)\s+after\s+(.+)$", compact, re.I)
    if match:
        left = re.sub(r"^how many \w+ (?:did it take )?(?:for (?:me|us) )?", "",
                      match.group(1), flags=re.I)
        if _terms(left) and _terms(match.group(2)):
            return left, match.group(2)
    match = re.search(r"\bbefore\s+(.+?)\s+did\s+(?:i|we)\s+(.+)$", compact, re.I)
    if match and all(_terms(group) for group in match.groups()):
        return match.group(1), match.group(2)
    match = re.search(r"\bhave\s+(?:i|we)\s+been\s+(.+?)\s+when\s+(.+)$", compact, re.I)
    if match and all(_terms(group) for group in match.groups()):
        return match.group(1), match.group(2)
    return ()


def _relative_anchor(query: str) -> str:
    """Keep the questioned event predicate; subordinate clauses are disambiguators, not targets."""
    compact = " ".join(query.strip().split()).rstrip("?")
    match = re.search(
        r"\b(?:how many\s+\w+\s+ago\s+)?did\s+(?:i|we)\s+(.+?)(?:\s+when\s+|\s+while\s+|\s+where\s+|$)",
        compact, re.I,
    )
    return match.group(1) if match and _terms(match.group(1)) else query


def _relative_context_anchor(query: str) -> str | None:
    compact = " ".join(query.strip().split()).rstrip("?")
    match = re.search(r"\b(?:when|while)\s+(.+)$", compact, re.I)
    return match.group(1) if match and _terms(match.group(1)) else None


@dataclass(frozen=True)
class CompressionReport:
    input_items: int
    output_items: int
    input_chars: int
    output_chars: int
    input_charges: int
    preserved_charges: int
    query_charges: int
    preserved_query_charges: int
    exact_spans: bool


@dataclass(frozen=True)
class ExactExecution:
    state: str  # resolved | abstain | unsupported
    answer: str | None
    citation_labels: tuple[str, ...]
    reason: str
    fact_ids: tuple[int, ...] = ()


def _ordinal_from_item(item: EvidenceItem) -> date | None:
    if item.event_time is None:
        return None
    try:
        return date.fromordinal(item.event_time)
    except ValueError:
        return None


def _refined_ordinal_from_item(item: EvidenceItem) -> date | None:
    """Refine a session header with an explicit clock carried by the exact evidence span."""
    base = _ordinal_from_item(item)
    if base is None:
        return None
    content = item.content or ""
    # A bare calendar date may name an object (for example, "the March 15th issue") rather
    # than the event.  Only an unambiguous deictic is transported here; typed ledgers bind
    # explicit dates to predicates separately.
    if re.search(r"\byesterday\b", content, re.I):
        return date.fromordinal(base.toordinal() - 1)
    return base


def _float_fields(fields: dict[str, str], name: str) -> tuple[float, ...]:
    try:
        return tuple(float(value) for value in fields.get(name, "").split(",") if value)
    except ValueError:
        return ()


def _confident(scores: tuple[float, ...], margins: tuple[float, ...], count: int) -> bool:
    """Fail closed unless each operand has lexical support and a separated runner-up."""
    return (len(scores) >= count and len(margins) >= count and
            all(score >= 2.0 for score in scores[:count]) and
            all(margin >= 0.5 for margin in margins[:count]))


def execute_exact(pack: EvidencePack, event_ledger: EventLedger | None = None,
                  worldline_ledger: StateWorldlineLedger | None = None,
                  boundary_ledger: BoundaryFiberLedger | None = None,
                  relational_ledger: RelationalTensorLedger | None = None,
                  temporal_gauge: TemporalReferenceGauge | None = None,
                  constraint_ledger: ConstraintClosureLedger | None = None) -> ExactExecution:
    """Resolve only closed calendar operations; uncertainty remains explicit."""
    if not pack.query_plan:
        return ExactExecution("unsupported", None, (), "missing_plan")
    fields = dict(part.split("=", 1) for part in pack.query_plan.split(";") if "=" in part)
    operation, unit = fields.get("op"), fields.get("unit")
    focus = tuple(label for label in fields.get("focus", "").split(",") if label)
    by_label = {label: item for label, item in zip(pack.citation_labels, pack.items)}
    primary = by_label.get(focus[0]) if focus else None

    if fields.get("constraint_schema") and constraint_ledger is not None:
        try:
            constraint_query_day = int(fields["constraint_query_day"]) \
                if fields.get("constraint_query_day") else None
        except ValueError:
            return ExactExecution("unsupported", None, (), "invalid_constraint_clock")
        result = constraint_ledger.execute(ConstraintProgram(
            fields["constraint_schema"], constraint_query_day))
        return ExactExecution(result.state, result.answer, (), result.reason, result.fact_ids)

    if fields.get("worldline_schema"):
        if worldline_ledger is None:
            return ExactExecution("unsupported", None, (), "missing_worldline_ledger")
        result = worldline_ledger.execute(WorldlineProgram(
            fields["worldline_schema"], unquote(fields.get("worldline_subject", "")),
            unquote(fields.get("worldline_choice_a", "")),
            unquote(fields.get("worldline_choice_b", "")), fields.get("worldline_direction", "")))
        return ExactExecution(result.state, result.answer, (), result.reason, result.fact_ids)

    if fields.get("boundary_schema"):
        if boundary_ledger is None:
            return ExactExecution("unsupported", None, (), "missing_boundary_ledger")
        try:
            start_day = int(fields["boundary_start"]) if fields.get("boundary_start") else None
            end_day = int(fields["boundary_end"]) if fields.get("boundary_end") else None
        except ValueError:
            return ExactExecution("unsupported", None, (), "invalid_boundary_clock")
        result = boundary_ledger.execute(BoundaryProgram(
            fields["boundary_schema"], start_day, end_day,
            tuple(unquote(value) for value in fields.get("boundary_operands", "").split("|")
                  if value)))
        return ExactExecution(result.state, result.answer, (), result.reason, result.fact_ids)

    prior_bindings = tuple(label for label in fields.get("bindings", "").split(",") if label)
    prior_days = tuple(_ordinal_from_item(by_label[label]) for label in prior_bindings
                       if label in by_label)
    prior_binding_closed = (len(prior_bindings) == 2 and _confident(
        _float_fields(fields, "binding_scores"), _float_fields(fields, "binding_margins"), 2) and
        len(prior_days) == 2 and None not in prior_days and prior_days[0] != prior_days[1])
    if (fields.get("relational_anchor_a") and fields.get("relational_anchor_b") and
            relational_ledger is not None and not prior_binding_closed):
        program = RelationalProgram(
            fields.get("relational_unit", unit or "day"),
            unquote(fields["relational_anchor_a"]), unquote(fields["relational_anchor_b"]),
            fields.get("relational_last_a") == "1", fields.get("relational_last_b") == "1")
        result = relational_ledger.execute(program)
        if result.state != "resolved" or result.value is None:
            return ExactExecution(result.state, None, (), result.reason, result.fact_ids)
        if result.value == 1:
            answer = f"one {result.unit}"
        else:
            answer = f"{result.value} {result.unit}s"
        return ExactExecution("resolved", answer, (), result.reason, result.fact_ids)

    if fields.get("temporal_gauge_schema") and temporal_gauge is not None:
        try:
            gauge_query_day = int(fields["temporal_gauge_query_day"])
        except (KeyError, ValueError):
            return ExactExecution("unsupported", None, (), "invalid_temporal_gauge_clock")
        result = temporal_gauge.execute(TemporalGaugeProgram(
            fields["temporal_gauge_schema"], fields.get("temporal_gauge_unit", unit or "day"),
            gauge_query_day))
        if result.state != "resolved" or result.value is None:
            return ExactExecution(result.state, None, (), result.reason, result.fact_ids)
        plural = "" if result.value == 1 else "s"
        return ExactExecution("resolved", f"{result.value} {result.unit}{plural} ago", (),
                              result.reason, result.fact_ids)

    if operation == "event_date":
        if primary is None:
            return ExactExecution("abstain", None, (), "missing_or_invalid_focus")
        content = (primary.content or "").casefold()
        event_day = _refined_ordinal_from_item(primary)
        for marker, (month, day_number) in _HOLIDAYS.items():
            if marker in content:
                suffix = "th" if 10 <= day_number % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(
                    day_number % 10, "th")
                return ExactExecution("resolved", f"{date(2000, month, 1):%B} {day_number}{suffix}",
                                      (focus[0],), "explicit_holiday")
        match = _EXPLICIT_DATE.search(primary.content or "")
        if match:
            return ExactExecution("resolved", f"{match.group(1).title()} {match.group(2)}",
                                  (focus[0],), "explicit_calendar_date")
        if not _confident(_float_fields(fields, "focus_scores"),
                          _float_fields(fields, "focus_margins"), 1):
            return ExactExecution("abstain", None, (focus[0],), "low_focus_margin")
        if event_day is not None and re.search(r"\b(today|that day|hoje|naquele dia)\b", content):
            return ExactExecution("resolved", event_day.isoformat(), (focus[0],), "dated_today")
        return ExactExecution("abstain", None, (focus[0],), "no_exact_event_date")

    if operation == "relative_time":
        if primary is None:
            return ExactExecution("abstain", None, (), "missing_or_invalid_focus")
        if not _confident(_float_fields(fields, "focus_scores"),
                          _float_fields(fields, "focus_margins"), 1):
            return ExactExecution("abstain", None, (focus[0],), "low_focus_margin")
        event_day = _refined_ordinal_from_item(primary)
        context_label = fields.get("context")
        reference_day = None
        if context_label:
            context = by_label.get(context_label)
            if context is None or not _confident(_float_fields(fields, "context_score"),
                                                 _float_fields(fields, "context_margin"), 1):
                return ExactExecution("abstain", None, (focus[0],), "ambiguous_context_event")
            context_day = _refined_ordinal_from_item(context)
            if event_day is None or context_day is None:
                return ExactExecution("abstain", None, (focus[0], context_label),
                                      "invalid_contextual_observer")
            if context_day < event_day:
                return ExactExecution("abstain", None, (focus[0], context_label),
                                      "context_precedes_observed_event")
            reference_day = context_day
        if reference_day is None:
            try:
                reference_day = date.fromisoformat(fields["query_date"])
            except (KeyError, ValueError):
                reference_day = None
        if event_day is None or reference_day is None or event_day > reference_day:
            return ExactExecution("abstain", None, (focus[0],), "invalid_temporal_operands")
        days = (reference_day - event_day).days
        if unit in ("day", "days", "dia", "dias"):
            value, label = days, "day"
        elif unit in ("week", "weeks", "semana", "semanas") and days % 7 == 0:
            value, label = days // 7, "week"
        elif unit in ("month", "months", "mes", "meses"):
            value = ((reference_day.year - event_day.year) * 12 +
                     reference_day.month - event_day.month)
            label = "month"
        elif unit in ("year", "years", "ano", "anos"):
            value = reference_day.year - event_day.year - ((reference_day.month, reference_day.day) <
                                                       (event_day.month, event_day.day))
            label = "year"
        else:
            return ExactExecution("unsupported", None, (focus[0],), "unsupported_unit")
        plural = "" if value == 1 else "s"
        citations = (focus[0], context_label) if context_label else (focus[0],)
        reason = "contextual_observer_difference" if context_label else "exact_calendar_difference"
        return ExactExecution("resolved", f"{value} {label}{plural} ago", citations, reason)

    if operation == "interval":
        bindings = tuple(label for label in fields.get("bindings", "").split(",") if label)
        binding_scores = _float_fields(fields, "binding_scores")
        binding_margins = _float_fields(fields, "binding_margins")
        if len(bindings) != 2 or not _confident(binding_scores, binding_margins, 2):
            return ExactExecution("abstain", None, bindings, "ambiguous_interval_operands")
        operands = tuple(by_label.get(label) for label in bindings)
        if any(item is None for item in operands):
            return ExactExecution("abstain", None, bindings, "interval_operand_not_in_pack")
        days_pair = tuple(_ordinal_from_item(item) for item in operands)
        if any(day is None for day in days_pair) or days_pair[0] == days_pair[1]:
            return ExactExecution("abstain", None, bindings, "invalid_interval_dates")
        days = abs((days_pair[1] - days_pair[0]).days)
        if unit in ("day", "days", "dia", "dias"):
            value, label = days, "day"
        elif unit in ("week", "weeks", "semana", "semanas") and days % 7 == 0:
            value, label = days // 7, "week"
        elif unit in ("month", "months", "mes", "meses") and days_pair[0].day == days_pair[1].day:
            value = abs((days_pair[1].year - days_pair[0].year) * 12 +
                        days_pair[1].month - days_pair[0].month)
            label = "month"
        elif unit in ("year", "years", "ano", "anos") and \
                (days_pair[0].month, days_pair[0].day) == (days_pair[1].month, days_pair[1].day):
            value, label = abs(days_pair[1].year - days_pair[0].year), "year"
        else:
            return ExactExecution("unsupported", None, bindings, "inexact_interval_unit")
        plural = "" if value == 1 else "s"
        return ExactExecution("resolved", f"{value} {label}{plural}", bindings,
                              "exact_proven_interval")

    if operation == "sum":
        if event_ledger is None:
            return ExactExecution("unsupported", None, (), "missing_event_ledger")
        schema, aggregate_unit = fields.get("aggregate_schema"), fields.get("aggregate_unit")
        if not schema or not aggregate_unit:
            return ExactExecution("unsupported", None, (), "aggregate_schema_not_compiled")
        result = event_ledger.aggregate(AggregateProgram(
            schema, aggregate_unit, fields.get("aggregate_month") or None))
        if result.state != "resolved" or result.value is None:
            return ExactExecution(result.state, None, (), result.reason, result.fact_ids)
        value = format(result.value, "f").rstrip("0").rstrip(".") if "." in format(
            result.value, "f") else format(result.value, "f")
        if result.unit == "USD":
            answer = f"${value}"
        else:
            plural = "" if result.value == 1 else "s"
            answer = f"{value} {result.unit}{plural}"
        return ExactExecution("resolved", answer, (), result.reason, result.fact_ids)

    if operation == "count_distinct":
        if event_ledger is None:
            return ExactExecution("unsupported", None, (), "missing_event_ledger")
        schema = fields.get("count_schema")
        if not schema:
            return ExactExecution("unsupported", None, (), "count_schema_not_compiled")
        result = event_ledger.count(CountProgram(schema, fields.get("count_month") or None))
        if result.state != "resolved" or result.value is None:
            return ExactExecution(result.state, None, (), result.reason, result.fact_ids)
        return ExactExecution("resolved", str(result.value), (), result.reason, result.fact_ids)

    return ExactExecution("unsupported", None, (), "operation_not_implemented")


class ProofCarryingCodec:
    """Selects one exact measurement per candidate and emits a compact dated evidence table.

    It never generates a paraphrase.  The digest and offsets allow the caller to reconstruct every
    excerpt from the verified parent.  Retrieval rank remains the primary global budget order.
    """

    @staticmethod
    def _score_text(query: str, text: str) -> float:
        overlap = len(_terms(query) & _terms(text))
        query_numbers = {match.group(0).casefold() for match in _NUMBER.finditer(query)}
        text_numbers = {match.group(0).casefold() for match in _NUMBER.finditer(text)}
        number_match = len(query_numbers & text_numbers)
        charge_bonus = min(2, len(semantic_charges(text))) * 0.15
        role_bonus = 0.25 if text.casefold().startswith("user:") else 0.0
        return 2.0 * overlap + 3.0 * number_match + charge_bonus + role_bonus

    @staticmethod
    def _best_unit(query: str, content: str) -> tuple[int, int, str, float]:
        scored = []
        for position, (start, end, unit) in enumerate(_units(content)):
            score = ProofCarryingCodec._score_text(query, unit)
            scored.append((score, -position, start, end, unit))
        score, _, start, end, unit = max(scored)
        return start, end, unit, score

    def compress(self, query: str, pack: EvidencePack, max_chars: int = 8192,
                 query_time: int | None = None) \
            -> tuple[EvidencePack, CompressionReport]:
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        query_plan = compile_query_equation(query, query_time)
        aggregate_program = compile_aggregate_program(query) if query_plan.startswith("op=sum;") else None
        if aggregate_program is not None:
            query_plan += (f";aggregate_schema={aggregate_program.schema};"
                           f"aggregate_unit={aggregate_program.unit}")
            if aggregate_program.month:
                query_plan += f";aggregate_month={aggregate_program.month}"
        count_program = compile_count_program(query) if query_plan.startswith("op=count_distinct;") else None
        if count_program is not None:
            query_plan += f";count_schema={count_program.schema}"
            if count_program.month:
                query_plan += f";count_month={count_program.month}"
        worldline_program = compile_worldline_program(query)
        if worldline_program is not None:
            query_plan += f";worldline_schema={worldline_program.schema}"
            if worldline_program.subject:
                query_plan += f";worldline_subject={quote(worldline_program.subject, safe='')}"
            if worldline_program.choice_a:
                query_plan += f";worldline_choice_a={quote(worldline_program.choice_a, safe='')}"
            if worldline_program.choice_b:
                query_plan += f";worldline_choice_b={quote(worldline_program.choice_b, safe='')}"
            if worldline_program.direction:
                query_plan += f";worldline_direction={worldline_program.direction}"
        boundary_program = (compile_boundary_program(query, query_time)
                            if query_plan.startswith(("op=lookup;", "op=latest_state;")) else None)
        if boundary_program is not None:
            query_plan += f";boundary_schema={boundary_program.schema}"
            if boundary_program.start_day is not None:
                query_plan += f";boundary_start={boundary_program.start_day}"
            if boundary_program.end_day is not None:
                query_plan += f";boundary_end={boundary_program.end_day}"
            if boundary_program.operands:
                query_plan += ";boundary_operands=" + "|".join(
                    quote(value, safe="") for value in boundary_program.operands)
        relational_program = (compile_relational_program(query)
                              if query_plan.startswith("op=interval;") else None)
        if relational_program is not None:
            query_plan += (f";relational_unit={relational_program.unit};relational_anchor_a="
                           f"{quote(relational_program.anchor_a, safe='')};relational_anchor_b="
                           f"{quote(relational_program.anchor_b, safe='')};relational_last_a="
                           f"{int(relational_program.last_a)};relational_last_b="
                           f"{int(relational_program.last_b)}")
        temporal_program = (compile_temporal_gauge(query, query_time)
                            if query_plan.startswith("op=relative_time;") else None)
        if temporal_program is not None:
            query_plan += (f";temporal_gauge_schema={temporal_program.schema};"
                           f"temporal_gauge_unit={temporal_program.unit};"
                           f"temporal_gauge_query_day={temporal_program.query_day}")
        constraint_program = compile_constraint_program(query, query_time)
        if constraint_program is not None:
            query_plan += f";constraint_schema={constraint_program.schema}"
            if constraint_program.query_day is not None:
                query_plan += f";constraint_query_day={constraint_program.query_day}"
        focus_query = _relative_anchor(query) if query_plan.startswith("op=relative_time;") else query
        proposals = []
        all_input_charges = set()
        for item in pack.items:
            parent = item.content if item.content is not None else str(item.value)
            all_input_charges.update(semantic_charges(parent))
            start, end, excerpt, score = self._best_unit(focus_query, parent)
            header = f"date={_iso_day(item.event_time)}"
            label = f"E{item.retrieval_rank or len(proposals) + 1}"
            rendered = f"[{label} {header}]\n{excerpt}"
            proposals.append((item, start, end, excerpt, rendered, score, label))
        focused = sorted(proposals, key=lambda proposal: (
            -proposal[5],
            proposal[0].retrieval_rank if proposal[0].retrieval_rank is not None else 2 ** 63 - 1,
            proposal[0].fact_id,
        ))[:4]
        focused = tuple(proposal for proposal in focused if proposal[5] > 0)
        if focused:
            focus_labels = ",".join(proposal[6] for proposal in focused)
            focus_scores = ",".join(f"{proposal[5]:.2f}" for proposal in focused)
            runner_up = focused[1][5] if len(focused) > 1 else 0.0
            focus_margins = [max(0.0, focused[0][5] - runner_up)]
            focus_margins.extend(max(0.0, proposal[5]) for proposal in focused[1:])
            query_plan += (f";focus={focus_labels};focus_scores={focus_scores};focus_margins=" +
                           ",".join(f"{margin:.2f}" for margin in focus_margins))
        context_anchor = (_relative_context_anchor(query)
                          if query_plan.startswith("op=relative_time;") else None)
        if context_anchor:
            ranked_context = sorted(((self._score_text(context_anchor, proposal[3]), proposal)
                                     for proposal in proposals), key=lambda pair: (
                                         -pair[0], pair[1][0].retrieval_rank or 2 ** 63 - 1,
                                         pair[1][0].fact_id))
            if ranked_context and ranked_context[0][0] > 0:
                alternative = ranked_context[1][0] if len(ranked_context) > 1 else 0.0
                query_plan += (f";context={ranked_context[0][1][6]};context_score="
                               f"{ranked_context[0][0]:.2f};context_margin="
                               f"{max(0.0, ranked_context[0][0] - alternative):.2f};"
                               "observer_clock=context_event")
        anchors = _interval_anchors(query) if query_plan.startswith("op=interval;") else ()
        if anchors:
            selected = set()
            bindings = []
            for anchor in anchors:
                ranked = sorted(((self._score_text(anchor, proposal[3]), proposal)
                                 for proposal in proposals), key=lambda pair: (
                                     -pair[0], pair[1][0].retrieval_rank or 2 ** 63 - 1,
                                     pair[1][0].fact_id))
                eligible = [pair for pair in ranked if pair[1][6] not in selected]
                if not eligible or eligible[0][0] <= 0:
                    bindings = []
                    break
                best = eligible[0]
                selected.add(best[1][6])
                alternative = max((score for score, proposal in eligible[1:]
                                   if proposal[6] not in selected), default=0.0)
                bindings.append((best[1][6], best[0], max(0.0, best[0] - alternative)))
            if len(bindings) == 2:
                query_plan += (";bindings=" + ",".join(cell[0] for cell in bindings) +
                               ";binding_scores=" + ",".join(f"{cell[1]:.2f}" for cell in bindings) +
                               ";binding_margins=" + ",".join(f"{cell[2]:.2f}" for cell in bindings))
        plan_cost = len(f"<HORIZON_QUERY_PLAN>{query_plan}</HORIZON_QUERY_PLAN>\n") + len(
            '<HORIZON_EVIDENCE trust="untrusted-data">\n\n</HORIZON_EVIDENCE>')
        proposals.sort(key=lambda proposal: (
            proposal[0].retrieval_rank is None,
            proposal[0].retrieval_rank if proposal[0].retrieval_rank is not None else 2 ** 63 - 1,
            proposal[0].fact_id,
        ))
        chosen, used = [], plan_cost
        for item, start, end, excerpt, rendered, _score, _label in proposals:
            cost = len(rendered) + (2 if chosen else 0)
            if used + cost > max_chars:
                continue
            chosen.append(EvidenceItem(
                item.fact_id, item.source, item.version, item.value, excerpt, item.span,
                item.verifier_state, item.sequence, item.retrieval_rank, item.event_time,
                (start, end), hashlib.sha256(
                    (item.content if item.content is not None else str(item.value)).encode()
                ).hexdigest(), _iso_day(item.event_time),
            ))
            used += cost
        compressed = EvidencePack.build(
            pack.query_id, chosen, generation_id=pack.generation_id,
            recovery_reason=pack.recovery_reason, query_plan=query_plan,
            citation_labels={item.fact_id: f"E{item.retrieval_rank or position + 1}"
                             for position, item in enumerate(chosen)},
        )
        output_charges = {charge for item in compressed.items
                          for charge in semantic_charges(item.content or "")}
        query_charges = set(semantic_charges(query))
        report = CompressionReport(
            len(pack.items), len(compressed.items),
            sum(len(item.content or str(item.value)) for item in pack.items),
            sum(len(item.content or str(item.value)) for item in compressed.items),
            len(all_input_charges), len(all_input_charges & output_charges),
            len(query_charges), len(query_charges & output_charges), True,
        )
        return compressed, report

    @staticmethod
    def verify(compressed: EvidencePack, parents: EvidencePack) -> bool:
        by_id = {item.fact_id: item for item in parents.items}
        for item in compressed.items:
            parent = by_id.get(item.fact_id)
            if parent is None or item.content_span is None or item.parent_sha256 is None:
                return False
            raw = parent.content if parent.content is not None else str(parent.value)
            if hashlib.sha256(raw.encode()).hexdigest() != item.parent_sha256:
                return False
            start, end = item.content_span
            if raw[start:end] != item.content:
                return False
        return True
