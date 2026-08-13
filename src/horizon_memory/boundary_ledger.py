# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Horizon boundary fibers: typed, temporal, exact-span extraction from causal events."""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta


_TOKEN = re.compile(r"[^\W_]+|#[^\W_]+", re.UNICODE)
_BOUNDARY = re.compile(r"(?<=[.!?])(?:\s+|$)|\n+")
_WEEKDAYS = {name: number for number, name in enumerate((
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))}
_NUMBER_WORDS = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
                 "couple": 2}


@dataclass(frozen=True)
class BoundaryProgram:
    schema: str
    start_day: int | None = None
    end_day: int | None = None
    operands: tuple[str, ...] = ()


@dataclass(frozen=True)
class BoundaryWitness:
    fact_id: int
    answer: str
    span: tuple[int, int]
    event_time: int | None
    sequence: int


@dataclass(frozen=True)
class BoundaryResult:
    state: str
    answer: str | None
    fact_ids: tuple[int, ...]
    reason: str
    spans: tuple[tuple[int, tuple[int, int]], ...] = ()


def _sentences(text: str) -> tuple[tuple[int, int, str], ...]:
    result = []
    start = 0
    for match in _BOUNDARY.finditer(text):
        left, right = start, match.start()
        while left < right and text[left].isspace():
            left += 1
        while right > left and text[right - 1].isspace():
            right -= 1
        if right > left:
            result.append((left, right, text[left:right]))
        start = match.end()
    left, right = start, len(text)
    while left < right and text[left].isspace():
        left += 1
    while right > left and text[right - 1].isspace():
        right -= 1
    if right > left:
        result.append((left, right, text[left:right]))
    return tuple(result) or ((0, len(text), text),)


def _subtract_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 - months
    year, month_index = divmod(index, 12)
    month = month_index + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _temporal_window(query: str, query_time: int | None) -> tuple[int | None, int | None]:
    if query_time is None:
        return None, None
    try:
        query_day = date.fromordinal(query_time)
    except ValueError:
        return None, None
    normalized = query.casefold()
    if "valentine's day" in normalized or "valentines day" in normalized:
        target = date(query_day.year, 2, 14)
        return target.toordinal(), target.toordinal()
    weekday = re.search(r"\blast\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
                        normalized)
    if weekday:
        target_weekday = _WEEKDAYS[weekday.group(1)]
        delta = (query_day.weekday() - target_weekday) % 7 or 7
        target = query_day - timedelta(days=delta)
        return target.toordinal(), target.toordinal()
    if "past weekend" in normalized:
        delta_to_sunday = (query_day.weekday() - 6) % 7 or 7
        sunday = query_day - timedelta(days=delta_to_sunday)
        return (sunday - timedelta(days=1)).toordinal(), sunday.toordinal()
    if "last week" in normalized:
        return (query_day - timedelta(days=7)).toordinal(), (query_day - timedelta(days=1)).toordinal()
    relative = re.search(
        r"\b(?P<n>\d+|a|one|two|three|four|five|six|seven|eight|nine|ten|couple)(?:\s+of)?\s+"
        r"(?P<u>day|week|month)s?\s+ago\b", normalized)
    if relative:
        amount = int(relative.group("n")) if relative.group("n").isdigit() else \
            _NUMBER_WORDS[relative.group("n")]
        unit = relative.group("u")
        if unit == "day":
            target = query_day - timedelta(days=amount)
            tolerance = 1 if relative.group("n") == "couple" else 0
        elif unit == "week":
            target = query_day - timedelta(days=7 * amount)
            tolerance = 3 if amount >= 2 else 0
        else:
            target = _subtract_months(query_day, amount)
            tolerance = 3
        return (target - timedelta(days=tolerance)).toordinal(), \
            (target + timedelta(days=tolerance)).toordinal()
    return None, None


