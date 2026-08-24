# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fail-closed event alignment and exact Gregorian elapsed-day proofs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import re


_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}
_DATE = re.compile(
    r"(?<!\d)(?P<day>[0-3]?\d)\s+"
    r"(?P<month>January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(?P<year>1[0-9]{3}|20[0-9]{2})(?!\d)", re.I)
_QUESTION = re.compile(
    r"^how many days after (?P<left>.+?) did (?P<right>.+?)\??$", re.I)
_TOKEN = re.compile(r"[A-Za-z][A-Za-z'’-]*")
_STOP = frozenset({
    "a", "after", "an", "and", "at", "by", "did", "do", "does", "for",
    "from", "he", "her", "his", "how", "in", "into", "many", "of", "on",
    "she", "the", "their", "they", "to", "was", "were", "with",
})


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _normalize(token: str) -> str:
    value = token.casefold().strip("'’-")
    for suffix in ("ingly", "edly", "ing", "ed", "es", "s"):
        if len(value) >= len(suffix) + 4 and value.endswith(suffix):
            return value[:-len(suffix)]
    return value


def _tokens(text: str) -> frozenset[str]:
    return frozenset(value for match in _TOKEN.finditer(text)
                     if (value := _normalize(match.group())) not in _STOP)


@dataclass(frozen=True)
class EventDateAlignment:
    event_span: tuple[int, int]
    date_span: tuple[int, int]
    context_span: tuple[int, int]
    iso_date: str
    matched_tokens: tuple[str, ...]
    event_token_count: int
    runner_up_count: int


@dataclass(frozen=True)
class EventDateIntervalProof:
    question_sha256: str
    passage_sha256: str
    alignments: tuple[EventDateAlignment, EventDateAlignment]
    result: int

    def verify(self, question: str, passage: str) -> bool:
        if _sha(question) != self.question_sha256 or _sha(passage) != self.passage_sha256:
            return False
        return _compile_event_date_interval(question, passage) == self


def _events(question: str) -> tuple[tuple[str, tuple[int, int]], ...] | None:
    match = _QUESTION.fullmatch(question.strip())
    if match is None:
        return None
    base = question.find(match.group(0))
    return tuple((match.group(name).rstrip("?"),
                  (base + match.start(name),
                   base + match.end(name) - int(match.group(name).endswith("?"))))
                 for name in ("left", "right"))


def _dated_contexts(passage: str):
    matches = list(_DATE.finditer(passage))
    if len(matches) != 2:
        return ()
    separators = []
    for previous, current in zip(matches, matches[1:]):
        between = passage[previous.end():current.start()]
        sentence_boundary = re.search(r"[.!?]", between)
        if sentence_boundary:
            separators.append(None)
            continue
        boundary = re.search(r"[,;:]", between)
        separators.append(previous.end() + boundary.end() if boundary
                          else (previous.end() + current.start()) // 2)
    rows = []
    for index, match in enumerate(matches):
        try:
            parsed = date(int(match.group("year")), _MONTHS[match.group("month").casefold()],
                          int(match.group("day")))
        except ValueError:
            return ()
        left_sentence = max(passage.rfind(mark, 0, match.start()) for mark in ".!?") + 1
        next_stops = [position for mark in ".!?" if (position := passage.find(mark, match.end())) >= 0]
        right_sentence = min(next_stops) + 1 if next_stops else len(passage)
        if index and separators[index - 1] is not None:
            left_sentence = max(left_sentence, separators[index - 1])
        if index + 1 < len(matches) and separators[index] is not None:
            right_sentence = min(right_sentence, separators[index])
        span = (left_sentence, right_sentence)
        rows.append((parsed, match.span(), span, _tokens(passage[span[0]:span[1]])))
    return tuple(rows)


def _align(event: tuple[str, tuple[int, int]], contexts) -> EventDateAlignment | None:
    anchors = _tokens(event[0])
    if not anchors:
        return None
    scored = sorted(
        ((len(anchors & tokens), parsed, date_span, context_span, tokens)
         for parsed, date_span, context_span, tokens in contexts),
        key=lambda item: (-item[0], item[2]),
    )
    best, runner_up = scored[0], scored[1][0]
    matched = tuple(sorted(anchors & best[4]))
    if best[0] == 0 or best[0] / len(anchors) < 0.5 or best[0] <= runner_up:
        return None
    return EventDateAlignment(
        event[1], best[2], best[3], best[1].isoformat(), matched, len(anchors), runner_up)


def _compile_event_date_interval(question: str, passage: str) -> EventDateIntervalProof | None:
    events = _events(question)
    contexts = _dated_contexts(passage)
    if events is None or not contexts:
        return None
    left, right = _align(events[0], contexts), _align(events[1], contexts)
    if left is None or right is None or left.date_span == right.date_span:
        return None
    elapsed = (date.fromisoformat(right.iso_date) - date.fromisoformat(left.iso_date)).days
    if elapsed <= 0:
        return None
    return EventDateIntervalProof(_sha(question), _sha(passage), (left, right), elapsed)


def compile_event_date_interval(question: str, passage: str) -> EventDateIntervalProof | None:
    """Compile an exact two-event day interval or abstain."""
    return _compile_event_date_interval(question, passage)


__all__ = ["EventDateAlignment", "EventDateIntervalProof", "compile_event_date_interval"]
