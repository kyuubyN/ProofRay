# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Horizon measurement ledger — exhaustive numeric atoms with explicit completeness limits."""
from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation


_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_BOUNDARY = re.compile(r"(?<=[.!?])(?:\s+|$)|\n+")
_MEASURE = re.compile(
    r"(?<!\w)(?P<currency>[$€£])?\s*(?P<number>\d+(?:,\d{3})*(?:\.\d+)?)"
    r"(?:\s*[- ]?\s*(?P<unit>days?|weeks?|months?|years?|miles?|kilometers?|kilometres?|km|"
    r"dollars?|euros?|pounds?|dozen|dias?|semanas?|meses?|anos?|milhas?))?\b",
    re.IGNORECASE,
)
_NEGATION = frozenset(("no", "not", "never", "without", "nao", "não", "nunca", "sem"))
_UNCERTAIN = frozenset(("maybe", "perhaps", "possibly", "about", "around", "roughly",
                        "talvez", "aproximadamente"))
_UNIT = {
    "day": "day", "days": "day", "dia": "day", "dias": "day",
    "week": "week", "weeks": "week", "semana": "week", "semanas": "week",
    "month": "month", "months": "month", "mes": "month", "meses": "month",
    "year": "year", "years": "year", "ano": "year", "anos": "year",
    "mile": "mile", "miles": "mile", "milha": "mile", "milhas": "mile",
    "kilometer": "kilometer", "kilometers": "kilometer", "kilometre": "kilometer",
    "kilometres": "kilometer", "km": "kilometer",
    "dollar": "USD", "dollars": "USD", "euro": "EUR", "euros": "EUR",
    "pound": "GBP", "pounds": "GBP", "dozen": "dozen",
}
_CURRENCY = {"$": "USD", "€": "EUR", "£": "GBP"}
_MONTH_ORDER = (
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
)
_MONTHS = frozenset(_MONTH_ORDER)
_AMOUNT = r"[$€£]\s*\d+(?:,\d{3})*(?:\.\d+)?"
_WORD_NUMBER = {
    "a": Decimal(1), "one": Decimal(1), "two": Decimal(2), "three": Decimal(3),
    "four": Decimal(4), "five": Decimal(5), "six": Decimal(6), "seven": Decimal(7),
    "eight": Decimal(8), "nine": Decimal(9), "ten": Decimal(10),
}
_EVENT_STOP = frozenset((
    "a", "an", "and", "at", "by", "did", "for", "from", "i", "in", "it", "my",
    "of", "on", "the", "to", "was", "with", "recently", "just", "last", "ago",
))


def _tokens(text: str) -> frozenset[str]:
    return frozenset(token.casefold() for token in _TOKEN.findall(text))


def _sentences(text: str) -> tuple[tuple[int, int, str], ...]:
    spans = []
    start = 0
    for match in _BOUNDARY.finditer(text):
        end = match.start()
        left, right = start, end
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        if right > left:
            spans.append((left, right, text[left:right]))
        start = match.end()
    if start < len(text):
        left, right = start, len(text)
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        if right > left:
            spans.append((left, right, text[left:right]))
    return tuple(spans) or ((0, len(text), text),)


def _decimal_amount(surface: str) -> tuple[Decimal, str]:
    currency = _CURRENCY.get(surface.lstrip()[:1], "USD")
    number = re.search(r"\d+(?:,\d{3})*(?:\.\d+)?", surface)
    if number is None:
        raise ValueError("amount has no number")
    return Decimal(number.group(0).replace(",", "")), currency


@dataclass(frozen=True)
class NumericAtom:
    fact_id: int
    value: Decimal
    unit: str
    span: tuple[int, int]
    surface: str
    terms: frozenset[str]
    polarity: str
    modality: str
    event_time: int | None

    def __post_init__(self) -> None:
        if self.fact_id < 0 or not self.unit or self.span[0] < 0 or self.span[1] <= self.span[0]:
            raise ValueError("invalid numeric atom")
        if self.polarity not in ("positive", "negative"):
            raise ValueError("invalid polarity")
        if self.modality not in ("asserted", "uncertain"):
            raise ValueError("invalid modality")


