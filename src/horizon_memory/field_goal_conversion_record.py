# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compile explicit field-goal conversion records into checked algebra.

This surface accepts records that state both a metric and an exact temporal scope,
for example ``missed two of his three field goals during the game``.  It derives
only identities entailed by that record: ``made = attempts - missed`` and, when an
exhaustive yard list is present, filters and arithmetic over that list.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import re


_SENTENCE = re.compile(r"[^.!?]+(?:[.!?]+|$)")
_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
    "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
}
_NUMBER = r"(?:zero|one|two|three|four|five|six|seven|eight|nine|\d{1,2})"
_NAME = r"(?:[A-Z][A-Za-z'’.-]*(?:\s+[A-Z][A-Za-z'’.-]*){0,4})"
_YARDS = r"(?:\s*\((?P<yards>\d{1,3}(?:\s*,\s*\d{1,3})+)\))?"
_MADE_OF = re.compile(
    rf"\b(?P<actor>{_NAME})\s+(?:converted|made|hit)\s+"
    rf"(?P<made>{_NUMBER})\s+(?:of|for)\s+(?P<attempts>{_NUMBER})\s+field goals?\b"
    + _YARDS
)
_SLASH = re.compile(
    rf"\b(?P<actor>{_NAME})\s+was\s+(?P<made>\d{{1,2}})\s*/\s*"
    rf"(?P<attempts>\d{{1,2}})\s+on\s+field goals?\b" + _YARDS
)
_MISSED_OF = re.compile(
    rf"\b(?P<actor>{_NAME})\s+missed\s+(?P<missed>{_NUMBER})\s+of\s+"
    rf"(?:his|her|their)\s+(?P<attempts>{_NUMBER})\s+field goals?\b"
)
_DIRECT_MISSED = re.compile(
    rf"\b(?P<actor>{_NAME})\s+missed\s+(?P<missed>{_NUMBER})\s+field goals?\b"
)
_TRAILING_SCOPE = re.compile(
    r"^\s*(?:in|during|for|on)\s+(?P<label>this game|the game|the contest|the day|"
    r"the win|the victory|the loss|the defeat|the first half|the second half|"
    r"the first quarter|the second quarter|the third quarter|the fourth quarter)\b|"
    r"^\s+in\s+total\b",
    re.IGNORECASE,
)
_LEADING_SCOPE = re.compile(
    r"^\s*(?:In|During)\s+(?P<label>this game|the game|the contest|the first half|"
    r"the second half|the first quarter|the second quarter|the third quarter|"
    r"the fourth quarter)\s*,"
)
_TITLE_TOKENS = frozenset({"k", "kicker", "placekicker", "veteran", "rookie", "former"})


def _number(raw: str) -> int:
    folded = raw.casefold()
    return int(folded) if folded.isdigit() else _NUMBER_WORDS[folded]


def _scope(label: str) -> str:
    folded = re.sub(r"\s+", " ", label.casefold()).strip()
    if folded in {
        "this game", "the game", "the contest", "the day", "the win",
        "the victory", "the loss", "the defeat", "in total",
    }:
        return "whole_game"
    return {
        "the first half": "first_half", "the second half": "second_half",
        "the first quarter": "quarter:1", "the second quarter": "quarter:2",
        "the third quarter": "quarter:3", "the fourth quarter": "quarter:4",
    }[folded]


def _explicit_scope(sentence: str, evidence_end: int) -> tuple[str, str]:
    trailing = _TRAILING_SCOPE.match(sentence[evidence_end:])
    if trailing:
        return _scope(trailing.group("label") or "in total"), "attached scope"
    leading = _LEADING_SCOPE.match(sentence)
    if leading:
        return _scope(leading.group("label")), "sentence-frame scope"
    return "local_unknown", "conversion record has no exact temporal scope"


def _actor_tokens(actor: str) -> tuple[str, ...]:
    tokens = (
        token.casefold().strip(".'’-") for token in actor.split()
        if token.casefold().strip(".'’-") not in _TITLE_TOKENS
    )
    return tuple(token for token in tokens if token)


def _actor_matches(query_actor: str, evidence_actor: str) -> bool:
    query = _actor_tokens(query_actor)
    evidence = _actor_tokens(evidence_actor)
    return bool(query and evidence and (query == evidence or query[-1] == evidence[-1]))


@dataclass(frozen=True)
class FieldGoalConversionRecord:
    source_span: tuple[int, int]
    sentence_span: tuple[int, int]
    actor: str
    scope: str
    made: int | None
    missed: int | None
    attempts: int | None
    yards: tuple[int, ...]
    authorized: bool
    kind: str
    text: str
    reason: str

    def value(
        self, metric: str, *, lower: int | None = None, upper: int | None = None,
        lower_inclusive: bool = True, upper_inclusive: bool = True,
    ) -> int | Fraction | None:
        if metric in {"made", "missed", "attempts"}:
            return getattr(self, metric)
        if self.made is None or len(self.yards) != self.made:
            return None
        if metric == "yard_sum":
            return sum(self.yards)
        if metric == "yard_max":
            return max(self.yards) if self.yards else None
        if metric == "yard_average":
            return Fraction(sum(self.yards), len(self.yards)) if self.yards else None
        if metric == "range_count":
            def selected(value: int) -> bool:
                above = lower is None or value > lower or (lower_inclusive and value == lower)
                below = upper is None or value < upper or (upper_inclusive and value == upper)
                return above and below
            return sum(selected(value) for value in self.yards)
        raise ValueError(f"unsupported conversion-record metric: {metric}")


