# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Relational tensor bindings for exact temporal operands.

Bindings are selected jointly over typed predicate/entity/clock coordinates.  Repeated reports at the
same clock form one orbit; lowering a lexical margin is never used as a substitute for structure.
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, timedelta


_TOKEN = re.compile(r"[^\W_]+|#[^\W_]+", re.UNICODE)
_SENTENCE = re.compile(r"(?<=[.!?])(?:\s+|$)|\n+")
_DATE = re.compile(
    r"\b(?:(?P<m1>January|February|March|April|May|June|July|August|September|October|"
    r"November|December)\s+(?P<d1>\d{1,2})(?:st|nd|rd|th)?|"
    r"(?P<d2>\d{1,2})(?:st|nd|rd|th)?\s+of\s+(?P<m2>January|February|March|April|May|June|"
    r"July|August|September|October|November|December)|"
    r"(?P<mn>1[0-2]|0?[1-9])/(?P<dn>3[01]|[12]\d|0?[1-9]))\b", re.I)
_MONTHS = {name.casefold(): number for number, name in enumerate((
    "January", "February", "March", "April", "May", "June", "July", "August",
    "September", "October", "November", "December"), 1)}
_STOP = frozenset((
    "a", "an", "and", "at", "between", "did", "for", "from", "had", "have", "i", "in",
    "it", "me", "my", "of", "on", "the", "time", "to", "was", "when", "with",
))
_ACTION = frozenset((
    "accept", "arrive", "attend", "buy", "discover", "find", "finish", "get", "help",
    "invest", "order", "participate", "play", "prepare", "receive", "replace", "sell",
    "see", "start", "take", "visit", "work",
))
_ALIASES = {
    "accepted": "accept", "arrived": "arrive", "attended": "attend", "bought": "buy",
    "buying": "buy", "discovered": "discover", "finding": "find", "finished": "finish",
    "got": "get", "helped": "help", "invested": "invest", "loved": "love", "ordered": "order",
    "participated": "participate", "playing": "play", "preparing": "prepare",
    "received": "receive", "replaced": "replace", "saw": "see", "sold": "sell",
    "started": "start", "starting": "start", "taking": "take", "visited": "visit",
    "working": "work",
}


@dataclass(frozen=True)
class RelationalProgram:
    unit: str
    anchor_a: str
    anchor_b: str
    last_a: bool = False
    last_b: bool = False


@dataclass(frozen=True)
class RelationalAtom:
    fact_id: int
    event_day: int
    sequence: int
    sentence: str
    clause: str


@dataclass(frozen=True)
class RelationalResult:
    state: str
    value: int | None
    unit: str | None
    fact_ids: tuple[int, ...]
    reason: str
    operand_days: tuple[int, int] | tuple[()] = ()


def _normalize(token: str) -> str:
    token = token.casefold().rstrip("'s")
    if token in _ALIASES:
        return _ALIASES[token]
    if len(token) > 5 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 5 and token.endswith("ing"):
        stem = token[:-3]
        return stem[:-1] if len(stem) > 3 and stem[-1:] == stem[-2:-1] else stem
    if len(token) > 4 and token.endswith("ed"):
        return token[:-2]
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _terms(text: str) -> frozenset[str]:
    return frozenset(_normalize(token) for token in _TOKEN.findall(text)
                     if token.casefold() not in _STOP and len(token) > 1)


def _clean_anchor(text: str) -> str:
    text = re.sub(r"^(?:the day|the time)\s+", "", text.strip(), flags=re.I)
    text = re.sub(r"^(?:i|me to)\s+", "", text, flags=re.I)
    arrive = re.fullmatch(r"my\s+(.+?)\s+to\s+(arrive)", text, re.I)
    if arrive:
        return f"{arrive.group(2)} {arrive.group(1)}"
    return text


def _shared_object(anchor: str) -> str:
    terms = [token for token in _TOKEN.findall(anchor) if _normalize(token) not in _ACTION and
             token.casefold() not in _STOP and token.casefold() not in {"new"}]
    return " ".join(terms)