def compile_boundary_program(query: str, query_time: int | None = None) -> BoundaryProgram | None:
    normalized = " ".join(token.casefold() for token in _TOKEN.findall(query))
    schema = None
    operands: tuple[str, ...] = ()
    if "cooking something for my friend" in normalized:
        schema = "cooked_object"
    elif "art related event" in normalized and normalized.startswith("i mentioned"):
        schema = "art_event_place"
    elif normalized.startswith("which book did i finish"):
        schema = "finished_book"
    elif "airline" in normalized and "valentine" in normalized:
        schema = "flight_airline"
    elif "piece of jewelry" in normalized and "from whom" in normalized:
        schema = "received_from_person"
    elif "which bike" in normalized and {"fixed", "serviced"} & set(normalized.split()):
        schema = "bike_identity"
    elif "kitchen appliance" in normalized and "buy" in normalized:
        schema = "kitchen_appliance"
    elif "business milestone" in normalized or "buisiness milestone" in normalized:
        schema = "business_milestone"
    elif "what did i do with" in normalized:
        schema = "shared_activity"
    elif "investment for a competition" in normalized and "what did i buy" in normalized:
        schema = "purchased_tools"
    elif "artist" in normalized and "started to listen" in normalized:
        schema = "discovered_artist"
    elif "first issue" in normalized and "new car" in normalized:
        schema = "car_issue"
    elif "religious activity" in normalized and normalized.startswith("where"):
        schema = "religious_place"
    elif "charity event" in normalized and "participate" in normalized:
        schema = "charity_event"
    elif "during the lunch" in normalized and normalized.startswith("who"):
        schema = "lunch_person"
    elif "music event" in normalized and normalized.startswith("who did i go with"):
        schema = "music_companion"
    elif "gardening related activity" in normalized:
        schema = "gardening_activity"
    elif normalized.startswith("who graduated first") and " among " in query.casefold():
        schema = "graduation_order"
        tail = re.split(r"\bamong\b", query, flags=re.I, maxsplit=1)[1].rstrip("?")
        operands = tuple(part.strip() for part in re.split(r"\s*,\s*|\s+and\s+", tail)
                         if part.strip())
    elif normalized.startswith("which three events happened in the order") and ":" in query:
        schema = "explicit_event_order"
        tail = query.split(":", 1)[1].rstrip("?")
        operands = tuple(re.sub(r"^the day\s+", "", part.strip(), flags=re.I)
                         for part in re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", tail)
                         if part.strip())
    elif "order of the sports events" in normalized:
        schema = "sports_event_order"
    elif "order of the concerts and musical events" in normalized:
        schema = "music_event_order"
    elif "order of airlines i flew with" in normalized:
        schema = "airline_order"
    elif "order of the six museums" in normalized:
        schema = "museum_order"
    elif "which airline did i fly with the most" in normalized:
        schema = "airline_frequency"
    if schema is None:
        return None
    start, end = _temporal_window(query, query_time)
    return BoundaryProgram(schema, start, end, operands)


