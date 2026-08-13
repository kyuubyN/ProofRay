# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compile explicit terminal game/day summaries into typed scalar facts."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re


_SENTENCE = re.compile(r".+?(?:(?<!\d)[.!?]+(?!\d)|$)")
_NAME = r"(?:[A-Z][A-Za-z'’.-]*(?:\s+[A-Z][A-Za-z'’.-]*){0,5})"
_FRAME = re.compile(
    rf"\b(?P<actor>{_NAME})\s+(?:would\s+)?(?:finish(?:ed)?|end(?:ed)?)\s+"
    r"(?:the\s+)?(?P<frame>game|day)\s+with\s+(?P<body>.+)$"
)
_NUMBER_WORDS = {
    "no": 0, "zero": 0, "a": 1, "an": 1, "one": 1, "two": 2,
    "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
    "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20,
}
_NUMBER = r"(?:no|zero|a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|\d{1,4})"
_TUPLE = re.compile(
    rf"(?<![\d.])\b(?P<count>{_NUMBER})(?!\.\d)\s+"
    r"(?:(?P<premod>rushing|receiving|passing|total)\s+)?"
    r"(?P<unit>yards?|touchdowns?|interceptions?|receptions?|catches|carries|sacks|"
    r"completions?|passes)"
    r"(?:\s+(?P<postmod>rushing|receiving|passing))?\b",
    re.IGNORECASE,
)
_FRAME_PREFIX = re.compile(r"^\s*(?:(?:career|season)-high\s+)?$", re.IGNORECASE)
_TUPLE_SEPARATOR = re.compile(
    r"^\s*(?:,\s*)?(?:(?:and|plus|for|on|with|along with|and also had)\s+)?"
    r"(?:the\s+)?$",
    re.IGNORECASE,
)
_NON_GAME_SCOPE = re.compile(
    r"^\s+(?:on|for|in)\s+(?:the\s+)?(?:season|year|career)\b|"
    r"^\s+(?:career|season)(?:-|\s)",
    re.IGNORECASE,
)
_TITLE_TOKENS = frozenset({
    "qb", "rb", "wr", "te", "fb", "cb", "db", "lb", "k", "quarterback",
    "running", "back", "receiver", "kicker", "rookie", "veteran", "backup",
})
_PRONOUNS = frozenset({"he", "she", "they", "it"})


def _number(raw: str) -> int:
    folded = raw.casefold()
    return int(folded) if folded.isdigit() else _NUMBER_WORDS[folded]


def _metric(unit: str, premod: str | None, postmod: str | None) -> str:
    folded = unit.casefold()
    base = {
        "yard": "yards", "yards": "yards",
        "touchdown": "touchdowns", "touchdowns": "touchdowns",
        "interception": "interceptions", "interceptions": "interceptions",
        "reception": "receptions", "receptions": "receptions", "catches": "receptions",
        "carries": "carries", "sack": "sacks", "sacks": "sacks",
        "completion": "completions", "completions": "completions",
        "passes": "passes",
    }[folded]
    modifier = (premod or postmod or "").casefold()
    return f"{modifier}_{base}" if modifier else base


def _actor_tokens(actor: str) -> tuple[str, ...]:
    tokens = (
        token.casefold().strip(".'’-") for token in actor.split()
        if token.casefold().strip(".'’-") not in _TITLE_TOKENS
    )
    return tuple(token for token in tokens if token)


def _actor_matches(query: str, evidence: str) -> bool:
    left, right = _actor_tokens(query), _actor_tokens(evidence)
    return bool(left and right and (left == right or left[-1] == right[-1]))


@dataclass(frozen=True)
class TerminalStateFact:
    source_span: tuple[int, int]
    sentence_span: tuple[int, int]
    actor: str
    scope: str
    metric: str
    value: int
    authorized: bool
    text: str
    reason: str


@dataclass(frozen=True)
class TerminalStateResolution:
    state: str
    value: int | None
    facts: tuple[TerminalStateFact, ...]
    reason: str


@dataclass(frozen=True)
class TerminalStateIndex:
    source_sha256: str
    facts: tuple[TerminalStateFact, ...]

    def resolve(self, *, actor: str, scope: str, metric: str) -> TerminalStateResolution:
        facts = tuple(
            fact for fact in self.facts
            if fact.authorized and fact.scope == scope and fact.metric == metric
            and _actor_matches(actor, fact.actor)
        )
        if not facts:
            return TerminalStateResolution("unsupported", None, (), "no exact terminal-state fact")
        values = {fact.value for fact in facts}
        if len(values) != 1:
            return TerminalStateResolution("conflict", None, facts, "terminal-state facts disagree")
        return TerminalStateResolution("closed", next(iter(values)), facts,
                                       "all exact terminal-state facts agree")

    def verify(self, source: str) -> bool:
        if hashlib.sha256(source.encode()).hexdigest() != self.source_sha256:
            return False
        return all(
            0 <= fact.source_span[0] < fact.source_span[1] <= len(source)
            and source[fact.source_span[0]:fact.source_span[1]] == fact.text
            for fact in self.facts
        )


def build_terminal_state_index(source: str) -> TerminalStateIndex:
    if not source:
        raise ValueError("terminal-state index requires non-empty source text")
    facts: list[TerminalStateFact] = []
    for sentence_match in _SENTENCE.finditer(source):
        sentence = sentence_match.group()
        for frame in _FRAME.finditer(sentence):
            if frame.group("actor").casefold() in _PRONOUNS:
                continue
            body = frame.group("body")
            tuples = list(_TUPLE.finditer(body))
            if not tuples or _FRAME_PREFIX.fullmatch(body[:tuples[0].start()]) is None:
                continue
            previous_end = 0
            for position, item in enumerate(tuples):
                gap = body[previous_end:item.start()]
                if position and _TUPLE_SEPARATOR.fullmatch(gap) is None:
                    continue
                local_end = frame.start("body") + item.end()
                tail = sentence[local_end:local_end + 48]
                non_game_scope = _NON_GAME_SCOPE.match(tail) is not None
                start = sentence_match.start() + frame.start()
                end = sentence_match.start() + local_end
                facts.append(TerminalStateFact(
                    (start, end), (sentence_match.start(), sentence_match.end()),
                    frame.group("actor"), "non_game" if non_game_scope else "whole_game",
                    _metric(item.group("unit"), item.group("premod"), item.group("postmod")),
                    _number(item.group("count")), not non_game_scope, source[start:end],
                    "tuple has season/year/career scope" if non_game_scope
                    else "literal terminal game/day tuple",
                ))
                previous_end = item.end()
    return TerminalStateIndex(hashlib.sha256(source.encode()).hexdigest(), tuple(facts))
