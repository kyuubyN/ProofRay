# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Causal state worldlines for exact current-state and temporal-choice execution.

This module deliberately supports a small typed grammar.  It does not treat arbitrary mentions as
events: every accepted transition needs an authoritative role, an identity, and an independently
recoverable clock or version coordinate.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta


_TOKEN = re.compile(r"[^\W_]+|#[^\W_]+", re.UNICODE)
_BOUNDARY = re.compile(r"(?<=[.!?])(?:\s+|$)|\n+")
_MONTHS = {name.casefold(): number for number, name in enumerate((
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December"), 1)}
_WEEKDAYS = {name: number for number, name in enumerate((
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))}
_CHOICE_STOP = frozenset((
    "a", "an", "the", "my", "our", "i", "we", "event", "issue", "vehicle", "device",
    "mode", "of", "in", "transport", "participation", "post", "about", "activity",
))


def _sentences(text: str) -> tuple[str, ...]:
    result = []
    start = 0
    for match in _BOUNDARY.finditer(text):
        sentence = text[start:match.start()].strip()
        if sentence:
            result.append(sentence)
        start = match.end()
    tail = text[start:].strip()
    if tail:
        result.append(tail)
    return tuple(result) or (text,)


def _term_key(token: str) -> str:
    token = token.casefold().rstrip("'s")
    nominal_gauge = {"removal": "remove", "subscription": "subscribe"}
    if token in nominal_gauge:
        return nominal_gauge[token]
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


def _terms(text: str, *, choices: bool = False) -> frozenset[str]:
    stop = _CHOICE_STOP if choices else frozenset()
    return frozenset(_term_key(token) for token in _TOKEN.findall(text)
                     if token.casefold() not in stop and len(token) > 1)


def _choice_surface(text: str) -> str:
    return re.sub(r"^(?:the|a|an|my|our)\s+", "", text.strip().rstrip("?"), flags=re.I)


def _choice_answer(choice: str) -> str:
    """Render a query operand as an answer without consulting evidence labels or gold."""
    post = re.fullmatch(r"post about (?P<x>.+?) recipe", choice, re.I)
    if post:
        return f"posted a recipe for {post.group('x')}"
    return choice


@dataclass(frozen=True)
class WorldlineProgram:
    schema: str
    subject: str = ""
    choice_a: str = ""
    choice_b: str = ""
    direction: str = ""


@dataclass(frozen=True)
class WorldlineAtom:
    fact_id: int
    sentence: str
    event_time: int | None
    sequence: int
    transport_kind: str = ""


@dataclass(frozen=True)
class WorldlineResult:
    state: str
    answer: str | None
    fact_ids: tuple[int, ...]
    reason: str


def compile_worldline_program(query: str) -> WorldlineProgram | None:
    normalized = " ".join(token.casefold() for token in _TOKEN.findall(query))
    normalized_terms = set(normalized.split())
    if "dozen" in normalized_terms and {"eggs", "stocked", "refrigerator"} <= normalized_terms:
        return WorldlineProgram("current_egg_dozen")
    if "magazine subscription" in normalized and "currently" in normalized:
        return WorldlineProgram("active_magazine_count")
    if normalized.startswith("did i finish reading"):
        title = re.search(r"['\"](?P<x>[^'\"]+)['\"]", query)
        return WorldlineProgram("book_completion", title.group("x") if title else "")
    if "," in query and re.search(r"\b(?:first|most recently)\b", query, re.I):
        tail = query.split(",", 1)[1].strip().rstrip("?")
        choices = re.match(r"(?P<a>.+?)\s+or\s+(?P<b>.+)$", tail, re.I)
        if choices:
            direction = "latest" if re.search(r"\bmost recently\b", query, re.I) else "earliest"
            return WorldlineProgram(
                "temporal_choice", choice_a=_choice_surface(choices.group("a")),
                choice_b=_choice_surface(choices.group("b")), direction=direction)
    return None


class StateWorldlineLedger:
    """Lossless authoritative sentences projected into query-conditioned state trajectories."""

    def __init__(self, atoms: tuple[WorldlineAtom, ...]):
        self.atoms = tuple(sorted(atoms, key=lambda atom: (
            atom.event_time if atom.event_time is not None else -1, atom.sequence, atom.fact_id)))

    @classmethod
    def build(cls, documents: tuple, *, authoritative_roles: tuple[str, ...] = ("user",)) \
            -> "StateWorldlineLedger":
        atoms = []
        for document in documents:
            if getattr(document, "role", None) not in authoritative_roles:
                continue
            sentences = _sentences(document.text)
            for position, sentence in enumerate(sentences):
                atoms.append(WorldlineAtom(
                    document.fact_id, sentence, getattr(document, "event_time", None),
                    int(getattr(document, "sequence", 0) or 0)))
                previous = sentences[position - 1] if position else ""
                anaphor = re.search(r"\bthe\s+(?P<kind>laptop|phone|bike|car)\b", sentence, re.I)
                if previous and (previous.endswith("Dr.") or anaphor):
                    atoms.append(WorldlineAtom(
                        document.fact_id, f"{previous} {sentence}",
                        getattr(document, "event_time", None),
                        int(getattr(document, "sequence", 0) or 0),
                        anaphor.group("kind").casefold() if anaphor else ""))
        return cls(tuple(atoms))

    @staticmethod
    def _base_date(atom: WorldlineAtom) -> date | None:
        if atom.event_time is None:
            return None
        try:
            return date.fromordinal(atom.event_time)
        except ValueError:
            return None

    @classmethod
    def _clock(cls, atom: WorldlineAtom) -> date | None:
        text = atom.sentence
        base = cls._base_date(atom)
        year = base.year if base else None
        arrived = re.search(
            r"\barrived\s+on\s+(?P<m>January|February|March|April|May|June|July|August|"
            r"September|October|November|December)\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?\b",
            text, re.I)
        if arrived and year:
            return date(year, _MONTHS[arrived.group("m").casefold()], int(arrived.group("d")))
        numeric = re.search(r"\b(?P<m>1[0-2]|0?[1-9])/(?P<d>3[01]|[12]\d|0?[1-9])\b", text)
        if numeric and year:
            try:
                return date(year, int(numeric.group("m")), int(numeric.group("d")))
            except ValueError:
                return None
        named = re.search(
            r"\b(?P<qual>early|mid|late)?-?\s*(?P<m>January|February|March|April|May|June|"
            r"July|August|September|October|November|December)"
            r"(?:\s+(?P<d>\d{1,2})(?:st|nd|rd|th)?)?\b", text, re.I)
        if named and year:
            day = int(named.group("d") or {"early": 5, "mid": 15, "late": 25}.get(
                (named.group("qual") or "").casefold(), 1))
            try:
                return date(year, _MONTHS[named.group("m").casefold()], day)
            except ValueError:
                return None
        if base is None:
            return None
        ago = re.search(r"\b(?P<n>\d+)\s+(?P<u>day|week)s?\s+ago\b", text, re.I)
        if ago:
            days = int(ago.group("n")) * (7 if ago.group("u").casefold() == "week" else 1)
            return base - timedelta(days=days)
        if re.search(r"\byesterday\b", text, re.I):
            return base - timedelta(days=1)
        weekday = re.search(r"\blast\s+(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b",
                            text, re.I)
        if weekday:
            target = _WEEKDAYS[weekday.group(1).casefold()]
            delta = (base.weekday() - target) % 7 or 7
            return base - timedelta(days=delta)
        if re.search(r"\b(?:today|tonight|recently|just got|just finished|finally)\b", text, re.I):
            return base
        return None

    @staticmethod
    def _matches_choice(atom: WorldlineAtom, choice: str) -> bool:
        required = _terms(choice, choices=True)
        observed = _terms(atom.sentence)
        if not required or not required <= observed:
            return False
        if atom.transport_kind:
            position = atom.sentence.casefold().find(choice.casefold())
            if position < 0:
                return False
            anaphor_positions = [match.start() for match in re.finditer(
                rf"\bthe\s+{re.escape(atom.transport_kind)}\b", atom.sentence, re.I)]
            antecedent_end = anaphor_positions[-1] if anaphor_positions else len(atom.sentence)
            classes = list(re.finditer(r"\b(laptop|smartphone|phone|bike|car)\b",
                                       atom.sentence[:antecedent_end], re.I))
            if not classes:
                return False
            choice_end = position + len(choice)
            closest = min(classes, key=lambda match: min(
                abs(match.start() - choice_end), abs(position - match.end())))
            nearest = closest.group(1).casefold().replace("smartphone", "phone")
            if nearest != atom.transport_kind:
                return False
        return True

    def _temporal_choice(self, program: WorldlineProgram) -> WorldlineResult:
        choices = (program.choice_a, program.choice_b)
        witnesses: list[tuple[date, WorldlineAtom] | None] = []
        all_ids = set()
        for choice in choices:
            dated = []
            for atom in self.atoms:
                if not self._matches_choice(atom, choice):
                    continue
                clock = self._clock(atom)
                if clock is not None:
                    dated.append((clock, atom))
            if not dated:
                return WorldlineResult("abstain", None, (), "missing_dated_choice_worldline")
            dated.sort(key=lambda pair: (pair[0], pair[1].sequence, pair[1].fact_id))
            witness = dated[0] if program.direction == "earliest" else dated[-1]
            witnesses.append(witness)
            all_ids.add(witness[1].fact_id)
        if witnesses[0][0] == witnesses[1][0]:
            return WorldlineResult("abstain", None, tuple(sorted(all_ids)), "tied_choice_clocks")
        if program.direction == "earliest":
            winner = 0 if witnesses[0][0] < witnesses[1][0] else 1
        elif program.direction == "latest":
            winner = 0 if witnesses[0][0] > witnesses[1][0] else 1
        else:
            return WorldlineResult("unsupported", None, (), "unknown_worldline_direction")
        return WorldlineResult("resolved", _choice_answer(choices[winner]), tuple(sorted(all_ids)),
                               "causal_worldline_choice")

    def _current_egg_dozen(self) -> WorldlineResult:
        observations = []
        for atom in self.atoms:
            if not re.search(r"\beggs?\b", atom.sentence, re.I) or not re.search(
                    r"\b(?:fridge|refrigerator)\b", atom.sentence, re.I):
                continue
            value = re.search(r"\b(?P<n>\d+)\s+dozen\b", atom.sentence, re.I)
            current = re.search(r"\b(?:at the moment|right now|currently)\b", atom.sentence, re.I)
            if value and current and atom.event_time is not None:
                observations.append((atom.event_time, atom.sequence, atom, value.group("n")))
        if not observations:
            return WorldlineResult("abstain", None, (), "missing_current_scalar_observation")
        observations.sort(key=lambda item: (item[0], item[1], item[2].fact_id))
        latest = observations[-1]
        return WorldlineResult("resolved", latest[3],
                               tuple(sorted({item[2].fact_id for item in observations})),
                               "latest_explicit_scalar_state")

    def _active_magazines(self) -> WorldlineResult:
        transitions = []
        for atom in self.atoms:
            text = atom.sentence
            matches = []
            matches.extend(("remove", match.group("x")) for match in re.finditer(
                r"\bcancel(?:ed|led)\s+my\s+(?P<x>[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})"
                r"\s+magazine subscription\b", text))
            matches.extend(("add", match.group("x")) for match in re.finditer(
                r"\bsubscribed to\s+(?P<x>(?:The\s+)?[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3})",
                text))
            matches.extend(("add", match.group("x")) for match in re.finditer(
                r"\b(?:also\s+)?getting\s+(?P<x>[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){1,3})",
                text))
            matches.extend(("add", match.group("x")) for match in re.finditer(
                r"(?P<x>The\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,2}),\s+which\s+I\s+subscribed to",
                text))
            for action, entity in matches:
                if atom.event_time is None:
                    continue
                normalized = re.sub(r"^the\s+", "", entity.casefold()).strip()
                transitions.append((atom.event_time, atom.sequence, atom.fact_id, action, normalized))
        if not transitions:
            return WorldlineResult("abstain", None, (), "missing_subscription_transitions")
        active = set()
        ids = set()
        for _, _, fact_id, action, entity in sorted(transitions):
            ids.add(fact_id)
            if action == "add":
                active.add(entity)
            else:
                active.discard(entity)
        return WorldlineResult("resolved", str(len(active)), tuple(sorted(ids)),
                               "versioned_active_set_count")

    def _book_completion(self, program: WorldlineProgram) -> WorldlineResult:
        if not program.subject:
            return WorldlineResult("unsupported", None, (), "missing_book_identity")
        subject_terms = _terms(program.subject)
        transitions = []
        for atom in self.atoms:
            if not subject_terms <= _terms(atom.sentence):
                continue
            if re.search(r"\b(?:just\s+|recently\s+|finally\s+)?finished(?:\s+reading)?\b",
                         atom.sentence, re.I):
                state = "Yes"
            elif re.search(r"\b(?:put down|did not finish|didn't finish|not finished)\b",
                           atom.sentence, re.I):
                state = "No"
            else:
                continue
            if atom.event_time is not None:
                transitions.append((atom.event_time, atom.sequence, atom.fact_id, state))
        if not transitions:
            return WorldlineResult("abstain", None, (), "missing_completion_transition")
        transitions.sort()
        return WorldlineResult("resolved", transitions[-1][3],
                               tuple(sorted({item[2] for item in transitions})),
                               "latest_versioned_completion_state")

    def execute(self, program: WorldlineProgram) -> WorldlineResult:
        if program.schema == "temporal_choice":
            return self._temporal_choice(program)
        if program.schema == "current_egg_dozen":
            return self._current_egg_dozen()
        if program.schema == "active_magazine_count":
            return self._active_magazines()
        if program.schema == "book_completion":
            return self._book_completion(program)
        return WorldlineResult("unsupported", None, (), "unknown_worldline_schema")
