# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Proof-carrying difference for two explicit homogeneous question quantities."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re


_QUANTITY = re.compile(r"(?<!\d)(?P<value>\d{1,4})\s*(?:-|\s)(?P<unit>yards?)\b", re.I)
_OPERATOR = re.compile(
    r"\bhow many more yards\b[^?]{0,160}\bcompared to\b",
    re.IGNORECASE,
)
_BANNED = re.compile(r"\b(?:between|ranging|range|total|accumulat|percent|average|at least|at most)\b", re.I)


@dataclass(frozen=True)
class QuantityOperand:
    value: int
    value_span: tuple[int, int]
    unit_span: tuple[int, int]
    text: str


@dataclass(frozen=True)
class HomogeneousDifferenceProof:
    question_sha256: str
    operator_span: tuple[int, int]
    operands: tuple[QuantityOperand, QuantityOperand]
    result: int

    def verify(self, question: str) -> bool:
        if hashlib.sha256(question.encode()).hexdigest() != self.question_sha256:
            return False
        if not (0 <= self.operator_span[0] < self.operator_span[1] <= len(question)):
            return False
        for operand in self.operands:
            start, end = operand.value_span
            if question[start:end] != str(operand.value):
                return False
            if question[operand.unit_span[0]:operand.unit_span[1]].casefold().rstrip("s") != "yard":
                return False
        return self.operands[0].value >= self.operands[1].value and self.result == (
            self.operands[0].value - self.operands[1].value
        )


def compile_homogeneous_difference(question: str) -> HomogeneousDifferenceProof | None:
    if not question or _BANNED.search(question):
        return None
    operator = _OPERATOR.search(question)
    quantities = list(_QUANTITY.finditer(question))
    if operator is None or len(quantities) != 2:
        return None
    values = [int(item.group("value")) for item in quantities]
    if values[0] < values[1]:
        return None
    operands = tuple(
        QuantityOperand(
            int(item.group("value")), item.span("value"), item.span("unit"), item.group(),
        )
        for item in quantities
    )
    return HomogeneousDifferenceProof(
        hashlib.sha256(question.encode()).hexdigest(), operator.span(), operands,
        values[0] - values[1],
    )