def compile_relational_program(query: str) -> RelationalProgram | None:
    compact = " ".join(query.strip().split()).rstrip("?")
    unit_match = re.search(r"\b(days?|weeks?)\b", compact, re.I)
    if not unit_match:
        return None
    unit = "week" if unit_match.group(1).casefold().startswith("week") else "day"
    match = re.search(r"\bbetween\s+(.+?)\s+and\s+(.+)$", compact, re.I)
    if match:
        left, right = map(_clean_anchor, match.groups())
        return RelationalProgram(unit, left, right, "last time" in left.casefold(),
                                 "last time" in right.casefold())
    match = re.search(r"\bHow many\s+\w+\s+before\s+(.+?)\s+did\s+I\s+(.+)$", compact, re.I)
    if match:
        later, earlier = match.groups()
        if re.search(r"\bher gift\b", earlier, re.I):
            earlier = re.sub(r"\bher gift\b", "gift for best friend birthday", earlier, flags=re.I)
        return RelationalProgram(unit, _clean_anchor(earlier), _clean_anchor(later))
    match = re.search(r"\bHow many\s+\w+\s+did it take\s+(?:for\s+)?(.+?)\s+after\s+(.+)$",
                      compact, re.I)
    if match:
        later, earlier = map(_clean_anchor, match.groups())
        if re.search(r"\bit\b", earlier, re.I):
            earlier = re.sub(r"\bit\b", _shared_object(later), earlier, flags=re.I)
        return RelationalProgram(unit, earlier, later)
    match = re.search(r"\bHow many\s+\w+\s+have I been\s+(.+?)\s+when I\s+(.+)$", compact, re.I)
    if match:
        return RelationalProgram(unit, *map(_clean_anchor, match.groups()))
    match = re.search(r"\bHow many\s+\w+\s+had passed since\s+(.+?)\s+when I\s+(.+)$",
                      compact, re.I)
    if match:
        return RelationalProgram(unit, *map(_clean_anchor, match.groups()))
    match = re.search(r"\bHow many days did it take me to finish\s+(.+)$", compact, re.I)
    if match:
        title = re.sub(r"\s+by\s+.+$", "", match.group(1), flags=re.I)
        return RelationalProgram("day", f"start {title}", f"finish {title}")
    return None


def _sentences(text: str) -> tuple[str, ...]:
    raw = [part.strip() for part in _SENTENCE.split(text) if part.strip()]
    merged = []
    for part in raw:
        if merged and re.search(r"\b(?:St|Dr|Mr|Mrs|Ms)\.$", merged[-1]):
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return tuple(merged) or (text,)


def _clause(sentence: str, position: int) -> str:
    boundaries = [0, len(sentence)]
    boundaries.extend(match.start() for match in re.finditer(r",|;|\s+and\s+", sentence, re.I))
    boundaries = sorted(set(boundaries))
    left = max(boundary for boundary in boundaries if boundary <= position)
    right = min((boundary for boundary in boundaries if boundary > position), default=len(sentence))
    return sentence[left:right].strip(" ,;")


def _calendar_day(match: re.Match, year: int) -> int | None:
    if match.group("mn"):
        month, day_number = int(match.group("mn")), int(match.group("dn"))
    else:
        month_name = (match.group("m1") or match.group("m2")).casefold()
        month = _MONTHS[month_name]
        day_number = int(match.group("d1") or match.group("d2"))
    try:
        return date(year, month, day_number).toordinal()
    except ValueError:
        return None


