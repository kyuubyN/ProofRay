# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fail-closed discourse ledger for field-goal event ellipsis in narrative text.

This adapter does not answer questions. It turns literal mentions into explicit event
bundles, non-events, references, or unresolved debt. Aggregate execution is authorized
only when the ledger closes without debt.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re


_SENTENCE = re.compile(r"[^.!?]+(?:[.!?]+|$)")
_MENTION = re.compile(r"\bfield goals?\b", re.IGNORECASE)
_DIRECT = re.compile(
    r"(?P<sequence>\b\d{1,3}[- ]yard(?:\s*(?:(?:,\s*)?(?:and\s+)?"
    r"(?:a\s+)?\d{1,3}[- ]yard))*)\s+field goals?",
    re.IGNORECASE,
)
_AFTER_VALUES = re.compile(
    r"\bfield goals?\s+(?:of|from)\s+(?P<sequence>\d{1,3}"
    r"(?:\s*(?:,|and)\s*\d{1,3})*)\s*(?:yards?|yards?\s+out)\b",
    re.IGNORECASE,
)
_PAREN_VALUES = re.compile(
    r"\bfield goals?\s*\([^)]*?(?P<sequence>\d{1,3}-yarder"
    r"(?:\s*(?:,|and)\s*(?:a\s+)?\d{1,3}-yarder)*)",
    re.IGNORECASE,
)
_CARDINAL_BEFORE = re.compile(
    r"\b(?P<count>one|two|three|four|five|six|seven|eight|nine|\d{1,2})"
    r"(?:\s+[a-z-]+){0,3}\s+field goals?\b",
    re.IGNORECASE,
)
_QUARTER = re.compile(r"\b(first|second|third|fourth|1st|2nd|3rd|4th) quarter\b", re.IGNORECASE)
_NON_EVENT = re.compile(
    r"\b(?:miss(?:ed|es|ing)?|blocked|fake|attempt(?:ed|s)?|range|formation|"
    r"need(?:ed|ing)?|chance|try|tried)\b",
    re.IGNORECASE,
)
_EXPLICIT_FAILURE = re.compile(
    r"\b\d{1,3}[- ]yard\s+field goal\s+(?:attempt\b[^.!?]{0,32})?"
    r"(?:was\s+)?(?:blocked|missed)\b",
    re.IGNORECASE,
)
_UNBOUND_ELLIPSIS = re.compile(
    r"\b(?:another|again)\b[^.!?]{0,48}\b(?:from\s+)?\d{1,3}[- ]yards?\b",
    re.IGNORECASE,
)
_REFERENCE = re.compile(
    r"\b(?:the|this|that|latter|former|his|her|its)\s+(?:first|second|third|fourth|"
    r"fifth|sixth|seventh|eighth|ninth|\d+(?:st|nd|rd|th))?\s*field goal\b|"
    r"\bfield goal\b[^.!?]{0,64}\b(?:record|career|history|streak|consecutive)\b",
    re.IGNORECASE,
)
_SUCCESS = re.compile(
    r"\b(?:scored|made|kicked|booted|nailed|hit|converted|drained|settled for|"
    r"answered with|responded with|tacked on|added|successful on|good)\b",
    re.IGNORECASE,
)
_SINGULAR = re.compile(r"\b(?:a|an|one|another)\s+(?:[A-Za-z'-]+\s+){0,3}field goal\b",
                       re.IGNORECASE)
_ORDINALS = {
    "first": 1, "1st": 1, "second": 2, "2nd": 2,
    "third": 3, "3rd": 3, "fourth": 4, "4th": 4,
}
_CARDINALS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9,
}


@dataclass(frozen=True)
class EllipsisMention:
    source_span: tuple[int, int]
    sentence_span: tuple[int, int]
    state: str
    event_count: int
    yards: tuple[int, ...]
    quarter: int | None
    text: str
    reason: str


@dataclass(frozen=True)
class CausalEllipsisLedger:
    source_sha256: str
    mentions: tuple[EllipsisMention, ...]
    state: str
    event_count: int
    unresolved_spans: tuple[tuple[int, int], ...]

    def count(self, quarters: frozenset[int] | None = None) -> int | None:
        if self.state != "closed":
            return None
        if quarters is not None and any(
                mention.state == "event" and mention.quarter is None
                for mention in self.mentions):
            return None
        return sum(
            mention.event_count for mention in self.mentions
            if mention.state == "event" and
            (quarters is None or mention.quarter in quarters)
        )

    def verify(self, source: str) -> bool:
        if hashlib.sha256(source.encode()).hexdigest() != self.source_sha256:
            return False
        return all(
            0 <= mention.source_span[0] < mention.source_span[1] <= len(source)
            and source[mention.source_span[0]:mention.source_span[1]] == mention.text
            for mention in self.mentions
        )


