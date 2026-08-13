# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Predicate-bound exact percentage complement with source provenance."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import re


_QUERY = re.compile(
    r"^how many(?: in)? percent (?:weren't|aren't|wasn't|isn't)\s+(?P<predicate>.+?)\??$|"
    r"^how many(?: in)? percent (?:were|are|was|is) not\s+(?P<predicate_not>.+?)\??$",
    re.IGNORECASE,
)
_PERCENT = re.compile(r"(?<![\d.])(?P<value>\d{1,3}(?:\.\d+)?)\s*%")
_ITEM = re.compile(r".+?(?:,|;|\band\s+(?=\d{1,3}(?:\.\d+)?\s*%)|(?<!\d)[.!?](?!\d)|$)", re.I)
_TOKEN = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+|\d+")
_APPROX = re.compile(r"\b(?:about|around|approximately|roughly|nearly|over|under)\s*$", re.I)
_STOP = frozenset({
    "a", "an", "any", "are", "from", "in", "of", "or", "people", "person",
    "the", "their", "was", "were", "who", "years", "year", "age", "population",
    "race", "ancestry", "group", "groups", "made", "up",
})


def _normalize(token: str) -> str:
    folded = token.casefold()
    if len(folded) > 4 and folded.endswith("ies"):
        return folded[:-3] + "y"
    if len(folded) > 4 and folded.endswith("s"):
        return folded[:-1]
    return folded


def _anchors(text: str) -> frozenset[str]:
    return frozenset(
        normalized for match in _TOKEN.finditer(text)
        if (normalized := _normalize(match.group())) not in _STOP
    )


def _render(value: Decimal) -> str:
    return format(value.normalize(), "f")


@dataclass(frozen=True)
class PercentageFact:
    value: str
    value_span: tuple[int, int]
    percent_span: tuple[int, int]
    context_span: tuple[int, int]
    context_text: str
    context_anchors: tuple[str, ...]


@dataclass(frozen=True)
class PercentageComplementProof:
    question_sha256: str
    passage_sha256: str
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
            self.predicate_span, self.predicate_anchors, self.fact, self.result,
        )


def _query_predicate(question: str) -> tuple[str, tuple[int, int]] | None:
    match = _QUERY.fullmatch(question.strip())
    if not match:
        return None
    group = "predicate" if match.group("predicate") is not None else "predicate_not"
    text = match.group(group).strip()
    start = question.casefold().find(text.casefold())
    return text, (start, start + len(text))


def _facts(passage: str) -> tuple[PercentageFact, ...]:
    facts = []
    for item in _ITEM.finditer(passage):
        context = item.group()
        if context.count("(") != context.count(")"):
            # A delimiter cut through a nested percentage decomposition.  Its
            # inner value cannot inherit the outer predicate (or vice versa).
            continue
        context_anchors = tuple(sorted(_anchors(context)))
        for percent in _PERCENT.finditer(context):
            prefix = context[max(0, percent.start() - 24):percent.start()]
            value = Decimal(percent.group("value"))
            if _APPROX.search(prefix) or value < 0 or value > 100:
                continue
            facts.append(PercentageFact(
                _render(value),
                (item.start() + percent.start("value"), item.start() + percent.end("value")),
                (item.start() + percent.start(), item.start() + percent.end()), item.span(),
                context, context_anchors,
            ))
    return tuple(facts)


def _derive(question: str, passage: str):
    parsed = _query_predicate(question)
    if parsed is None:
        return None
    predicate, predicate_span = parsed
    if re.search(r"\bor\b", predicate, re.IGNORECASE):
        return None
    anchors = _anchors(predicate)
    if not anchors:
        return None
    winners = [fact for fact in _facts(passage) if anchors <= set(fact.context_anchors)]
    if len(winners) != 1:
        return None
    fact = winners[0]
    result = _render(Decimal(100) - Decimal(fact.value))
    return predicate_span, tuple(sorted(anchors)), fact, result


def compile_percentage_complement(question: str, passage: str) -> PercentageComplementProof | None:
    if not question or not passage:
        return None
    derived = _derive(question, passage)
    if derived is None:
        return None
    return PercentageComplementProof(
        hashlib.sha256(question.encode()).hexdigest(), hashlib.sha256(passage.encode()).hexdigest(),
        derived[0], derived[1], derived[2], derived[3],
    )
