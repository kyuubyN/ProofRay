# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""COUNT_DISTINCT over a closed typed racial-percentage partition."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import re

from .racial_partition_proof import RacialPartitionFact, extract_closed_racial_partition


_HEAD = re.compile(r"^how many (?:of the )?(?:races|racial groups|race groups)\b", re.I)
_PERCENT = re.compile(r"(?<![\d.])(?P<value>\d{1,3}(?:\.\d+)?)\s*%")
_BANNED = re.compile(r"\b(?:which|ancestr|ethnic|combined|not make up more)\b", re.I)


@dataclass(frozen=True)
class NumericPredicate:
    lower: str | None
    upper: str | None
    lower_inclusive: bool
    upper_inclusive: bool

    def accepts(self, value: Decimal) -> bool:
        if self.lower is not None:
            bound = Decimal(self.lower)
            if value < bound or (value == bound and not self.lower_inclusive):
                return False
        if self.upper is not None:
            bound = Decimal(self.upper)
            if value > bound or (value == bound and not self.upper_inclusive):
                return False
        return True


@dataclass(frozen=True)
class RacialFilterCountProof:
    question_sha256: str
    passage_sha256: str
    predicate: NumericPredicate
    partition: tuple[RacialPartitionFact, ...]
    selected_categories: tuple[str, ...]
    result: int

    def verify(self, question: str, passage: str) -> bool:
        if hashlib.sha256(question.encode()).hexdigest() != self.question_sha256:
            return False
        if hashlib.sha256(passage.encode()).hexdigest() != self.passage_sha256:
            return False
        derived = _derive(question, passage)
        return derived is not None and derived == (
            self.predicate, self.partition, self.selected_categories, self.result,
        )


def _predicate(question: str) -> NumericPredicate | None:
    if _HEAD.search(question) is None or _BANNED.search(question):
        return None
    values = [match.group("value") for match in _PERCENT.finditer(question)]
    folded = question.casefold()
    if len(values) == 2 and "more than" in folded and ("less than" in folded or "no more than" in folded):
        return NumericPredicate(values[0], values[1], False, "no more than" in folded)
    if len(values) != 1:
        return None
    value = values[0]
    if "no more than" in folded or "or lower" in folded:
        return NumericPredicate(None, value, False, True)
    if "less than" in folded or "smaller than" in folded:
        return NumericPredicate(None, value, False, False)
    if "at least" in folded or "or more" in folded:
        return NumericPredicate(value, None, True, False)
    if "more than" in folded or "larger than" in folded:
        return NumericPredicate(value, None, False, False)
    return None


def _derive(question: str, passage: str):
    predicate = _predicate(question)
    if predicate is None:
        return None
    partition = extract_closed_racial_partition(passage)
    if partition is None:
        return None
    selected = tuple(sorted(
        fact.category for fact in partition if predicate.accepts(Decimal(fact.percentage))
    ))
    return predicate, partition, selected, len(selected)


def compile_racial_filter_count(question: str, passage: str) -> RacialFilterCountProof | None:
    if not question or not passage:
        return None
    derived = _derive(question, passage)
    if derived is None:
        return None
    return RacialFilterCountProof(
        hashlib.sha256(question.encode()).hexdigest(), hashlib.sha256(passage.encode()).hexdigest(),
        derived[0], derived[1], derived[2], derived[3],
    )
