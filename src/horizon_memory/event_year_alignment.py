# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Fail-closed dual event-to-year alignment for elapsed-year questions."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re


_CLAUSE = re.compile(r"[^.;!?]+(?:[.;!?]+|$)")
_YEAR = re.compile(r"(?<![\d-])(?P<year>1[0-9]{3}|20[0-9]{2})(?![\d-])")
_TOKEN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'’-]*")
_BETWEEN = re.compile(
    r"^how many years (?:have )?passed between (?P<left>.+?) and (?P<right>.+?)\??$",
    re.IGNORECASE,
)
_AFTER = re.compile(
    r"^how many years (?:after|before) (?P<left>.+?) "
    r"(?:did|was|were|until) (?P<right>.+?)\??$",
    re.IGNORECASE,
)
_STOP = frozenset({
    "a", "an", "and", "as", "at", "be", "been", "being", "before", "by", "did",
    "do", "does", "for", "from", "had", "has", "have", "he", "her", "his", "how",
    "in", "into", "it", "its", "many", "of", "on", "or", "she", "that", "the",
    "their", "them", "then", "there", "they", "this", "to", "until", "was", "were",
    "when", "which", "who", "with", "year", "years", "after", "between", "passed",
})


def _normalize(token: str) -> str:
    folded = token.casefold().strip("'’-")
    for suffix in ("ing", "edly", "ed", "es", "s"):
        if len(folded) >= len(suffix) + 5 and folded.endswith(suffix):
            return folded[:-len(suffix)]
    return folded


def _tokens(text: str, offset: int = 0) -> tuple[tuple[str, tuple[int, int], str], ...]:
    values = []
    for match in _TOKEN.finditer(text):
        normalized = _normalize(match.group())
        if normalized and normalized not in _STOP:
            values.append((normalized, (offset + match.start(), offset + match.end()), match.group()))
    return tuple(values)


@dataclass(frozen=True)
class EventYearAlignment:
    event_span: tuple[int, int]
    clause_span: tuple[int, int]
    year_span: tuple[int, int]
    year: int
    anchor_tokens: tuple[str, ...]
    matched_anchor_count: int
    event_anchor_count: int
    runner_up_count: int


@dataclass(frozen=True)
class EventYearIntervalProof:
    question_sha256: str
    passage_sha256: str
    alignments: tuple[EventYearAlignment, EventYearAlignment]
    result: int

    def verify(self, question: str, passage: str) -> bool:
        if hashlib.sha256(question.encode()).hexdigest() != self.question_sha256:
            return False
        if hashlib.sha256(passage.encode()).hexdigest() != self.passage_sha256:
            return False
        derived = _derive(question, passage)
        return derived is not None and derived == (self.alignments, self.result)


def _events(question: str) -> tuple[tuple[str, tuple[int, int]], tuple[str, tuple[int, int]]] | None:
    for pattern in (_BETWEEN, _AFTER):
        match = pattern.fullmatch(question.strip())
        if match:
            base = question.find(match.group(0))
            return (
                (match.group("left"), (base + match.start("left"), base + match.end("left"))),
                (match.group("right").rstrip("?"),
                 (base + match.start("right"), base + match.end("right") - int(match.group("right").endswith("?")))),
            )
    return None


def _candidate_clauses(passage: str) -> tuple[tuple[tuple[int, int], int, tuple[int, int], set[str]], ...]:
    candidates = []
    for clause in _CLAUSE.finditer(passage):
        years = list(_YEAR.finditer(clause.group()))
        if len(years) != 1:
            continue
        year = years[0]
        candidates.append((
            clause.span(), int(year.group("year")),
            (clause.start() + year.start("year"), clause.start() + year.end("year")),
            {token for token, _, _ in _tokens(clause.group())},
        ))
    return tuple(candidates)


def _align(
    event: tuple[str, tuple[int, int]],
    clauses: tuple[tuple[tuple[int, int], int, tuple[int, int], set[str]], ...],
) -> EventYearAlignment | None:
    event_tokens = _tokens(event[0], event[1][0])
    anchor_set = {token for token, _, _ in event_tokens}
    if len(anchor_set) < 3:
        return None
    scored = sorted(
        ((len(anchor_set & clause_tokens), span, year, year_span, clause_tokens)
         for span, year, year_span, clause_tokens in clauses),
        key=lambda item: (-item[0], item[1][0]),
    )
    if not scored:
        return None
    best = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0
    matched = tuple(sorted(anchor_set & best[4]))
    if best[0] < 3 or best[0] / len(anchor_set) < 0.5 or best[0] - runner_up < 2:
        return None
    return EventYearAlignment(
        event[1], best[1], best[3], best[2], matched, best[0], len(anchor_set), runner_up,
    )


def _derive(
    question: str, passage: str,
) -> tuple[tuple[EventYearAlignment, EventYearAlignment], int] | None:
    events = _events(question)
    if events is None:
        return None
    clauses = _candidate_clauses(passage)
    left, right = _align(events[0], clauses), _align(events[1], clauses)
    if left is None or right is None or left.clause_span == right.clause_span:
        return None
    alignments = (left, right)
    return alignments, abs(right.year - left.year)


def compile_event_year_interval(question: str, passage: str) -> EventYearIntervalProof | None:
    if not question or not passage or _YEAR.search(question):
        return None
    derived = _derive(question, passage)
    if derived is None:
        return None
    return EventYearIntervalProof(
        hashlib.sha256(question.encode()).hexdigest(),
        hashlib.sha256(passage.encode()).hexdigest(), derived[0], derived[1],
    )


def compile_closed_event_year_interval(question: str, passage: str) -> EventYearIntervalProof | None:
    """Compile only when the supplied source has a closed two-year universe."""
    proof = compile_event_year_interval(question, passage)
    if proof is None:
        return None
    source_years = {int(match.group("year")) for match in _YEAR.finditer(passage)}
    aligned_years = {alignment.year for alignment in proof.alignments}
    if len(source_years) != 2 or source_years != aligned_years:
        return None
    return proof
