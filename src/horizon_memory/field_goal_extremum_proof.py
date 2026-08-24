# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Complete-enumeration min/max proofs for explicit field-goal distances."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re


_QUERY = re.compile(
    r"^how many yards was (?:the )?(?P<operator>longest|shortest) "
    r"(?:(?P<prefix_scope>first|second) half )?field goal"
    r"(?: (?:in|of) (?:the )?(?P<suffix_scope>game|first half|second half|"
    r"first quarter|second quarter|third quarter|fourth quarter))?\??$", re.I)
_SERIES = re.compile(
    r"(?P<series>\d{1,3}[- ]yard(?:er)?(?:\s*(?:,|and)\s*(?:a\s+)?"
    r"\d{1,3}[- ]yard(?:er)?)*)\s+field goals?\b(?!\s+attempt)", re.I)
_SHARED_UNIT_SERIES = re.compile(
    r"(?P<series>\d{1,3}(?:(?:\s*,\s*(?:and\s+(?:a\s+)?)?|"
    r"\s+and\s+(?:a\s+)?)\d{1,3})+)"
    r"[- ]yard(?:er)?\s+(?:field goals?|FG)\b(?!\s+attempt)", re.I)
_DASH_SHARED_SERIES = re.compile(
    r"(?P<first>\d{1,3})-\s*(?:,|and)\s*(?:a\s+)?(?P<second>\d{1,3})"
    r"[- ]yard(?:er)?\s+(?:field goals?|FG)\b", re.I)
_FROM = re.compile(
    r"\bfield goals?\b.{0,36}?\bfrom\s+(?P<value>\d{1,3})\s+yards?\s+out\b", re.I)
_BARE_GOAL = re.compile(r"(?P<value>\d{1,3})[- ]yard goal\b", re.I)
_ABBREVIATED_GOAL = re.compile(
    r"(?P<value>\d{1,3})\s*(?:[- ]yards?|[- ]yd)\s+FG\b", re.I)
_YARDER = re.compile(r"(?P<value>\d{1,3})[- ]yarder\b", re.I)
_BARE_FIELD = re.compile(r"(?P<value>\d{1,3})[- ]yard field\b", re.I)
_YARD_KICK = re.compile(r"(?P<value>\d{1,3})[- ]yard kick\b", re.I)
_MENTION = re.compile(r"\bfield goals?\b", re.I)
_NON_SCORE = re.compile(
    r"\b(?:attempt(?:ed)?|miss(?:ed)?|block(?:ed)?|no good|failed|"
    r"shank(?:ed)?|instead of|wide (?:left|right)|fell short)\b", re.I)
_SCOPE_MARKER = re.compile(
    r"\b(?:in|during|near (?:the )?end of|after (?:a )?scoreless)\s+(?:the\s+)?"
    r"(?P<scope>first|1st|second|2nd|third|3rd|fourth|4th)\s+quarter\b|"
    r"\b(?P<half>first|second) half\b|"
    r"\b(?P<after_break>after (?:the )?break)\b|"
    r"\b(?:the )?only score (?:in|of) (?:the )?"
    r"(?P<only_scope>first|1st|second|2nd|third|3rd|fourth|4th)\s+quarter\b|"
    r"\b(?P<poss_scope>first|1st|second|2nd|third|3rd|fourth|4th)\s+quarter['’]s\b|"
    r"\b(?:in|into|midway through|to start)\s+(?:the\s+)?"
    r"(?P<short_scope>first|1st|second|2nd|third|3rd|fourth|4th)\b"
    r"(?!\s+(?:half|quarter))|"
    r"\b(?P<halftime>halftime)\b|"
    r"\b(?P<overtime>(?:in|after|to start) overtime)\b|"
    r"\b(?P<rest_game>for the rest of the game)\b", re.I)
