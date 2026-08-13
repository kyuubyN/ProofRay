# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic compiler for typed numerator/denominator records."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re


# Decimal points in box-score values (for example ``115.0``) are not sentence
# boundaries; splitting there can sever a coordinated terminal record.
_SENTENCE = re.compile(r".+?(?:(?<!\d)[.!?]+(?!\d)|$)")
_NAME = r"(?:[A-Z][A-Za-z'’.-]*(?:\s+[A-Z][A-Za-z'’.-]*){0,4})"
_COMPLETED = re.compile(
    rf"\b(?P<actor>{_NAME})\s+completed\s+(?P<numerator>\d{{1,3}})\s+of\s+"
    r"(?P<denominator>\d{1,3})\s+(?P<unit>passes)\b"
)
_GAME_FRAME_COMPLETED = re.compile(
    rf"\b(?P<actor>{_NAME})\s+(?:finished|ended)\s+the\s+game\s+(?:having\s+)?"
    r"completed\s+(?P<numerator>\d{1,3})\s+of\s+"
    r"(?P<denominator>\d{1,3})\s+(?P<unit>passes)\b"
)
_TERMINAL_STATS = re.compile(
    rf"\b(?P<actor>{_NAME})\s+(?:would\s+)?(?:finish|finished|end|ended)\s+the\s+game\s+"
    r"with\s+(?:stats\s+of\s+)?(?P<numerator>\d{1,3})\s+of\s+"
    r"(?P<denominator>\d{1,3})\s+(?P<unit>passes)\s+(?:completed|complete)\b"
)
_COORDINATED_TERMINAL = re.compile(
    rf"\b(?P<actor>{_NAME})\s+completed\s+\d{{1,3}}\s+of\s+\d{{1,3}}\s+passes\b"
    r".{0,300}?\band\s+(?:would\s+)?(?:finish|finished|end|ended)\s+the\s+game\s+"
    r"with\s+(?:stats\s+of\s+)?(?P<numerator>\d{1,3})\s+of\s+"
    r"(?P<denominator>\d{1,3})\s+(?P<unit>passes)\s+(?:completed|complete)\b"
)
_CLAUSE_SCOPE = re.compile(
    r"\b(?:in|during|for|on)\s+(?P<label>this game|the game|the contest|the day|"
    r"the win|the victory|the loss|the defeat|the first half|the second half|"
    r"the first quarter|the second quarter|the third quarter|the fourth quarter)\b",
    re.IGNORECASE,
)
_LEADING_SCOPE = re.compile(
    r"^\s*(?:In|During|On)\s+(?P<label>this game|the game|the contest|the day|"
    r"the first half|the second half|the first quarter|the second quarter|"
    r"the third quarter|the fourth quarter)\s*,",
)
_TITLE_TOKENS = frozenset({"qb", "quarterback", "starter", "veteran", "rookie", "backup"})


def _actor_tokens(actor: str) -> tuple[str, ...]:
    tokens = (
        token.casefold().strip(".'’-") for token in actor.split()
        if token.casefold().strip(".'’-") not in _TITLE_TOKENS
    )
    return tuple(token for token in tokens if token)


def _actor_matches(query: str, evidence: str) -> bool:
    left, right = _actor_tokens(query), _actor_tokens(evidence)
    return bool(left and right and (left == right or left[-1] == right[-1]))


def _scope(label: str) -> str:
    folded = label.casefold()
    if folded in {
        "this game", "the game", "the contest", "the day", "the win",
        "the victory", "the loss", "the defeat",
    }:
        return "whole_game"
    return {
        "the first half": "first_half", "the second half": "second_half",
        "the first quarter": "quarter:1", "the second quarter": "quarter:2",
        "the third quarter": "quarter:3", "the fourth quarter": "quarter:4",
    }[folded]


@dataclass(frozen=True)
class TypedRatioRecord:
    source_span: tuple[int, int]
    sentence_span: tuple[int, int]
    actor: str
    unit: str
    scope: str
    numerator: int
    denominator: int
    authorized: bool
    text: str
    reason: str

    def value(self, metric: str) -> int:
        if metric == "numerator":
            return self.numerator
        if metric == "denominator":
            return self.denominator
        if metric == "complement":
            return self.denominator - self.numerator
        raise ValueError(f"unsupported typed-ratio metric: {metric}")