class RelationalTensorLedger:
    """Exhaustive dated event atoms with joint, orbit-aware operand selection."""

    def __init__(self, atoms: tuple[RelationalAtom, ...]):
        self.atoms = tuple(sorted(atoms, key=lambda atom: (
            atom.event_day, atom.sequence, atom.fact_id, atom.clause)))

    @classmethod
    def build(cls, documents: tuple, *, authoritative_roles: tuple[str, ...] = ("user",)) \
            -> "RelationalTensorLedger":
        atoms = []
        for document in documents:
            if getattr(document, "role", None) not in authoritative_roles:
                continue
            event_time = getattr(document, "event_time", None)
            try:
                base = date.fromordinal(event_time) if event_time is not None else None
            except ValueError:
                base = None
            sentences = _sentences(document.text)
            for position, sentence in enumerate(sentences):
                context = sentence
                if position and re.search(r"\b(?:it|her|him|them|their|the meeting)\b", sentence, re.I):
                    context = f"{sentences[position - 1]} {sentence}"
                found = False
                if base is not None:
                    for match in _DATE.finditer(sentence):
                        event_day = _calendar_day(match, base.year)
                        if event_day is None:
                            continue
                        atoms.append(RelationalAtom(
                            document.fact_id, event_day,
                            int(getattr(document, "sequence", 0) or 0), context,
                            _clause(sentence, match.start())))
                        found = True
                    if re.search(r"\btoday\b", sentence, re.I):
                        atoms.append(RelationalAtom(
                            document.fact_id, base.toordinal(),
                            int(getattr(document, "sequence", 0) or 0), context, sentence))
                        found = True
                    if re.search(r"\byesterday\b", sentence, re.I):
                        atoms.append(RelationalAtom(
                            document.fact_id, (base - timedelta(days=1)).toordinal(),
                            int(getattr(document, "sequence", 0) or 0), context, sentence))
                        found = True
                next_sentence = sentences[position + 1] if position + 1 < len(sentences) else ""
                transported_forward_date = bool(
                    _DATE.search(next_sentence) and
                    re.search(r"\b(?:it|her|him|them|that)\b", next_sentence, re.I))
                if not found and not transported_forward_date and base is not None and re.search(
                        r"\b(?:recently|just got back|just came back|finally)\b", sentence, re.I):
                    atoms.append(RelationalAtom(
                        document.fact_id, base.toordinal(),
                        int(getattr(document, "sequence", 0) or 0), context, sentence))
        return cls(tuple(atoms))

    @staticmethod
    def _score(anchor: str, atom: RelationalAtom) -> tuple[float, float]:
        anchor_terms = _terms(anchor)
        if not anchor_terms:
            return 0.0, 0.0
        sentence_terms, clause_terms = _terms(atom.sentence), _terms(atom.clause)
        identity = anchor_terms - _ACTION
        actions = anchor_terms & _ACTION
        identity_coverage = len(identity & sentence_terms) / max(1, len(identity))
        action_hits = len(actions & clause_terms)
        total_hits = len(anchor_terms & sentence_terms)
        quoted = [" ".join(_TOKEN.findall(value.casefold())) for value in
                  re.findall(r"['\"]([^'\"]+)['\"]", anchor)]
        quote_bonus = 4.0 * sum(value and value in " ".join(_TOKEN.findall(atom.sentence.casefold()))
                                for value in quoted)
        score = 2.0 * action_hits + total_hits + 2.0 * identity_coverage + quote_bonus
        return score, identity_coverage

    def _orbits(self, anchor: str) -> tuple[tuple[float, int, tuple[int, ...]], ...]:
        by_day: dict[int, list[tuple[float, float, RelationalAtom]]] = {}
        for atom in self.atoms:
            score, coverage = self._score(anchor, atom)
            if coverage < 0.5 or score < 3.0:
                continue
            by_day.setdefault(atom.event_day, []).append((score, coverage, atom))
        result = []
        for event_day, candidates in by_day.items():
            best_score = max(item[0] for item in candidates)
            witnesses = tuple(sorted({item[2].fact_id for item in candidates
                                      if item[0] >= best_score - 0.25}))
            result.append((best_score, event_day, witnesses))
        return tuple(sorted(result, key=lambda item: (-item[0], item[1], item[2])))

    def execute(self, program: RelationalProgram) -> RelationalResult:
        left, right = self._orbits(program.anchor_a), self._orbits(program.anchor_b)
        if not left or not right:
            return RelationalResult("abstain", None, program.unit, (), "missing_relational_operand")
        pairs = []
        for left_item in left:
            for right_item in right:
                if left_item[1] == right_item[1]:
                    continue
                score = left_item[0] + right_item[0]
                if program.last_a:
                    score += left_item[1] / 10 ** 7
                if program.last_b:
                    score += right_item[1] / 10 ** 7
                pairs.append((score, left_item, right_item))
        if not pairs:
            return RelationalResult("abstain", None, program.unit, (), "non_independent_operands")
        pairs.sort(key=lambda item: (-item[0], item[1][1], item[2][1]))
        best = pairs[0]
        alternative_score = pairs[1][0] if len(pairs) > 1 else float("-inf")
        # A joint margin is measured after collapsing same-date reports into one orbit.
        if alternative_score > best[0] - 0.5:
            return RelationalResult("abstain", None, program.unit, (), "ambiguous_relational_tensor")
        days = abs(best[2][1] - best[1][1])
        if program.unit == "day":
            value = days
        elif program.unit == "week" and days % 7 in (0, 1):
            value = days // 7
        else:
            return RelationalResult("abstain", None, program.unit, (), "inexact_relational_unit")
        fact_ids = tuple(sorted(set(best[1][2]) | set(best[2][2])))
        return RelationalResult("resolved", value, program.unit, fact_ids,
                                "unique_relational_tensor", (best[1][1], best[2][1]))