_SCOPE_MAP = {
    "first": "quarter:1", "1st": "quarter:1",
    "second": "quarter:2", "2nd": "quarter:2",
    "third": "quarter:3", "3rd": "quarter:3",
    "fourth": "quarter:4", "4th": "quarter:4",
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class FieldGoalDistance:
    value: int
    value_span: tuple[int, int]
    mention_span: tuple[int, int]
    text: str
    scope: str
    multiplicity: int = 1


@dataclass(frozen=True)
class FieldGoalExtremumProof:
    question_sha256: str
    passage_sha256: str
    operator: str
    scope: str
    observations: tuple[FieldGoalDistance, ...]
    rejected_mentions: tuple[tuple[int, int], ...]
    result: int

    def verify(self, question: str, passage: str) -> bool:
        if _sha(question) != self.question_sha256 or _sha(passage) != self.passage_sha256:
            return False
        return _compile_field_goal_extremum(question, passage) == self


def _scope_at(passage: str, position: int) -> str:
    sentence_start = max(passage.rfind(mark, 0, position) for mark in ".!?") + 1
    future_stops = [stop for mark in ".!?" if (stop := passage.find(mark, position)) >= 0]
    sentence_end = min(future_stops) + 1 if future_stops else len(passage)
    local = tuple(_SCOPE_MARKER.finditer(passage, sentence_start, sentence_end))
    prior = tuple(_SCOPE_MARKER.finditer(passage, 0, position))
    if local:
        before = tuple(marker for marker in local if marker.start() <= position)
        marker = before[-1] if before else local[0]
    elif prior:
        marker = prior[-1]
    else:
        future = tuple(_SCOPE_MARKER.finditer(passage, position))
        if future and (future[0].group("scope") or future[0].group("short_scope")):
            raw = future[0].group("scope") or future[0].group("short_scope")
            if _SCOPE_MAP[raw.casefold()] == "quarter:2":
                return "quarter:1"
        return "unknown"
    raw_scope = (marker.group("scope") or marker.group("only_scope")
                 or marker.group("poss_scope") or marker.group("short_scope"))
    if raw_scope:
        return _SCOPE_MAP[raw_scope.casefold()]
    if marker.group("after_break"):
        return "second_half"
    if marker.group("halftime"):
        return "first_half" if position < marker.start() else "second_half"
    if marker.group("overtime"):
        return "overtime"
    if marker.group("rest_game"):
        return "post_scope_unknown"
    return f"{marker.group('half').casefold()}_half"


def _observations(passage: str):
    observations: list[FieldGoalDistance] = []
    covered: list[tuple[int, int]] = []
    rejected: list[tuple[int, int]] = []

    def non_score(start: int, end: int) -> bool:
        left_floor = max(0, start - 48)
        left = max(passage.rfind(mark, left_floor, start) for mark in ".!?;,") + 1
        stops = [position for mark in ".!?;,"
                 if (position := passage.find(mark, end, min(len(passage), end + 32))) >= 0]
        right = min(stops) if stops else min(len(passage), end + 20)
        context = passage[left:right]
        invalidated = passage[end:min(len(passage), end + 220)]
        timeout_invalidation = (
            re.search(r"\bcalled timeout\b", invalidated, re.I) is not None
            and re.search(r"\bkick again\b", invalidated, re.I) is not None)
        return _NON_SCORE.search(context) is not None or timeout_invalidation

    def add(value: int, span: tuple[int, int], mention_span: tuple[int, int],
            multiplicity: int = 1) -> None:
        observations.append(FieldGoalDistance(
            value, span, mention_span, passage[span[0]:span[1]],
            _scope_at(passage, mention_span[0]), multiplicity))

    def reject(mention_span: tuple[int, int]) -> None:
        rejected.append(mention_span)
        covered.append(mention_span)

    for match in _DASH_SHARED_SERIES.finditer(passage):
        mention_span = match.span()
        if non_score(match.start(), match.end()):
            reject(mention_span)
            continue
        for group in ("first", "second"):
            add(int(match.group(group)), match.span(group), mention_span)
        covered.append(mention_span)

    for match in _SHARED_UNIT_SERIES.finditer(passage):
        mention_start = match.start() + match.group().casefold().rfind("field goal")
        mention_span = (mention_start, match.end())
        if non_score(match.start(), match.end()):
            reject(mention_span)
            continue
        for value in re.finditer(r"\d{1,3}", match.group("series")):
            span = (match.start("series") + value.start(), match.start("series") + value.end())
            add(int(value.group()), span, mention_span)
        covered.append(mention_span)
    for match in _SERIES.finditer(passage):
        mention_start = match.start() + match.group().casefold().rfind("field goal")
        mention_span = (mention_start, match.end())
        if any(start == mention_start for start, _end in covered):
            continue
        if non_score(match.start(), match.end()):
            reject(mention_span)
            continue
        multiplicity = 2 if re.search(
            r"\ba pair of\s*$", passage[max(0, match.start() - 20):match.start()], re.I) else 1
        for value in re.finditer(r"\d{1,3}(?=[- ]yard)", match.group("series"), re.I):
            span = (match.start("series") + value.start(), match.start("series") + value.end())
            add(int(value.group()), span, mention_span, multiplicity)
        covered.append(mention_span)
    for match in _FROM.finditer(passage):
        if any(start <= match.start() < end for start, end in covered):
            continue
        if non_score(match.start(), match.end()):
            mention = _MENTION.search(passage, match.start(), match.end())
            if mention is not None:
                reject(mention.span())
            continue
        span = match.span("value")
        mention = _MENTION.search(passage, match.start(), match.end())
        assert mention is not None
        add(int(match.group("value")), span, mention.span())
        covered.append(mention.span())
    for match in _BARE_GOAL.finditer(passage):
        prefix = passage[max(0, match.start() - 48):match.start()]
        if not re.search(r"\b(?:kicker|kick(?:er|ed|ing)?|nail(?:ed|ing)?|hit|made)\b", prefix, re.I):
            continue
        if non_score(match.start(), match.end()):
            reject(match.span())
            continue
        span = match.span("value")
        add(int(match.group("value")), span, match.span())

    for pattern in (_ABBREVIATED_GOAL, _YARDER, _BARE_FIELD, _YARD_KICK):
        for match in pattern.finditer(passage):
            if non_score(match.start(), match.end()):
                reject(match.span())
                continue
            context = passage[max(0, match.start() - 72):min(len(passage), match.end() + 40)]
            if pattern in {_YARDER, _BARE_FIELD, _YARD_KICK} and not re.search(
                    r"\b(?:field goal|kicker|kick(?:er|ed|ing)?|nail(?:ed|ing)?|"
                    r"connect(?:ed|ing)?|convert(?:ed|ing)?)\b", context, re.I):
                continue
            span = match.span("value")
            add(int(match.group("value")), span, match.span())

    for mention in _MENTION.finditer(passage):
        if any(start <= mention.start() < end for start, end in covered):
            continue
        if non_score(mention.start(), mention.end()):
            rejected.append(mention.span())
            continue
        return None
    unique = {item.value_span: item for item in observations}
    return tuple(sorted(unique.values(), key=lambda item: item.value_span)), tuple(rejected)


def _selected_scope(observations: tuple[FieldGoalDistance, ...], scope: str):
    if scope == "game":
        return observations
    if scope == "first_half":
        return tuple(item for item in observations
                     if item.scope in {"first_half", "quarter:1", "quarter:2"})
    if scope == "second_half":
        return tuple(item for item in observations
                     if item.scope in {"second_half", "quarter:3", "quarter:4"})
    quarter = {"first_quarter": "quarter:1", "second_quarter": "quarter:2",
               "third_quarter": "quarter:3", "fourth_quarter": "quarter:4"}[scope]
    return tuple(item for item in observations if item.scope == quarter)


def _compile_field_goal_extremum(question: str, passage: str) -> FieldGoalExtremumProof | None:
    match = _QUERY.fullmatch(question.strip())
    if match is None or not passage:
        return None
    compiled = _observations(passage)
    if compiled is None or not compiled[0]:
        return None
    observations, rejected = compiled
    raw_scope = ((match.group("prefix_scope") + " half")
                 if match.group("prefix_scope") else match.group("suffix_scope") or "game")
    scope = raw_scope.casefold().replace(" ", "_")
    selected = _selected_scope(observations, scope)
    if not selected:
        return None
    values = tuple(item.value for item in selected)
    operator = match.group("operator").casefold()
    result = max(values) if operator == "longest" else min(values)
    return FieldGoalExtremumProof(
        _sha(question), _sha(passage), operator, scope, observations, rejected, result)


def compile_field_goal_extremum(question: str, passage: str) -> FieldGoalExtremumProof | None:
    """Compile a whole-game field-goal extremum after a closed mention audit."""
    return _compile_field_goal_extremum(question, passage)


__all__ = ["FieldGoalDistance", "FieldGoalExtremumProof", "compile_field_goal_extremum"]
