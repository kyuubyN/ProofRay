# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Margin proof from a closed score universe and explicit team outcome role."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

from .terminal_score_margin import _QUERY, _RETROSPECTIVE, _tokens


_SENTENCE = re.compile(r".+?(?:[.!?]+|$)")
_SCORE = re.compile(r"(?<![\d-])(?P<left>\d{1,2})\s*[-–]\s*(?P<right>\d{1,2})(?![\d-])")
_NON_SCORE = re.compile(
    r"\b(?:record|season|series|rank|ranking|seed|week|anniversary)\b|"
    r"\b(?:improve(?:d)?|fell|dropped|moved|went)\s+to\s*$",
    re.I,
)
_PARTIAL = re.compile(r"\b(?:halftime|half-time|quarter|period|at the half|lead)\b", re.I)
_SCORE_CUE = re.compile(
    r"\b(?:win|won|victory|lost|loss|defeated|beat|final|score|shootout|preserved)\b",
    re.I,
)
_LOCAL_NON_SCORE = re.compile(r"\b(?:record|week|series|ranking|seed)\b", re.I)


@dataclass(frozen=True)
class ScoreOccurrence:
    source_span: tuple[int, int]
    sentence_span: tuple[int, int]
    left: int
    right: int
    text: str


@dataclass(frozen=True)
class OutcomeEvidence:
    role: str
    sentence_span: tuple[int, int]
    team_tokens: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class ClosedScoreMarginProof:
    question_sha256: str
    passage_sha256: str
    query_role: str
    scores: tuple[ScoreOccurrence, ...]
    outcome: OutcomeEvidence
    result: int

    def verify(self, question: str, passage: str) -> bool:
        if hashlib.sha256(question.encode()).hexdigest() != self.question_sha256:
            return False
        if hashlib.sha256(passage.encode()).hexdigest() != self.passage_sha256:
            return False
        derived = _derive(question, passage)
        return derived is not None and derived == (
            self.query_role, self.scores, self.outcome, self.result,
        )


def _score_occurrences(passage: str) -> tuple[ScoreOccurrence, ...]:
    values = []
    for sentence in _SENTENCE.finditer(passage):
        for match in _SCORE.finditer(sentence.group()):
            prefix = sentence.group()[max(0, match.start() - 48):match.start()]
            local = sentence.group()[max(0, match.start() - 100):min(len(sentence.group()), match.end() + 100)]
            immediate = sentence.group()[max(0, match.start() - 24):min(len(sentence.group()), match.end() + 24)]
            if (_RETROSPECTIVE.search(sentence.group()) or _NON_SCORE.search(prefix)
                    or _LOCAL_NON_SCORE.search(immediate) or _SCORE_CUE.search(local) is None):
                continue
            left, right = int(match.group("left")), int(match.group("right"))
            if left == right or left > 80 or right > 80:
                continue
            start, end = sentence.start() + match.start(), sentence.start() + match.end()
            values.append(ScoreOccurrence((start, end), sentence.span(), left, right, passage[start:end]))
    return tuple(values)


def _plausible_pair_values(passage: str) -> frozenset[tuple[int, int]]:
    """Return every score-shaped pair that cannot be typed as a non-score locally.

    Closure is deliberately stricter than recognition: an unclassified pair is
    ambiguity, not evidence that may be silently discarded.
    """
    values = set()
    for sentence in _SENTENCE.finditer(passage):
        for match in _SCORE.finditer(sentence.group()):
            prefix = sentence.group()[max(0, match.start() - 48):match.start()]
            immediate = sentence.group()[max(0, match.start() - 24):min(len(sentence.group()), match.end() + 24)]
            if _RETROSPECTIVE.search(sentence.group()) or _NON_SCORE.search(prefix) or _LOCAL_NON_SCORE.search(immediate):
                continue
            left, right = int(match.group("left")), int(match.group("right"))
            if left > 80 or right > 80:
                continue
            values.add(tuple(sorted((left, right), reverse=True)))
    return frozenset(values)


def _role_evidence(team: str, passage: str) -> tuple[OutcomeEvidence, ...]:
    team_tokens = tuple(sorted(_tokens(team)))
    if not team_tokens:
        return ()
    last = re.escape(team_tokens[-1])
    winner = re.compile(
        rf"(?:\bwith the win\b[^.!?]{{0,100}}\b{last}\b|"
        rf"\b{last}\b[^.!?]{{0,80}}\b(?:won|win|victory|preserved)\b|"
        rf"\bvictory\b[^.!?]{{0,60}}\b(?:for|by)\s+(?:the\s+)?{last}\b)", re.I,
    )
    loser = re.compile(
        rf"(?:\bwith the loss\b[^.!?]{{0,100}}\b{last}\b|"
        rf"\b{last}\b[^.!?]{{0,80}}\b(?:lost|loss|fell)\b)", re.I,
    )
    values = []
    for sentence in _SENTENCE.finditer(passage):
        folded_tokens = _tokens(sentence.group())
        if not set(team_tokens) <= folded_tokens or _RETROSPECTIVE.search(sentence.group()):
            continue
        roles = []
        if winner.search(sentence.group()): roles.append("winner")
        if loser.search(sentence.group()): roles.append("loser")
        for role in roles:
            values.append(OutcomeEvidence(role, sentence.span(), team_tokens, sentence.group()))
    return tuple(values)


def _derive(question: str, passage: str):
    query = _QUERY.fullmatch(question.strip())
    if not query:
        return None
    role = "winner" if query.group("role").casefold() == "win" else "loser"
    scores = _score_occurrences(passage)
    distinct = {tuple(sorted((score.left, score.right), reverse=True)) for score in scores}
    if len(distinct) != 1 or not scores or _plausible_pair_values(passage) != frozenset(distinct):
        return None
    score_sentences = {score.sentence_span for score in scores}
    if any(_PARTIAL.search(passage[start:end]) for start, end in score_sentences):
        return None
    evidence = _role_evidence(query.group("team"), passage)
    matching = tuple(item for item in evidence if item.role == role)
    opposite = tuple(item for item in evidence if item.role != role)
    if not matching or opposite:
        return None
    high, low = next(iter(distinct))
    return role, scores, matching[0], high - low


def compile_closed_score_margin(question: str, passage: str) -> ClosedScoreMarginProof | None:
    if not question or not passage:
        return None
    derived = _derive(question, passage)
    if derived is None:
        return None
    return ClosedScoreMarginProof(
        hashlib.sha256(question.encode()).hexdigest(), hashlib.sha256(passage.encode()).hexdigest(),
        derived[0], derived[1], derived[2], derived[3],
    )