@dataclass(frozen=True)
class TypedRatioResolution:
    state: str
    value: int | None
    records: tuple[TypedRatioRecord, ...]
    reason: str


@dataclass(frozen=True)
class TypedRatioIndex:
    source_sha256: str
    records: tuple[TypedRatioRecord, ...]

    def resolve(self, *, actor: str, unit: str, scope: str, metric: str) -> TypedRatioResolution:
        records = tuple(
            item for item in self.records
            if item.authorized and item.unit == unit and item.scope == scope
            and _actor_matches(actor, item.actor)
        )
        if not records:
            return TypedRatioResolution("unsupported", None, (), "no exact typed ratio authority")
        values = {item.value(metric) for item in records}
        if len(values) != 1:
            return TypedRatioResolution("conflict", None, records, "typed ratio authorities disagree")
        return TypedRatioResolution("closed", next(iter(values)), records,
                                    "all exact typed ratio authorities agree")

    def verify(self, source: str) -> bool:
        if hashlib.sha256(source.encode()).hexdigest() != self.source_sha256:
            return False
        return all(
            0 <= item.source_span[0] < item.source_span[1] <= len(source)
            and source[item.source_span[0]:item.source_span[1]] == item.text
            for item in self.records
        )


def build_typed_ratio_index(source: str) -> TypedRatioIndex:
    if not source:
        raise ValueError("typed ratio index requires non-empty source text")
    records: list[TypedRatioRecord] = []
    for sentence_match in _SENTENCE.finditer(source):
        sentence = sentence_match.group()
        coordinated = list(_COORDINATED_TERMINAL.finditer(sentence))
        terminal_matches = coordinated + [
            match for match in _TERMINAL_STATS.finditer(sentence)
            if not any(start <= match.start() and match.end() <= end
                       for start, end in (item.span() for item in coordinated))
        ]
        for match in terminal_matches:
            numerator, denominator = int(match.group("numerator")), int(match.group("denominator"))
            valid = 0 <= numerator <= denominator
            start, end = sentence_match.start() + match.start(), sentence_match.start() + match.end()
            records.append(TypedRatioRecord(
                (start, end), (sentence_match.start(), sentence_match.end()), match.group("actor"),
                match.group("unit").casefold(), "whole_game", numerator, denominator, valid,
                source[start:end], "literal terminal game record" if valid else "negative complement",
            ))
        game_frame_spans = {match.span() for match in _GAME_FRAME_COMPLETED.finditer(sentence)}
        matches = list(_GAME_FRAME_COMPLETED.finditer(sentence))
        matches.extend(
            match for match in _COMPLETED.finditer(sentence)
            if not any(start <= match.start() and match.end() <= end for start, end in game_frame_spans)
        )
        for match in matches:
            framed = match.re is _GAME_FRAME_COMPLETED
            clause_tail = re.split(r",\s+and\b|;", sentence[match.end():], maxsplit=1)[0]
            attached = _CLAUSE_SCOPE.search(clause_tail)
            leading = _LEADING_SCOPE.match(sentence)
            if framed:
                scope = "whole_game"
            elif attached:
                scope = _scope(attached.group("label"))
            elif leading:
                scope = _scope(leading.group("label"))
            else:
                scope = "local_unknown"
            numerator, denominator = int(match.group("numerator")), int(match.group("denominator"))
            arithmetic_valid = 0 <= numerator <= denominator
            authorized = scope != "local_unknown" and arithmetic_valid
            reason = (
                "negative complement in typed ratio" if not arithmetic_valid
                else "explicit exact-scope ratio record" if authorized
                else "typed ratio lacks an exact temporal scope"
            )
            start, end = sentence_match.start() + match.start(), sentence_match.start() + match.end()
            records.append(TypedRatioRecord(
                (start, end), (sentence_match.start(), sentence_match.end()), match.group("actor"),
                match.group("unit").casefold(), scope, numerator, denominator, authorized,
                source[start:end], reason,
            ))
    return TypedRatioIndex(hashlib.sha256(source.encode()).hexdigest(), tuple(records))
