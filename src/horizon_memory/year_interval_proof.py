# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Proof-carrying arithmetic for explicitly question-grounded year intervals."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re


_YEAR = re.compile(r"(?<![\d-])(?P<year>1[0-9]{3}|20[0-9]{2})(?![\d-])")
_ELAPSED = re.compile(
    r"\bhow many years (?:have )?passed between\b",
    re.IGNORECASE,
)
_BANNED = re.compile(
    r"\b(?:centur(?:y|ies)|decades?|years? old|age(?:d)?|anniversary)\b|"
    r"\b(?:1[0-9]{3}|20[0-9]{2})s\b|"
    r"\b(?:1[0-9]{3}|20[0-9]{2})\s*[–—-]\s*\d{1,4}\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class YearOperand:
    value: int
    span: tuple[int, int]
    text: str


@dataclass(frozen=True)
class YearIntervalProof:
    question_sha256: str
    question_span: tuple[int, int]
    operator_span: tuple[int, int]
    operands: tuple[YearOperand, YearOperand]
    result: int
    state: str
    reason: str

    def verify(self, question: str) -> bool:
        if hashlib.sha256(question.encode()).hexdigest() != self.question_sha256:
            return False
        if self.question_span != (0, len(question)):
            return False
        if self.state != "closed" or len(self.operands) != 2:
            return False
        if not (0 <= self.operator_span[0] < self.operator_span[1] <= len(question)):
            return False
        for operand in self.operands:
            if not (0 <= operand.span[0] < operand.span[1] <= len(question)):
                return False
            if question[operand.span[0]:operand.span[1]] != operand.text:
                return False
            if int(operand.text) != operand.value:
                return False
        return self.result == abs(self.operands[1].value - self.operands[0].value)


def compile_year_interval(question: str) -> YearIntervalProof | None:
    if not question or _BANNED.search(question):
        return None
    operator = _ELAPSED.search(question)
    years = list(_YEAR.finditer(question))
    if operator is None or len(years) != 2:
        return None
    operands = tuple(
        YearOperand(int(match.group("year")), match.span("year"), match.group("year"))
        for match in years
    )
    return YearIntervalProof(
        hashlib.sha256(question.encode()).hexdigest(), (0, len(question)), operator.span(),
        operands, abs(operands[1].value - operands[0].value), "closed",
        "two explicit Gregorian year operands and an elapsed-year operator",
    )
