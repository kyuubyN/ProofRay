# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D13 percentage complement with an explicit population/set carrier."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import re

from .percentage_complement_proof import PercentageFact, _anchors, _facts, _render


_QUERY = re.compile(
    r"^how many(?: in)? percent of (?P<carrier>.+?) "
    r"(?:were|was|are|is) not (?P<predicate>.+?)\??$|"
    r"^how many(?: in)? percent of (?P<carrier_contract>.+?) "
    r"(?:weren't|wasn't|aren't|isn't|didn't) (?P<predicate_contract>.+?)\??$",
    re.IGNORECASE,
)
_BANNED_CARRIER = re.compile(
    r"\b(?:either|or|combined|compared|than|more|less|between|difference|"
    r"increase|decrease|at least|at most)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CarrierPercentageComplementProof:
    question_sha256: str
    passage_sha256: str
    carrier_span: tuple[int, int]
    predicate_span: tuple[int, int]
    predicate_anchors: tuple[str, ...]
    fact: PercentageFact
    result: str

    def verify(self, question: str, passage: str) -> bool:
        if hashlib.sha256(question.encode()).hexdigest() != self.question_sha256:
            return False
        if hashlib.sha256(passage.encode()).hexdigest() != self.passage_sha256:
            return False
        derived = _derive(question, passage)
        return derived is not None and derived == (
            self.carrier_span, self.predicate_span, self.predicate_anchors,
            self.fact, self.result,
        )


def _derive(question: str, passage: str):
    match = _QUERY.fullmatch(question.strip())
    if not match:
        return None
    carrier_group = "carrier" if match.group("carrier") is not None else "carrier_contract"
    predicate_group = "predicate" if match.group("predicate") is not None else "predicate_contract"
    carrier, predicate = match.group(carrier_group), match.group(predicate_group)
    if _BANNED_CARRIER.search(carrier) or re.search(r"\bor\b", predicate, re.I):
        return None
    anchors = _anchors(predicate)
    if not anchors:
        return None
    winners = [fact for fact in _facts(passage) if anchors <= set(fact.context_anchors)]
    if len(winners) != 1:
        return None
    fact = winners[0]
    result = _render(Decimal(100) - Decimal(fact.value))
    return (
        match.span(carrier_group), match.span(predicate_group), tuple(sorted(anchors)), fact, result,
    )


def compile_carrier_percentage_complement(
    question: str, passage: str,
) -> CarrierPercentageComplementProof | None:
    if not question or not passage:
        return None
    derived = _derive(question, passage)
    if derived is None:
        return None
    return CarrierPercentageComplementProof(
        hashlib.sha256(question.encode()).hexdigest(), hashlib.sha256(passage.encode()).hexdigest(),
        derived[0], derived[1], derived[2], derived[3], derived[4],
    )
