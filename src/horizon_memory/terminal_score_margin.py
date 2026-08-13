# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Authenticated terminal score margin with explicit winner/loser roles."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re


_TEAM = r"(?:[A-Z][A-Za-z'’.-]*(?:\s+[A-Z][A-Za-z'’.-]*){0,3})"
_WIN = re.compile(
    rf"\b(?P<winner>{_TEAM})\s+(?:defeated|beat)\s+(?P<loser>(?:the\s+)?{_TEAM})"
    r"(?:\s+by a score of|\s+by|,)?\s+(?P<winner_score>\d{1,2})\s*[-–]\s*"
    r"(?P<loser_score>\d{1,2})\b"
)
_LOSS = re.compile(
    rf"\b(?P<loser>{_TEAM})\s+lost\s+(?P<winner_score>\d{{1,2}})\s*[-–]\s*"
    rf"(?P<loser_score>\d{{1,2}})\s+to\s+(?P<winner>(?:the\s+)?{_TEAM})\b"
)
_QUERY = re.compile(
    r"^(?:by )?how many points did (?P<team>[A-Za-z][A-Za-z .'-]*?) "
    r"(?P<role>win|lose|lost)(?: the game)? by(?: against [A-Za-z .'-]+)?\??$",
    re.I,
)
_STOP = frozenset({"the", "team", "football", "club"})
_SENTENCE = re.compile(r".+?(?:[.!?]+|$)")
_RETROSPECTIVE = re.compile(
    r"\b(?:previous|prior|last meeting|since|anniversary|in history|historical|years? earlier)\b",
    re.I,
)


def _tokens(team: str) -> frozenset[str]:
    return frozenset(token for token in re.findall(r"[a-z]+", team.casefold()) if token not in _STOP)


def _matches(query: str, authority: str) -> bool:
    left, right = _tokens(query), _tokens(authority)
    return bool(left and right and (left <= right or right <= left))


@dataclass(frozen=True)
class TerminalScoreAuthority:
    source_span: tuple[int, int]
    winner: str
    loser: str
    winner_score: int
    loser_score: int
    text: str


@dataclass(frozen=True)
class TerminalScoreMarginProof:
    question_sha256: str
    passage_sha256: str
    query_role: str
    authority: TerminalScoreAuthority
    result: int

    def verify(self, question: str, passage: str) -> bool:
        if hashlib.sha256(question.encode()).hexdigest() != self.question_sha256:
            return False
        if hashlib.sha256(passage.encode()).hexdigest() != self.passage_sha256:
            return False
        derived = _derive(question, passage)
        return derived is not None and derived == (self.query_role, self.authority, self.result)


def _authorities(passage: str) -> tuple[TerminalScoreAuthority, ...]:
    values = []
    for pattern in (_WIN, _LOSS):
        for match in pattern.finditer(passage):
            sentence_text = next(
                (sentence.group() for sentence in _SENTENCE.finditer(passage)
                 if sentence.start() <= match.start() < sentence.end()),
                "",
            )
            if _RETROSPECTIVE.search(sentence_text):
                continue
            winner_score, loser_score = int(match.group("winner_score")), int(match.group("loser_score"))
            if winner_score <= loser_score:
                continue
            values.append(TerminalScoreAuthority(
                match.span(), match.group("winner"), match.group("loser"), winner_score, loser_score,
                match.group(),
            ))
    return tuple(values)


def _derive(question: str, passage: str):
    query = _QUERY.fullmatch(question.strip())
    if not query:
        return None
    role = "winner" if query.group("role").casefold() == "win" else "loser"
    candidates = tuple(
        authority for authority in _authorities(passage)
        if _matches(query.group("team"), getattr(authority, role))
    )
    if len(candidates) != 1:
        return None
    authority = candidates[0]
    return role, authority, authority.winner_score - authority.loser_score


def compile_terminal_score_margin(question: str, passage: str) -> TerminalScoreMarginProof | None:
    if not question or not passage:
        return None
    derived = _derive(question, passage)
    if derived is None:
        return None
    return TerminalScoreMarginProof(
        hashlib.sha256(question.encode()).hexdigest(), hashlib.sha256(passage.encode()).hexdigest(),
        derived[0], derived[1], derived[2],
    )
