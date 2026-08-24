# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reopenable sum over one explicitly marked terminal game score."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re


_QUERY = re.compile(
    r"^how many (?:"
    r"(?:total )?points (?:were|was) scored(?: (?:in|during) (?:the |this |entire )?game)?|"
    r"points in total (?:were|was) scored(?: in (?:the )?game)?|"
    r"points total (?:were|was) scored(?: in (?:the )?game)?|"
    r"points (?:were|was) scored total(?: in (?:the )?game)?|"
    r"total points (?:were|was) scored by (?:the )?end of (?:the )?game|"
    r"total points (?:were|was) scored (?:in (?:the )?game )?by both teams|"
    r"total points (?:were|was) scored between (?:both|the two) teams|"
    r"total points did both teams (?:score(?: in (?:the )?game)?|combine for)|"
    r"points did both teams score in total|"
    r"points total did both teams score|"
    r"total points were in (?:the )?game"
    r")\??$", re.I)
_FINAL = re.compile(
    r"\b(?:final score(?: was| of)?|eventually be the final score(?: of)?)\s*"
    r"(?P<left>\d{1,2})\s*[-–]\s*(?P<right>\d{1,2})\b", re.I)
_MARGIN_QUERY = re.compile(
    r"^how many points did (?P<subject>.+?) (?:win|lose) by\??$|"
    r"^how many points did (?P<winner>.+?) beat (?P<loser>.+?) by\??$|"
    r"^how many points difference was there between the winning and losing team\??$",
    re.I,
)
_SUBJECT_STOP = frozenset({
    "a", "an", "did", "game", "how", "many", "points", "the", "they", "team",
})


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class FinalScoreSumProof:
    question_sha256: str
    passage_sha256: str
    score_span: tuple[int, int]
    left_span: tuple[int, int]
    right_span: tuple[int, int]
    left: int
    right: int
    result: int

    def verify(self, question: str, passage: str) -> bool:
        if _sha(question) != self.question_sha256 or _sha(passage) != self.passage_sha256:
            return False
        return _compile_final_score_sum(question, passage) == self


@dataclass(frozen=True)
class FinalScoreMarginProof:
    question_sha256: str
    passage_sha256: str
    score_span: tuple[int, int]
    left_span: tuple[int, int]
    right_span: tuple[int, int]
    left: int
    right: int
    result: int

    def verify(self, question: str, passage: str) -> bool:
        if _sha(question) != self.question_sha256 or _sha(passage) != self.passage_sha256:
            return False
        return _compile_final_score_margin(question, passage) == self


def _unique_terminal_score(passage: str):
    matches = tuple(_FINAL.finditer(passage))
    if len(matches) != 1:
        return None
    match = matches[0]
    left, right = int(match.group("left")), int(match.group("right"))
    if left > 80 or right > 80:
        return None
    return match, left, right


def _compile_final_score_sum(question: str, passage: str) -> FinalScoreSumProof | None:
    if _QUERY.fullmatch(question.strip()) is None or not passage:
        return None
    terminal = _unique_terminal_score(passage)
    if terminal is None:
        return None
    match, left, right = terminal
    return FinalScoreSumProof(
        _sha(question), _sha(passage), match.span(), match.span("left"),
        match.span("right"), left, right, left + right)


def compile_final_score_sum(question: str, passage: str) -> FinalScoreSumProof | None:
    """Compile one closed terminal-score sum, or abstain."""
    return _compile_final_score_sum(question, passage)


def _compile_final_score_margin(question: str, passage: str) -> FinalScoreMarginProof | None:
    match_query = _MARGIN_QUERY.fullmatch(question.strip())
    if match_query is None or not passage:
        return None
    subject_text = " ".join(
        value for value in (
            match_query.groupdict().get("subject"), match_query.groupdict().get("winner"),
            match_query.groupdict().get("loser")) if value)
    if re.search(r"\b(?:quarter|half|first game|second game|at halftime)\b", subject_text, re.I):
        return None
    terminal = _unique_terminal_score(passage)
    if terminal is None:
        return None
    match, left, right = terminal
    subject_anchors = {
        token for token in re.findall(r"[a-z0-9]+", subject_text.casefold())
        if token not in _SUBJECT_STOP and len(token) > 2}
    sentence_start = max(passage.rfind(mark, 0, match.start()) for mark in ".!?") + 1
    stops = [position for mark in ".!?" if (position := passage.find(mark, match.end())) >= 0]
    sentence_end = min(stops) + 1 if stops else len(passage)
    frame = passage[sentence_start:sentence_end].casefold()
    if subject_anchors and not any(
            re.search(rf"\b{re.escape(anchor)}\b", frame) for anchor in subject_anchors):
        return None
    return FinalScoreMarginProof(
        _sha(question), _sha(passage), match.span(), match.span("left"),
        match.span("right"), left, right, abs(left - right))


def compile_final_score_margin(question: str, passage: str) -> FinalScoreMarginProof | None:
    """Compile one terminal-score victory margin, or abstain."""
    return _compile_final_score_margin(question, passage)


__all__ = [
    "FinalScoreMarginProof", "FinalScoreSumProof", "compile_final_score_margin",
    "compile_final_score_sum",
]