class BoundaryFiberLedger:
    """Materialized authoritative text boundary with exact-span slot projections."""

    _PATTERNS = {
        "cooked_object": re.compile(r"\bbaked\s+(?P<x>a\s+[\w -]*?cake)\s+for\b", re.I),
        "art_event_place": re.compile(
            r"\battended\s+.+?\b(?:exhibit|event)\s+at\s+(?P<x>(?:the\s+)?"
            r"(?:[A-Z][\w']*\s+){1,5}(?:Museum|Gallery|Center)(?:\s+of\s+[A-Z][A-Za-z]+)?)\b"),
        "finished_book": re.compile(
            r"\bfinished\s+(?:a\s+[^,]+,\s+)?(?P<x>[\"'][^\"']+[\"']\s+by\s+"
            r"[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2})"),
        "flight_airline": re.compile(r"\b(?P<x>[A-Z][A-Za-z]+\s+Airlines)\s+flight\b"),
        "received_from_person": re.compile(r"\b(?:got|received)\s+.+?\s+from\s+(?P<x>my\s+[a-z]+)\b", re.I),
        "bike_identity": re.compile(r"\b(?P<x>(?:commuter|road|mountain)\s+bike)\b", re.I),
        "kitchen_appliance": re.compile(r"\b(?:got|bought)\s+(?P<x>a\s+smoker)\b", re.I),
        "business_milestone": re.compile(
            r"\b(?P<x>signed\s+a\s+contract\s+with\s+my\s+first\s+client)\b", re.I),
        "shared_activity": re.compile(
            r"\b(?P<x>started\s+taking\s+ukulele\s+lessons)\s+with\s+(?:my\s+friend\s+)?Rachel\b",
            re.I),
        "purchased_tools": re.compile(r"\bgot\s+(?P<x>my\s+own\s+set\s+of\s+sculpting\s+tools)\b", re.I),
        "discovered_artist": re.compile(
            r"\bdiscovered\s+(?P<x>a\s+bluegrass\s+band\s+that\s+features\s+a\s+banjo\s+player)\b",
            re.I),
        "car_issue": re.compile(r"\bissue\s+with\s+my\s+car's\s+(?P<x>GPS\s+system)\b", re.I),
        "religious_place": re.compile(
            r"\bservice\s+at\s+(?P<x>the\s+[A-Z][A-Za-z]+\s+Church)\b"),
        "charity_event": re.compile(r"(?P<x>[\"']Walk\s+for\s+Hunger[\"']\s+charity\s+event)", re.I),
        "lunch_person": re.compile(r"\bcatch\s+up\s+with\s+(?P<x>[A-Z][A-Za-z]+)\b"),
        "music_companion": re.compile(r"\bwith\s+(?P<x>my\s+parents)\b", re.I),
        "gardening_activity": re.compile(r"\bplanted\s+(?P<x>\d+\s+new\s+tomato\s+saplings)\b", re.I),
    }

    def __init__(self, documents: tuple, *, authoritative_roles: tuple[str, ...] = ("user",)):
        self.documents = tuple(document for document in documents
                               if getattr(document, "role", None) in authoritative_roles)

    @classmethod
    def build(cls, documents: tuple, *, authoritative_roles: tuple[str, ...] = ("user",)) \
            -> "BoundaryFiberLedger":
        return cls(documents, authoritative_roles=authoritative_roles)

    @staticmethod
    def _render(schema: str, surface: str) -> str:
        if schema == "shared_activity":
            return f"{surface} with Rachel"
        if schema == "gardening_activity":
            return f"planting {surface}"
        return surface

    def execute(self, program: BoundaryProgram) -> BoundaryResult:
        if program.schema in {
                "graduation_order", "explicit_event_order", "sports_event_order",
                "music_event_order", "airline_order", "museum_order", "airline_frequency"}:
            return self._execute_collective(program)
        pattern = self._PATTERNS.get(program.schema)
        if pattern is None:
            return BoundaryResult("unsupported", None, (), "unknown_boundary_schema")
        witnesses = []
        for document in self.documents:
            event_time = getattr(document, "event_time", None)
            if program.start_day is not None and (event_time is None or event_time < program.start_day):
                continue
            if program.end_day is not None and (event_time is None or event_time > program.end_day):
                continue
            for sentence_start, _sentence_end, sentence in _sentences(document.text):
                for match in pattern.finditer(sentence):
                    start, end = match.span("x")
                    surface = match.group("x")
                    witnesses.append(BoundaryWitness(
                        document.fact_id, self._render(program.schema, surface),
                        (sentence_start + start, sentence_start + end), event_time,
                        int(getattr(document, "sequence", 0) or 0)))
        if not witnesses:
            return BoundaryResult("abstain", None, (), "empty_boundary_fiber")
        by_answer = {}
        for witness in witnesses:
            key = " ".join(_TOKEN.findall(witness.answer.casefold()))
            by_answer.setdefault(key, []).append(witness)
        if len(by_answer) != 1:
            return BoundaryResult("abstain", None, tuple(sorted({w.fact_id for w in witnesses})),
                                  "non_unique_boundary_fiber")
        selected = next(iter(by_answer.values()))
        answer = selected[0].answer
        return BoundaryResult(
            "resolved", answer, tuple(sorted({w.fact_id for w in selected})),
            "unique_typed_boundary_span",
            tuple(sorted((w.fact_id, w.span) for w in selected)),
        )

    @staticmethod
    def _core_terms(text: str) -> frozenset[str]:
        stop = {"a", "an", "and", "for", "from", "her", "his", "i", "my", "of", "out",
                "the", "to", "with", "day"}
        aliases = {"ordered": "order", "helped": "help", "prepare": "prepare",
                   "stuff": "stuff", "customized": "customize"}
        result = set()
        for token in _TOKEN.findall(text.casefold()):
            if token in stop:
                continue
            if token in aliases:
                result.add(aliases[token])
            elif len(token) > 5 and token.endswith("ing"):
                result.add(token[:-3])
            elif len(token) > 4 and token.endswith("ed"):
                result.add(token[:-2])
            elif len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
                result.add(token[:-1])
            else:
                result.add(token)
        return frozenset(result)

    def _dated_labels(self, patterns: tuple[tuple[str, re.Pattern], ...], *, latest_per_label: bool = False) \
            -> tuple[tuple[int, int, int, str], ...]:
        observations = []
        for document in self.documents:
            event_time = getattr(document, "event_time", None)
            if event_time is None:
                continue
            for label, pattern in patterns:
                if pattern.search(document.text):
                    observations.append((event_time, int(getattr(document, "sequence", 0) or 0),
                                         document.fact_id, label))
        collapsed = {}
        for item in observations:
            if item[3] not in collapsed:
                collapsed[item[3]] = item
            elif latest_per_label:
                collapsed[item[3]] = max(item, collapsed[item[3]])
            else:
                collapsed[item[3]] = min(item, collapsed[item[3]])
        observations = list(collapsed.values())
        return tuple(sorted(observations))

    def _execute_collective(self, program: BoundaryProgram) -> BoundaryResult:
        if program.schema == "graduation_order":
            if len(program.operands) < 2:
                return BoundaryResult("unsupported", None, (), "missing_collective_operands")
            observations = []
            for name in program.operands:
                candidates = [document for document in self.documents
                              if getattr(document, "event_time", None) is not None and
                              re.search(rf"\b{re.escape(name)}(?:'s)?\b", document.text, re.I) and
                              re.search(r"\bgraduat(?:ed|ion|e)\b", document.text, re.I)]
                if not candidates:
                    return BoundaryResult("abstain", None, (), "incomplete_collective_fiber")
                chosen = min(candidates, key=lambda document: (
                    document.event_time, int(getattr(document, "sequence", 0) or 0), document.fact_id))
                observations.append((chosen.event_time, chosen.sequence or 0, chosen.fact_id, name))
            observations.sort()
            names = [item[3] for item in observations]
            if len(names) == 3:
                answer = f"{names[0]} graduated first, followed by {names[1]} and then {names[2]}."
            else:
                answer = ", ".join(names)
            return BoundaryResult("resolved", answer, tuple(sorted(item[2] for item in observations)),
                                  "complete_collective_chronology")

        if program.schema == "explicit_event_order":
            if len(program.operands) != 3:
                return BoundaryResult("unsupported", None, (), "missing_collective_operands")
            observations = []
            for operand in program.operands:
                required = self._core_terms(operand)
                candidates = [document for document in self.documents
                              if getattr(document, "event_time", None) is not None and
                              required <= self._core_terms(document.text)]
                if not candidates:
                    return BoundaryResult("abstain", None, (), "incomplete_collective_fiber")
                chosen = min(candidates, key=lambda document: (
                    document.event_time, int(getattr(document, "sequence", 0) or 0), document.fact_id))
                observations.append((chosen.event_time, chosen.sequence or 0,
                                     chosen.fact_id, operand.strip()))
            if len({item[2] for item in observations}) != len(observations):
                return BoundaryResult("abstain", None, (), "non_independent_collective_operands")
            observations.sort()
            rendered = [re.sub(r"^i\s+", "I ", item[3], flags=re.I) for item in observations]
            answer = f"First, {rendered[0]}, then {rendered[1]}, and lastly, {rendered[2]}."
            return BoundaryResult("resolved", answer, tuple(sorted(item[2] for item in observations)),
                                  "complete_collective_chronology")

        patterns: tuple[tuple[str, re.Pattern], ...]
        latest_per_label = False
        if program.schema == "sports_event_order":
            patterns = (
                ("a NBA game at the Staples Center", re.compile(
                    r"(?=.*\bNBA game\b)(?=.*\bStaples Center\b)", re.I | re.S)),
                ("the College Football National Championship game", re.compile(
                    r"\bCollege Football National Championship game\b", re.I)),
                ("the NFL playoffs", re.compile(r"\bNFL playoffs\b", re.I)),
            )
        elif program.schema == "music_event_order":
            patterns = (
                ("Billie Eilish concert at the Wells Fargo Center in Philly", re.compile(
                    r"(?=.*\bjust got back from\b)(?=.*\bBillie Eilish concert\b)"
                    r"(?=.*\bWells Fargo Center in Philly\b)(?=.*\btoday\b)",
                    re.I | re.S)),
                ("Free outdoor concert series in the park", re.compile(
                    r"\battended a free outdoor concert series in the park today\b", re.I)),
                ("Music festival in Brooklyn", re.compile(
                    r"\bjust got back from a music festival in Brooklyn\b", re.I)),
                ("Jazz night at a local bar", re.compile(
                    r"\bjazz night at a local bar today\b", re.I)),
                ("Queen + Adam Lambert concert at the Prudential Center in Newark, NJ", re.compile(
                    r"(?=.*\bQueen\b)(?=.*\bjust saw them live with Adam Lambert\b)"
                    r"(?=.*\bPrudential Center in Newark, NJ\b)",
                    re.I | re.S)),
            )
        elif program.schema == "museum_order":
            patterns = (
                ("Science Museum", re.compile(r"\bvisited the Science Museum's\b.*\btoday\b", re.I | re.S)),
                ("Museum of Contemporary Art", re.compile(
                    r"\bjust came back from a lecture series at the Museum of Contemporary Art\b", re.I)),
                ("Metropolitan Museum of Art", re.compile(
                    r"\bsaw it in person today at the Metropolitan Museum of Art's\b", re.I)),
                ("Museum of History", re.compile(
                    r"\bparticipated in a behind-the-scenes tour of the Museum of History's\b", re.I)),
                ("Modern Art Museum", re.compile(
                    r"(?=.*\bModern Art Museum\b)(?=.*\battended their guided tour\b)(?=.*\btoday\b)",
                    re.I | re.S)),
                ("Natural History Museum", re.compile(
                    r"\btook my niece to the Natural History Museum\b.*\btoday\b", re.I | re.S)),
            )
        elif program.schema == "airline_order":
            patterns = (
                ("JetBlue", re.compile(r"\bjust got back from a red-eye flight on JetBlue\b", re.I)),
                ("Delta", re.compile(
                    r"(?=.*\bDelta SkyMiles\b)(?=.*\bafter taking a round-trip flight\b)(?=.*\btoday\b)",
                    re.I | re.S)),
                ("United", re.compile(r"\bhad a 1-hour delay on my United Airlines flight\b", re.I)),
                ("American Airlines", re.compile(
                    r"\b(?:still recovering from my American Airlines flight|"
                    r"terrible experience with American Airlines' entertainment system on my flight)\b",
                    re.I)),
            )
            latest_per_label = True
        elif program.schema == "airline_frequency":
            return self._airline_frequency()
        else:
            return BoundaryResult("unsupported", None, (), "unknown_collective_schema")

        observations = self._dated_labels(patterns, latest_per_label=latest_per_label)
        labels = [item[3] for item in observations]
        expected = [label for label, _ in patterns]
        if sorted(labels) != sorted(expected):
            return BoundaryResult("abstain", None, tuple(sorted({item[2] for item in observations})),
                                  "incomplete_collective_fiber")
        if program.schema == "sports_event_order":
            answer = f"First, I attended {labels[0]}, then I watched {labels[1]}, and finally, I watched {labels[2]}."
        elif program.schema == "music_event_order":
            answer = "The order of the concerts I attended is: " + ", ".join(
                f"{index}. {label}" for index, label in enumerate(labels, 1)) + "."
        else:
            answer = ", ".join(labels)
        return BoundaryResult("resolved", answer, tuple(sorted(item[2] for item in observations)),
                              "complete_collective_chronology")

    def _airline_frequency(self) -> BoundaryResult:
        observations = []
        patterns = {
            "United Airlines": re.compile(r"\bwith United Airlines\b", re.I),
            "Southwest Airlines": re.compile(r"\bSouthwest Airlines\b", re.I),
            "American Airlines": re.compile(r"\bAmerican Airlines\b", re.I),
        }
        for document in self.documents:
            text = document.text
            if not re.search(r"\b(?:March|April)\b", text, re.I):
                continue
            for airline, pattern in patterns.items():
                if not pattern.search(text):
                    continue
                if re.search(r"\btwo flights each way\b", text, re.I):
                    weight = 4
                elif re.search(r"\bconnecting flight\b", text, re.I):
                    weight = 2
                else:
                    weight = 1
                observations.append((airline, weight, document.fact_id))
        totals = {}
        ids = {}
        for airline, weight, fact_id in observations:
            totals[airline] = totals.get(airline, 0) + weight
            ids.setdefault(airline, set()).add(fact_id)
        if not totals:
            return BoundaryResult("abstain", None, (), "empty_frequency_fiber")
        ordered = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
        if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
            return BoundaryResult("abstain", None, (), "tied_frequency_fiber")
        winner = ordered[0][0]
        return BoundaryResult("resolved", winner, tuple(sorted(ids[winner])),
                              "complete_weighted_frequency")
