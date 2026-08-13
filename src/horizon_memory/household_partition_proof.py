# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Typed complement algebra over an explicitly introduced household universe."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import re


_SENTENCE = re.compile(r".+?(?:(?<!\d)[.!?]+(?!\d)|$)")
_UNIVERSE = re.compile(r"\b(?:there were\s+)?(?P<count>\d[\d,]*)\s+households\b|\bhouseholds\b", re.I)
_CHILDREN = re.compile(
    r"(?<![\d.])(?P<value>\d{1,3}(?:\.\d+)?)\s*%\s+had children under "
    r"(?:the\s+)?age of 18 living with them\b",
    re.I,
)
_NON_FAMILIES = re.compile(
    r"(?<![\d.])(?P<value>\d{1,3}(?:\.\d+)?)\s*%\s+were non-families\b",
    re.I,
)
_QUERY_CHILDREN = re.compile(
    r"^how many(?: in)? percent(?:age points)? of (?:the\s+\d[\d,]*|(?:the\s+)?households) "
    r"(?:didn't|did not) have children under (?:the\s+)?age of 18 living with them\??$",
    re.I,
)
_QUERY_FAMILIES = re.compile(
    r"^how many(?: in)? percent(?:age points)? of (?:the\s+\d[\d,]*|(?:the\s+)?households) "
    r"(?:were|are) (?:considered )?families\??$",
    re.I,
)


def _render(value: Decimal) -> str:
    return format(value.normalize(), "f")


@dataclass(frozen=True)
class HouseholdPartitionFact:
    relation: str
    percentage: str
    universe_span: tuple[int, int]
    percentage_span: tuple[int, int]
    source_span: tuple[int, int]
    text: str


@dataclass(frozen=True)
class HouseholdPartitionProof:
    question_sha256: str
    passage_sha256: str
    query_relation: str
    fact: HouseholdPartitionFact
    result: str

    def verify(self, question: str, passage: str) -> bool:
        if hashlib.sha256(question.encode()).hexdigest() != self.question_sha256:
            return False
        if hashlib.sha256(passage.encode()).hexdigest() != self.passage_sha256:
            return False
        derived = _derive(question, passage)
        return derived is not None and derived == (self.query_relation, self.fact, self.result)


def _query_relation(question: str) -> str | None:
    stripped = question.strip()
    if _QUERY_CHILDREN.fullmatch(stripped):
        return "without_children_under_18"
    if _QUERY_FAMILIES.fullmatch(stripped):
        return "families"
    return None


def _facts(passage: str, relation: str) -> tuple[HouseholdPartitionFact, ...]:
    pattern = _CHILDREN if relation == "without_children_under_18" else _NON_FAMILIES
    source_relation = "with_children_under_18" if relation == "without_children_under_18" else "non_families"
    facts = []
    for sentence in _SENTENCE.finditer(passage):
        universe = _UNIVERSE.search(sentence.group())
        if universe is None:
            continue
        for match in pattern.finditer(sentence.group()):
            value = Decimal(match.group("value"))
            if value < 0 or value > 100:
                continue
            start, end = sentence.start() + match.start(), sentence.start() + match.end()
            facts.append(HouseholdPartitionFact(
                source_relation, _render(value),
                (sentence.start() + universe.start(), sentence.start() + universe.end()),
                (sentence.start() + match.start("value"), sentence.start() + match.end("value")),
                (start, end), passage[start:end],
            ))
    return tuple(facts)


def _derive(question: str, passage: str):
    relation = _query_relation(question)
    if relation is None:
        return None
    facts = _facts(passage, relation)
    values = {fact.percentage for fact in facts}
    if len(facts) != 1 or len(values) != 1:
        return None
    fact = facts[0]
    result = _render(Decimal(100) - Decimal(fact.percentage))
    return relation, fact, result


def compile_household_partition(question: str, passage: str) -> HouseholdPartitionProof | None:
    if not question or not passage:
        return None
    derived = _derive(question, passage)
    if derived is None:
        return None
    return HouseholdPartitionProof(
        hashlib.sha256(question.encode()).hexdigest(), hashlib.sha256(passage.encode()).hexdigest(),
        derived[0], derived[1], derived[2],
    )
