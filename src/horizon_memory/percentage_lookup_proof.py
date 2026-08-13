# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Authenticated direct percentage lookup using D13 fact boundaries."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

from .percentage_complement_proof import PercentageFact, _anchors, _facts


_DIRECT = re.compile(
    r"^how many percent(?:age)? (?:were|was|are|is) (?P<predicate>.+?)\??$|"
    r"^how many percent(?:age)? of (?P<subject>.+?) (?:were|was|are|is|had) "
    r"(?P<object>.+?)\??$",
    re.IGNORECASE,
)
_BANNED = re.compile(
    r"\b(?:not|either|combined|compared|than|more|less|higher|lower|difference|"
    r"increase|decrease|between|from .+ to|approximately|about|total)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PercentageLookupProof:
    question_sha256: str
    passage_sha256: str
    query_anchors: tuple[str, ...]
    fact: PercentageFact
    result: str

    def verify(self, question: str, passage: str) -> bool:
        if hashlib.sha256(question.encode()).hexdigest() != self.question_sha256:
            return False
        if hashlib.sha256(passage.encode()).hexdigest() != self.passage_sha256:
            return False
        derived = _derive(question, passage)
        return derived is not None and derived == (self.query_anchors, self.fact, self.result)


def _derive(question: str, passage: str):
    if _BANNED.search(question):
        return None
    match = _DIRECT.fullmatch(question.strip())
    if not match:
        return None
    phrase = match.group("predicate") or f"{match.group('subject')} {match.group('object')}"
    anchors = _anchors(phrase)
    if not anchors:
        return None
    winners = [fact for fact in _facts(passage) if anchors <= set(fact.context_anchors)]
    if len(winners) != 1:
        return None
    return tuple(sorted(anchors)), winners[0], winners[0].value


def compile_percentage_lookup(question: str, passage: str) -> PercentageLookupProof | None:
    if not question or not passage:
        return None
    derived = _derive(question, passage)
    if derived is None:
        return None
    return PercentageLookupProof(
        hashlib.sha256(question.encode()).hexdigest(), hashlib.sha256(passage.encode()).hexdigest(),
        derived[0], derived[1], derived[2],
    )