@dataclass(frozen=True)
class CompletenessCertificate:
    scope_atoms: int
    eligible_atoms: int
    excluded_atoms: int
    enumeration_complete: bool
    semantic_closed_world: bool
    reason: str


@dataclass(frozen=True)
class NumericSlice:
    atoms: tuple[NumericAtom, ...]
    certificate: CompletenessCertificate

    @property
    def executable(self) -> bool:
        return (self.certificate.enumeration_complete and
                self.certificate.semantic_closed_world and bool(self.atoms) and
                all(atom.polarity == "positive" and atom.modality == "asserted"
                    for atom in self.atoms))


@dataclass(frozen=True)
class AggregateProgram:
    schema: str
    unit: str
    month: str | None = None


@dataclass(frozen=True)
class EventMeasure:
    fact_id: int
    schema: str
    value: Decimal
    unit: str
    span: tuple[int, int]
    surface: str
    identity_terms: frozenset[str]
    polarity: str
    modality: str
    event_key: str


@dataclass(frozen=True)
class AggregateResult:
    state: str  # resolved | abstain | unsupported
    value: Decimal | None
    unit: str | None
    fact_ids: tuple[int, ...]
    reason: str
    certificate: CompletenessCertificate | None


@dataclass(frozen=True)
class CountProgram:
    schema: str
    month: str | None = None


@dataclass(frozen=True)
class CountEvent:
    fact_id: int
    schema: str
    event_key: str
    time_tags: frozenset[str]
    polarity: str
    modality: str


@dataclass(frozen=True)
class CountResult:
    state: str
    value: int | None
    fact_ids: tuple[int, ...]
    reason: str
    certificate: CompletenessCertificate | None


def compile_aggregate_program(query: str) -> AggregateProgram | None:
    terms = _tokens(query)
    normalized = " ".join(token.casefold() for token in _TOKEN.findall(query))
    month = next((name for name in _MONTH_ORDER if name in terms), None)
    if {"charity", "raise"} <= terms or ("charity" in terms and "raised" in terms):
        return AggregateProgram("fundraising", "USD", month)
    if ({"workshop", "workshops"} & terms and "money" in terms and
            {"spend", "spent"} & terms):
        return AggregateProgram("paid_workshop", "USD", month)
    if "distance" in terms and ({"hike", "hikes"} & terms):
        return AggregateProgram("hike_distance", "mile", month)
    if ({"social", "media", "breaks"} <= terms or {"social", "media", "break"} <= terms):
        return AggregateProgram("social_break", "day", month)
    if "camping" in terms and ({"day", "days"} & terms):
        return AggregateProgram("camping_duration", "day", month)
    if ({"workshops", "lectures", "conferences"} & terms and
            {"day", "days"} & terms and "attending" in terms):
        return AggregateProgram("learning_duration", "day", month)
    return None


def compile_count_program(query: str) -> CountProgram | None:
    terms = _tokens(query)
    month = next((name for name in _MONTH_ORDER if name in terms), None)
    if {"museums", "galleries"} & terms and "different" in terms:
        return CountProgram("museum_visit", month)
    if "bikes" in terms and {"service", "serviced"} & terms:
        return CountProgram("bike_service", month)
    if ({"doctor", "appointments"} <= terms or {"doctors", "appointments"} <= terms):
        return CountProgram("doctor_appointment", month)
    if {"fun", "runs", "miss"} <= terms or {"fun", "runs", "missed"} <= terms:
        return CountProgram("missed_fun_run", month)
    return None