def _numbers(text: str) -> tuple[int, ...]:
    return tuple(int(value) for value in re.findall(r"\d{1,3}", text))


def _overlapping(pattern: re.Pattern[str], sentence: str, mention: re.Match[str]) -> re.Match[str] | None:
    return next((match for match in pattern.finditer(sentence)
                 if match.start() <= mention.start() < match.end()), None)


def _classify(sentence: str, mention: re.Match[str], quarter: int | None,
              sentence_start: int, sentence_end: int) -> EllipsisMention:
    absolute_span = (sentence_start + mention.start(), sentence_start + mention.end())
    common = dict(source_span=absolute_span, sentence_span=(sentence_start, sentence_end),
                  quarter=quarter, text=mention.group())

    if _EXPLICIT_FAILURE.search(sentence):
        return EllipsisMention(state="non_event", event_count=0, yards=(),
                               reason="explicitly blocked or missed attempt", **common)

    direct = _overlapping(_DIRECT, sentence, mention)
    if direct is not None:
        values = _numbers(direct.group("sequence"))
        if _NON_EVENT.search(sentence):
            return EllipsisMention(state="debt", event_count=0, yards=(),
                                   reason="direct value occurs in mixed non-event sentence", **common)
        return EllipsisMention(state="event", event_count=len(values), yards=values,
                               reason="direct yard-valued field-goal event", **common)

    after = _overlapping(_AFTER_VALUES, sentence, mention)
    if after is not None and not _NON_EVENT.search(sentence):
        values = _numbers(after.group("sequence"))
        return EllipsisMention(state="event", event_count=len(values), yards=values,
                               reason="postnominal coordinated yard values", **common)

    parenthetical = _overlapping(_PAREN_VALUES, sentence, mention)
    if parenthetical is not None and not _NON_EVENT.search(sentence):
        values = _numbers(parenthetical.group("sequence"))
        return EllipsisMention(state="event", event_count=len(values), yards=values,
                               reason="parenthetical yarder enumeration", **common)

    cardinal = _overlapping(_CARDINAL_BEFORE, sentence, mention)
    if cardinal is not None and not _NON_EVENT.search(sentence):
        raw = cardinal.group("count").casefold()
        count = _CARDINALS.get(raw, int(raw) if raw.isdigit() else 0)
        if count > 0 and _SUCCESS.search(sentence):
            return EllipsisMention(state="event", event_count=count, yards=(),
                                   reason="explicit cardinal successful bundle", **common)

    if _REFERENCE.search(sentence):
        return EllipsisMention(state="reference", event_count=0, yards=(),
                               reason="anaphoric or record reference, not a new event", **common)
    if _NON_EVENT.search(sentence):
        return EllipsisMention(state="non_event", event_count=0, yards=(),
                               reason="attempt, miss, fake, range or counterfactual", **common)
    if _SINGULAR.search(sentence) and _SUCCESS.search(sentence):
        return EllipsisMention(state="event", event_count=1, yards=(),
                               reason="explicit singular successful event", **common)
    return EllipsisMention(state="debt", event_count=0, yards=(),
                           reason="field-goal mention lacks a closed event interpretation", **common)


def build_field_goal_ledger(source: str) -> CausalEllipsisLedger:
    if not source:
        raise ValueError("causal ellipsis ledger requires non-empty source text")
    mentions = []
    quarter = None
    for sentence_match in _SENTENCE.finditer(source):
        sentence = sentence_match.group()
        observed_quarter = _QUARTER.search(sentence)
        if observed_quarter:
            quarter = _ORDINALS[observed_quarter.group(1).casefold()]
        for mention in _MENTION.finditer(sentence):
            mentions.append(_classify(
                sentence, mention, quarter, sentence_match.start(), sentence_match.end()))
        for ellipsis in _UNBOUND_ELLIPSIS.finditer(sentence):
            if _MENTION.search(ellipsis.group()) is None:
                start, end = (sentence_match.start() + ellipsis.start(),
                              sentence_match.start() + ellipsis.end())
                mentions.append(EllipsisMention(
                    (start, end), (sentence_match.start(), sentence_match.end()), "debt", 0,
                    (), quarter, source[start:end], "yard-valued ellipsis lacks an event anchor"))
    debts = tuple(item.source_span for item in mentions if item.state == "debt")
    state = "closed" if mentions and not debts else "incomplete"
    return CausalEllipsisLedger(
        hashlib.sha256(source.encode()).hexdigest(), tuple(mentions), state,
        sum(item.event_count for item in mentions if item.state == "event"), debts)
