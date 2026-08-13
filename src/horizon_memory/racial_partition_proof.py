# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Typed complement over an explicitly framed census racial-makeup universe."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import re

from .percentage_complement_proof import PercentageFact, _facts, _render


_SENTENCE = re.compile(r".+?(?:(?<!\d)[.!?]+(?!\d)|$)")
_FRAME = re.compile(r"\bracial makeup\b", re.I)
_QUERY = re.compile(
    r"^how many percent of (?P<carrier>(?:the\s+)?(?:people|population|"
    r"(?:county|city|town|village) population)) "
    r"(?:were|was|are|is) not (?P<category>.+?)\??$",
    re.I,
)


def _canonical(text: str) -> str | None:
    normalized = re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()
    normalized = re.sub(r"\bfrom\b", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if "non hispanic" in normalized:
        return None
    rules = (
        (r"^(?:white|whites)$", "white"),
        (r"^(?:black|blacks|african american|black or african american|blacks or african americans)$", "black"),
        (r"^(?:asian|asians|asian american|asian americans)$", "asian"),
        (r"^(?:american indian|american indians|native american|native americans)$", "native_american"),
        (r"^(?:pacific islander|pacific islanders)$", "pacific_islander"),
        (r"^(?:other race|other races)$", "other"),
        (r"^(?:2|two) or more races$", "multiracial"),
    )
    for pattern, value in rules:
        if re.fullmatch(pattern, normalized):
            return value
    return None


def _context_category(context: str) -> str | None:
    without_prefix = re.sub(r"^.*?\d{1,3}(?:\.\d+)?\s*%\s*", "", context, count=1)
    without_prefix = re.sub(r"\b(?:of the population|of residents)\b.*$", "", without_prefix, flags=re.I)
    without_prefix = re.sub(r"\s+and\s*$", "", without_prefix, flags=re.I)
    return _canonical(without_prefix.strip(" ,.;()\""))


@dataclass(frozen=True)
class RacialPartitionFact:
    category: str
    percentage: str
    frame_span: tuple[int, int]
    percentage_fact: PercentageFact


@dataclass(frozen=True)
class RacialPartitionProof:
    question_sha256: str
    passage_sha256: str
    category: str
    fact: RacialPartitionFact
    result: str

    def verify(self, question: str, passage: str) -> bool:
        if hashlib.sha256(question.encode()).hexdigest() != self.question_sha256:
            return False
        if hashlib.sha256(passage.encode()).hexdigest() != self.passage_sha256:
            return False
        derived = _derive(question, passage)
        return derived is not None and derived == (self.category, self.fact, self.result)


def _eligible_facts(passage: str, category: str) -> tuple[RacialPartitionFact, ...]:
    all_facts = _facts(passage)
    eligible = []
    for sentence in _SENTENCE.finditer(passage):
        if _FRAME.search(sentence.group()) is None:
            continue
        for fact in all_facts:
            if not (sentence.start() <= fact.context_span[0] and fact.context_span[1] <= sentence.end()):
                continue
            if _context_category(fact.context_text) == category:
                eligible.append(RacialPartitionFact(category, fact.value, sentence.span(), fact))
    return tuple(eligible)


def extract_closed_racial_partition(passage: str) -> tuple[RacialPartitionFact, ...] | None:
    """Return one complete, unique, rounding-closed racial partition."""
    all_facts = _facts(passage)
    frames = []
    for sentence in _SENTENCE.finditer(passage):
        if _FRAME.search(sentence.group()) is None:
            continue
        current = []
        for fact in all_facts:
            if not (sentence.start() <= fact.context_span[0] and fact.context_span[1] <= sentence.end()):
                continue
            category = _context_category(fact.context_text)
            if category is not None:
                current.append(RacialPartitionFact(category, fact.value, sentence.span(), fact))
        if current:
            frames.append(tuple(current))
    if len(frames) != 1:
        return None
    partition = frames[0]
    if len(partition) < 4 or len({fact.category for fact in partition}) != len(partition):
        return None
    total = sum(Decimal(fact.percentage) for fact in partition)
    if not (Decimal(99) <= total <= Decimal(101)):
        return None
    return partition


def _derive(question: str, passage: str):
    match = _QUERY.fullmatch(question.strip())
    if not match:
        return None
    category = _canonical(match.group("category"))
    if category is None:
        return None
    facts = _eligible_facts(passage, category)
    if len(facts) != 1:
        return None
    fact = facts[0]
    result = _render(Decimal(100) - Decimal(fact.percentage))
    return category, fact, result


def compile_racial_partition(question: str, passage: str) -> RacialPartitionProof | None:
    if not question or not passage:
        return None
    derived = _derive(question, passage)
    if derived is None:
        return None
    return RacialPartitionProof(
        hashlib.sha256(question.encode()).hexdigest(), hashlib.sha256(passage.encode()).hexdigest(),
        derived[0], derived[1], derived[2],
    )