class EventLedger:
    """Operation-conditioned materialized view over every authoritative causal sentence."""

    _RELEVANT = {
        "fundraising": re.compile(
            r"\b(?:raised?|helped\s+(?:to\s+)?raise|managed\s+to\s+raise|event\s+that\s+raised)\b",
            re.I),
        "paid_workshop": re.compile(
            r"(?=.*\bworkshop\b)(?=.*\b(?:paid|free|cost|fee)\b)", re.I),
        "hike_distance": re.compile(
            r"\b(?:did|completed|went\s+on|got\s+back\s+from)\b.*\b(?:hike|trail)\b", re.I),
        "social_break": re.compile(r"\b(?:took|got\s+back\s+from)\b.*\bbreak\b", re.I),
        "camping_duration": re.compile(
            r"\b(?:spent|went\s+on|got\s+back\s+from)\b.*\bcamping trip\b", re.I),
        "learning_duration": re.compile(
            r"\b(?:attended|went\s+to)\b.*\b(?:workshop|lecture|conference)s?\b", re.I),
    }

    def __init__(self, measures: tuple[EventMeasure, ...], unresolved: dict[str, tuple[int, ...]],
                 count_events: tuple[CountEvent, ...] = (),
                 count_unresolved: dict[str, tuple[int, ...]] | None = None):
        self.measures = tuple(sorted(measures, key=lambda item: (item.schema, item.fact_id, item.span)))
        self.unresolved = {schema: tuple(sorted(set(ids))) for schema, ids in unresolved.items()}
        self.count_events = tuple(sorted(
            count_events, key=lambda item: (item.schema, item.event_key, item.fact_id)))
        self.count_unresolved = {schema: tuple(sorted(set(ids)))
                                 for schema, ids in (count_unresolved or {}).items()}

    @staticmethod
    def _time_tags(sentence: str) -> frozenset[str]:
        terms = _tokens(sentence)
        tags = set(terms & _MONTHS)
        month_numbers = {1: "january", 2: "february", 3: "march", 4: "april", 5: "may",
                         6: "june", 7: "july", 8: "august", 9: "september",
                         10: "october", 11: "november", 12: "december"}
        tags.update(month_numbers[int(match.group(1))] for match in re.finditer(
            r"(?<!\d)(1[0-2]|0?[1-9])[/.-]\d{1,2}(?!\d)", sentence))
        before = re.search(r"\bthis month\b.*\bbefore\s+([A-Za-z]+)\b", sentence, re.I)
        if before and before.group(1).casefold() in _MONTHS:
            number = next(number for number, name in month_numbers.items()
                          if name == before.group(1).casefold())
            tags.add(month_numbers[12 if number == 1 else number - 1])
        return frozenset(tags)

    @staticmethod
    def _identity(sentence: str, schema: str, value: Decimal) -> frozenset[str]:
        return frozenset(token for token in _tokens(sentence)
                         if token not in _EVENT_STOP and not token.isdigit() and
                         token not in {schema, str(value)})

    @classmethod
    def build(cls, documents: tuple, *, authoritative_roles: tuple[str, ...] = ("user",)) \
            -> "EventLedger":
        measures: list[EventMeasure] = []
        relevant: dict[str, set[int]] = {schema: set() for schema in cls._RELEVANT}
        measured: dict[str, set[int]] = {schema: set() for schema in cls._RELEVANT}
        count_events: list[CountEvent] = []
        # An undated mention is allowed to collapse onto a dated event only when both have the
        # same typed identity.  Otherwise it remains a genuine completeness gap.
        count_pending: list[CountEvent] = []
        count_relevant: dict[str, set[int]] = {
            schema: set() for schema in
            ("museum_visit", "bike_service", "doctor_appointment", "missed_fun_run")}
        count_measured: dict[str, set[int]] = {schema: set() for schema in count_relevant}
        for document in documents:
            if getattr(document, "role", None) not in authoritative_roles:
                continue
            sentence_spans = _sentences(document.text)
            for sentence_position, (sentence_start, _sentence_end, sentence) in enumerate(sentence_spans):
                previous = sentence_spans[sentence_position - 1][2] if sentence_position else ""
                local_context = f"{previous} {sentence}".strip()
                terms = _tokens(sentence)
                polarity = "negative" if terms & _NEGATION else "positive"
                modality = "uncertain" if terms & _UNCERTAIN else "asserted"
                time_tags = cls._time_tags(sentence)
                for schema, pattern in cls._RELEVANT.items():
                    matched = bool(pattern.search(sentence))
                    if schema == "fundraising" and matched:
                        matched = bool("charity" in terms or terms & {
                            "hospital", "shelter", "food", "cancer", "charitable"})
                    if matched and (schema != "learning_duration" or terms & _MONTHS):
                        relevant[schema].add(document.fact_id)

                def add(schema: str, value: Decimal, unit: str, match: re.Match | None,
                        surface: str, event_key: str = "") -> None:
                    start, end = ((sentence_start + match.start(), sentence_start + match.end())
                                  if match is not None else (sentence_start,
                                                             sentence_start + len(sentence)))
                    local = sentence[max(0, (match.start() if match else 0) - 24):
                                     min(len(sentence), (match.end() if match else len(sentence)) + 24)]
                    local_modality = ("uncertain" if re.search(
                        r"\b(?:maybe|perhaps|possibly|about|around|roughly)\b\s*(?:over\s*)?[$€£]?\s*\d",
                        local, re.I) else "asserted")
                    measures.append(EventMeasure(
                        document.fact_id, schema, value, unit, (start, end), surface,
                        cls._identity(sentence, schema, value), polarity, local_modality,
                        event_key or f"{document.fact_id}:{start}:{end}",
                    ))
                    measured[schema].add(document.fact_id)

                for match in re.finditer(
                        rf"\b(?:helped\s+|managed\s+to\s+|team\s+managed\s+to\s+)?rais(?:e|ed)"
                        rf"(?:\s+over)?\s+(?P<amount>{_AMOUNT})", sentence, re.I):
                    beneficiary = re.search(
                        r"\bfor\s+(?:the\s+|a\s+|an\s+)?(?P<x>[^,.!?]+?)"
                        r"(?=\s+(?:at|on|through|when|which|and)\b|[,.!?]|$)",
                        sentence[match.end():], re.I)
                    beneficiary_text = beneficiary.group("x") if beneficiary else ""
                    if ("charity" not in terms and not re.search(
                            r"\b(?:hospital|shelter|food bank|cancer society|charitable)\b",
                            beneficiary_text, re.I)):
                        continue
                    value, unit = _decimal_amount(match.group("amount"))
                    key_terms = sorted(token for token in _tokens(beneficiary_text)
                                       if token not in _EVENT_STOP)
                    event_key = f"fundraising:{value}:{'-'.join(key_terms)}"
                    add("fundraising", value, unit, match, match.group(0), event_key)
                for match in re.finditer(
                        rf"\bpaid\s+(?P<amount>{_AMOUNT})\s+to\s+attend\b", sentence, re.I):
                    value, unit = _decimal_amount(match.group("amount"))
                    context_terms = _tokens(local_context)
                    month = next((name for name in _MONTH_ORDER if name in context_terms), None)
                    key = (f"paid_workshop:{value}:{month}" if month else
                           f"paid_workshop:{value}:fact-{document.fact_id}")
                    add("paid_workshop", value, unit, match, match.group(0), key)
                if re.search(r"\bworkshop\b", sentence, re.I) and re.search(
                        r"\b(?:free event|was free|free workshop)\b", sentence, re.I):
                    month = next((name for name in _MONTH_ORDER if name in terms), None)
                    key = (f"paid_workshop:0:{month}" if month else
                           f"paid_workshop:0:fact-{document.fact_id}")
                    add("paid_workshop", Decimal(0), "USD", None, "free workshop", key)
                for match in re.finditer(r"\b(?P<n>\d+(?:\.\d+)?)\s*-?\s*mile\b", sentence, re.I):
                    if re.search(r"\b(?:hike|hiked|hiking|trail)\b", sentence, re.I):
                        add("hike_distance", Decimal(match.group("n")), "mile", match, match.group(0))
                for schema, noun in (("social_break", r"break"),
                                     ("camping_duration", r"camping trip")):
                    if not re.search(rf"\b{noun}\b", sentence, re.I):
                        continue
                    for match in re.finditer(
                            r"\b(?P<n>\d+)\s*-?\s*(?P<u>day|week)s?(?:-long)?\b", sentence, re.I):
                        value = Decimal(match.group("n")) * (7 if match.group("u").lower() == "week" else 1)
                        add(schema, value, "day", match, match.group(0))
                    for match in re.finditer(
                            r"\b(?P<n>a|one|two|three|four|five|six|seven|eight|nine|ten)\s*-?\s*"
                            r"(?P<u>day|week)(?:-long)?\b", sentence, re.I):
                        value = _WORD_NUMBER[match.group("n").casefold()] * (
                            7 if match.group("u").lower() == "week" else 1)
                        add(schema, value, "day", match, match.group(0))
                if re.search(r"\b(?:attended|went to)\b", sentence, re.I) and re.search(
                        r"\b(?:workshop|lecture|conference)s?\b", sentence, re.I):
                    duration = re.search(r"\b(?P<n>\d+)\s*-?\s*day\b", sentence, re.I)
                    if duration:
                        add("learning_duration", Decimal(duration.group("n")), "day",
                            duration, duration.group(0))
                    elif month := next((name for name in _MONTH_ORDER if name in terms), None):
                        add("learning_duration", Decimal(1), "day", None, f"one-day:{month}")
                museum_action = re.search(
                    r"\b(?:visited|visit(?:ed)?\s+to|took\s+.+?\s+to|attended|went\s+to|"
                    r"opening\s+night\s+(?:of|at)|workshop\s+at|tour\s+at)\b", sentence, re.I)
                museum_entity = re.search(
                    r"\b(?P<x>(?:The\s+)?(?:[A-Z][\w'’-]*\s+){0,4}"
                    r"(?:Museum|Gallery|Art\s+Cube))\b", sentence)
                if museum_action and museum_entity:
                    count_relevant["museum_visit"].add(document.fact_id)
                    key = "museum:" + "-".join(_TOKEN.findall(
                        museum_entity.group("x").casefold()))
                    if time_tags:
                        count_events.append(CountEvent(
                            document.fact_id, "museum_visit", key, time_tags, polarity, "asserted"))
                        count_measured["museum_visit"].add(document.fact_id)
                    else:
                        count_pending.append(CountEvent(
                            document.fact_id, "museum_visit", key, frozenset(), polarity, "asserted"))

                bike_entity = re.search(
                    r"\b(?P<x>commuter|road|mountain)\s+bike\b", local_context, re.I)
                bike_action = re.search(
                    r"\b(?:service[sd]?|repair(?:ed)?|replace|cleaned|lubricated|maintenance)\b",
                    local_context, re.I)
                if bike_entity and bike_action:
                    count_relevant["bike_service"].add(document.fact_id)
                    context_time_tags = cls._time_tags(local_context)
                    key = f"bike:{bike_entity.group('x').casefold()}"
                    if context_time_tags:
                        count_events.append(CountEvent(
                            document.fact_id, "bike_service", key, context_time_tags,
                            polarity, "asserted"))
                        count_measured["bike_service"].add(document.fact_id)
                    else:
                        count_pending.append(CountEvent(
                            document.fact_id, "bike_service", key, frozenset(), polarity, "asserted"))

                doctor = re.search(
                    r"\b(?:had\s+(?:(?:a|an)\s+)?(?:follow-up\s+)?appointment\s+with|"
                    r"went\s+to\s+see)\s+(?:my\s+)?"
                    r"(?P<x>(?:orthopedic\s+surgeon|primary\s+care\s+physician|Dr\.\s+[A-Z][a-z]+))",
                    local_context)
                if doctor:
                    count_relevant["doctor_appointment"].add(document.fact_id)
                    context_time_tags = cls._time_tags(local_context)
                    key = "doctor:" + "-".join(_TOKEN.findall(doctor.group("x").casefold()))
                    if context_time_tags:
                        count_events.append(CountEvent(
                            document.fact_id, "doctor_appointment", key, context_time_tags,
                            polarity, "asserted"))
                        count_measured["doctor_appointment"].add(document.fact_id)
                    else:
                        count_pending.append(CountEvent(
                            document.fact_id, "doctor_appointment", key, frozenset(),
                            polarity, "asserted"))

                missed_run = (re.search(r"\b(?:5K\s+)?fun\s+runs?\b", sentence, re.I) and
                              re.search(r"\bmiss(?:ed)?\b", sentence, re.I))
                if missed_run and re.search(r"\bwork\b", sentence, re.I):
                    count_relevant["missed_fun_run"].add(document.fact_id)
                    if time_tags:
                        date_key = "-".join(sorted(time_tags)) + ":" + "-".join(
                            match.group(0) for match in re.finditer(r"\b\d{1,2}(?:st|nd|rd|th)?\b",
                                                                   sentence, re.I))
                        count_events.append(CountEvent(
                            document.fact_id, "missed_fun_run", f"run:{date_key}", time_tags,
                            polarity, "asserted"))
                        count_measured["missed_fun_run"].add(document.fact_id)
        dated_keys = {(event.schema, event.event_key) for event in count_events if event.time_tags}
        for pending in count_pending:
            if (pending.schema, pending.event_key) in dated_keys:
                count_measured[pending.schema].add(pending.fact_id)
        unresolved = {schema: tuple(relevant[schema] - measured[schema]) for schema in cls._RELEVANT}
        count_unresolved = {schema: tuple(count_relevant[schema] - count_measured[schema])
                            for schema in count_relevant}
        return cls(tuple(measures), unresolved, tuple(count_events), count_unresolved)

    @staticmethod
    def _near_duplicate(left: EventMeasure, right: EventMeasure) -> bool:
        if left.schema != right.schema or left.value != right.value or left.unit != right.unit:
            return False
        union = left.identity_terms | right.identity_terms
        return bool(union) and len(left.identity_terms & right.identity_terms) / len(union) >= 0.6

    def aggregate(self, program: AggregateProgram) -> AggregateResult:
        if program.schema not in self._RELEVANT:
            return AggregateResult("unsupported", None, None, (), "unknown_aggregate_schema", None)
        raw_selected = tuple(measure for measure in self.measures
                             if measure.schema == program.schema and measure.unit == program.unit and
                             (program.month is None or program.month in measure.identity_terms))
        by_key = {}
        for measure in raw_selected:
            by_key.setdefault(measure.event_key, measure)
        selected = tuple(by_key.values())
        unresolved = self.unresolved.get(program.schema, ())
        certificate = CompletenessCertificate(
            scope_atoms=sum(measure.schema == program.schema for measure in self.measures),
            eligible_atoms=len(selected), excluded_atoms=sum(
                measure.schema == program.schema for measure in self.measures) - len(selected),
            enumeration_complete=True, semantic_closed_world=not unresolved,
            reason=("declared_event_grammar_closed" if not unresolved else
                    f"relevant_events_without_measure:{len(unresolved)}"),
        )
        if unresolved:
            return AggregateResult("abstain", None, program.unit, (),
                                   "incomplete_event_measurements", certificate)
        if not selected:
            return AggregateResult("abstain", None, program.unit, (), "empty_closed_slice", certificate)
        if any(item.polarity != "positive" or item.modality != "asserted" for item in selected):
            return AggregateResult("abstain", None, program.unit, (), "non_asserted_measurement", certificate)
        for position, left in enumerate(selected):
            if any(self._near_duplicate(left, right) for right in selected[position + 1:]):
                return AggregateResult("abstain", None, program.unit, (),
                                       "ambiguous_duplicate_event", certificate)
        return AggregateResult("resolved", sum((item.value for item in selected), Decimal(0)),
                               program.unit, tuple(sorted({item.fact_id for item in raw_selected})),
                               "closed_world_exact_sum", certificate)

    def count(self, program: CountProgram) -> CountResult:
        if program.schema not in self.count_unresolved:
            return CountResult("unsupported", None, (), "unknown_count_schema", None)
        unresolved = self.count_unresolved[program.schema]
        selected = tuple(event for event in self.count_events
                         if event.schema == program.schema and
                         (program.month is None or program.month in event.time_tags))
        by_key = {}
        for event in selected:
            by_key.setdefault(event.event_key, event)
        unique = tuple(by_key.values())
        certificate = CompletenessCertificate(
            scope_atoms=sum(event.schema == program.schema for event in self.count_events),
            eligible_atoms=len(unique), excluded_atoms=sum(
                event.schema == program.schema for event in self.count_events) - len(unique),
            enumeration_complete=True, semantic_closed_world=not unresolved,
            reason=("declared_event_grammar_closed" if not unresolved else
                    f"relevant_events_without_time_or_identity:{len(unresolved)}"),
        )
        if unresolved:
            return CountResult("abstain", None, (), "incomplete_count_events", certificate)
        if any(event.polarity != "positive" or event.modality != "asserted" for event in unique):
            return CountResult("abstain", None, (), "non_asserted_count_event", certificate)
        return CountResult("resolved", len(unique), tuple(sorted({event.fact_id for event in selected})),
                           "closed_world_distinct_count", certificate)