@dataclass(frozen=True)
class ConversionResolution:
    state: str
    value: int | Fraction | None
    records: tuple[FieldGoalConversionRecord, ...]
    reason: str


@dataclass(frozen=True)
class FieldGoalConversionIndex:
    source_sha256: str
    records: tuple[FieldGoalConversionRecord, ...]

    def resolve(
        self, *, actor: str, scope: str, metric: str,
        lower: int | None = None, upper: int | None = None,
        lower_inclusive: bool = True, upper_inclusive: bool = True,
    ) -> ConversionResolution:
        candidates = tuple(
            record for record in self.records
            if record.authorized and record.scope == scope and _actor_matches(actor, record.actor)
        )
        valued = tuple(
            (record, record.value(
                metric, lower=lower, upper=upper,
                lower_inclusive=lower_inclusive, upper_inclusive=upper_inclusive,
            ))
            for record in candidates
        )
        authorities = tuple(record for record, value in valued if value is not None)
        values = {value for _, value in valued if value is not None}
        if not values:
            return ConversionResolution("unsupported", None, (), "no exact-scope record entails metric")
        if len(values) != 1:
            return ConversionResolution("conflict", None, authorities, "conversion records disagree")
        return ConversionResolution("closed", next(iter(values)), authorities,
                                    "all exact-scope conversion records agree")

    def verify(self, source: str) -> bool:
        if hashlib.sha256(source.encode()).hexdigest() != self.source_sha256:
            return False
        return all(
            0 <= record.source_span[0] < record.source_span[1] <= len(source)
            and source[record.source_span[0]:record.source_span[1]] == record.text
            for record in self.records
        )


def _record(
    source: str, sentence: str, sentence_start: int, sentence_end: int,
    match: re.Match[str], *, made: int | None, missed: int | None,
    attempts: int | None, kind: str,
) -> FieldGoalConversionRecord:
    scope, scope_reason = _explicit_scope(sentence, match.end())
    yards_raw = match.groupdict().get("yards")
    yards = tuple(int(value) for value in re.findall(r"\d{1,3}", yards_raw or ""))
    arithmetic_valid = (
        attempts is None or (
            made is not None and missed is not None and made >= 0 and missed >= 0
            and made + missed == attempts
        )
    )
    yards_valid = not yards or made is not None and len(yards) == made
    authorized = scope != "local_unknown" and arithmetic_valid and yards_valid
    if not arithmetic_valid:
        reason = "conversion arithmetic is inconsistent"
    elif not yards_valid:
        reason = "yard list cardinality disagrees with made count"
    else:
        reason = scope_reason
    start, end = sentence_start + match.start(), sentence_start + match.end()
    return FieldGoalConversionRecord(
        (start, end), (sentence_start, sentence_end), match.group("actor"), scope,
        made, missed, attempts, yards, authorized, kind, source[start:end], reason,
    )


def build_field_goal_conversion_index(source: str) -> FieldGoalConversionIndex:
    if not source:
        raise ValueError("conversion index requires non-empty source text")
    records: list[FieldGoalConversionRecord] = []
    for sentence_match in _SENTENCE.finditer(source):
        sentence = sentence_match.group()
        occupied: list[tuple[int, int]] = []
        for pattern, kind in ((_MADE_OF, "made_of_attempts"), (_SLASH, "slash_record")):
            for match in pattern.finditer(sentence):
                made, attempts = _number(match.group("made")), _number(match.group("attempts"))
                records.append(_record(
                    source, sentence, sentence_match.start(), sentence_match.end(), match,
                    made=made, missed=attempts - made, attempts=attempts, kind=kind,
                ))
                occupied.append(match.span())
        for match in _MISSED_OF.finditer(sentence):
            missed, attempts = _number(match.group("missed")), _number(match.group("attempts"))
            records.append(_record(
                source, sentence, sentence_match.start(), sentence_match.end(), match,
                made=attempts - missed, missed=missed, attempts=attempts, kind="missed_of_attempts",
            ))
            occupied.append(match.span())
        for match in _DIRECT_MISSED.finditer(sentence):
            if any(start <= match.start() < end for start, end in occupied):
                continue
            records.append(_record(
                source, sentence, sentence_match.start(), sentence_match.end(), match,
                made=None, missed=_number(match.group("missed")), attempts=None,
                kind="direct_missed_aggregate",
            ))
    return FieldGoalConversionIndex(hashlib.sha256(source.encode()).hexdigest(), tuple(records))