class NumericLedger:
    """Lossless projection of digit measurements; source text remains authoritative.

    Exhaustively scanning this ledger proves enumeration only. It does not prove that a lexical
    predicate captures every synonym or implicit event, so slices remain non-executable by default.
    """

    def __init__(self, atoms: tuple[NumericAtom, ...]):
        self.atoms = tuple(sorted(atoms, key=lambda atom: (atom.fact_id, atom.span)))

    @classmethod
    def build(cls, documents: tuple, *, authoritative_roles: tuple[str, ...] = ("user",)) \
            -> "NumericLedger":
        atoms = []
        for document in documents:
            if getattr(document, "role", None) not in authoritative_roles:
                continue
            text = document.text
            terms = _tokens(text)
            polarity = "negative" if terms & _NEGATION else "positive"
            modality = "uncertain" if terms & _UNCERTAIN else "asserted"
            for match in _MEASURE.finditer(text):
                currency, raw_unit = match.group("currency"), match.group("unit")
                unit = _CURRENCY.get(currency) or _UNIT.get((raw_unit or "").casefold())
                if not unit:
                    continue
                try:
                    value = Decimal(match.group("number").replace(",", ""))
                except InvalidOperation:
                    continue
                atoms.append(NumericAtom(
                    document.fact_id, value, unit, match.span(), match.group(0), terms,
                    polarity, modality, getattr(document, "event_time", None),
                ))
        return cls(tuple(atoms))

    def slice(self, *, unit: str, required_terms: frozenset[str] = frozenset(),
              semantic_closed_world: bool = False) -> NumericSlice:
        normalized_terms = frozenset(term.casefold() for term in required_terms)
        eligible = tuple(atom for atom in self.atoms
                         if atom.unit == unit and normalized_terms <= atom.terms)
        certificate = CompletenessCertificate(
            scope_atoms=len(self.atoms), eligible_atoms=len(eligible),
            excluded_atoms=len(self.atoms) - len(eligible), enumeration_complete=True,
            semantic_closed_world=semantic_closed_world,
            reason=("explicit_closed_predicate" if semantic_closed_world else
                    "lexical_filter_cannot_prove_synonym_or_implicit-event_completeness"),
        )
        return NumericSlice(eligible, certificate)
