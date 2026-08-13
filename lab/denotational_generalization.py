"""Primitives for D28 denotational generalization and counterfactual audits.

This module deliberately contains no dataset reader and no model adapter.  Its job is to make the
scientific boundary executable: training may check a denotation, evaluation payloads cannot carry one,
and programs that coincide in one world can be separated by identity-preserving interventions.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import re
from typing import Iterable, Mapping, Sequence


_NUMBER = re.compile(r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)(?![\w])")
_CAPITALISED = re.compile(r"\b[A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+)*")
_TOKEN = re.compile(r"[a-z@#]+")


def canonical(value: Decimal) -> str:
    return format(value.normalize(), "f")


@dataclass(frozen=True)
class AbstractProgram:
    """A small executable IR over stable operand identities, never raw positions in a list."""

    operator: str
    operands: tuple[str, ...]

    def __post_init__(self) -> None:
        allowed = {"lookup", "count", "sum", "difference", "argmax", "argmin"}
        if self.operator not in allowed:
            raise ValueError(f"unsupported operator: {self.operator}")
        if not self.operands:
            raise ValueError("a program needs at least one operand identity")
        if len(set(self.operands)) != len(self.operands):
            raise ValueError("operand identities must be conserved, not duplicated")
        if self.operator in {"lookup"} and len(self.operands) != 1:
            raise ValueError("lookup requires exactly one operand")
        if self.operator == "difference" and len(self.operands) != 2:
            raise ValueError("difference requires exactly two ordered operands")

    def execute(self, world: Mapping[str, Decimal]) -> str:
        try:
            values = tuple(world[identity] for identity in self.operands)
        except KeyError as exc:
            raise ValueError(f"world is missing operand {exc.args[0]}") from exc
        if self.operator == "lookup":
            result = values[0]
        elif self.operator == "count":
            result = Decimal(len(values))
        elif self.operator == "sum":
            result = sum(values, Decimal(0))
        elif self.operator == "difference":
            result = values[0] - values[1]
        elif self.operator == "argmax":
            result = max(values)
        else:
            result = min(values)
        return canonical(result)


def counterfactual_worlds(identities: Sequence[str], *, worlds: int = 7) -> tuple[dict[str, Decimal], ...]:
    """Create deterministic identity-preserving interventions independent of observed values."""
    if worlds < 2:
        raise ValueError("at least two counterfactual worlds are required")
    if not identities or len(set(identities)) != len(identities):
        raise ValueError("identities must be non-empty and unique")
    generated = []
    for world_index in range(worlds):
        world: dict[str, Decimal] = {}
        for identity_index, identity in enumerate(identities):
            digest = hashlib.sha256(f"D28|{world_index}|{identity_index}|{identity}".encode()).digest()
            # Non-zero, signed and non-linearly distributed values make accidental algebraic equality
            # unlikely while remaining exactly reproducible without an RNG or floating point.
            magnitude = 1 + int.from_bytes(digest[:4], "big") % 997
            sign = -1 if digest[4] & 1 else 1
            world[identity] = Decimal(sign * magnitude)
        generated.append(world)
    return tuple(generated)


def semantic_signature(program: AbstractProgram, identities: Sequence[str], *, worlds: int = 7) -> tuple[str, ...]:
    """Behavioral signature under the same interventions for every competing program."""
    return tuple(program.execute(world) for world in counterfactual_worlds(identities, worlds=worlds))


def denotation_consistent(programs: Iterable[AbstractProgram], observed_world: Mapping[str, Decimal],
                          gold: str) -> tuple[AbstractProgram, ...]:
    """Training-only filter.  Callers must never expose this operation through an evaluation view."""
    return tuple(program for program in programs if program.execute(observed_world) == gold)


def equivalence_classes(programs: Iterable[AbstractProgram], identities: Sequence[str], *,
                        worlds: int = 7) -> dict[tuple[str, ...], tuple[AbstractProgram, ...]]:
    grouped: dict[tuple[str, ...], list[AbstractProgram]] = {}
    for program in programs:
        signature = semantic_signature(program, identities, worlds=worlds)
        grouped.setdefault(signature, []).append(program)
    return {signature: tuple(members) for signature, members in grouped.items()}


def abstract_question(question: str) -> str:
    """Mechanical surface key used only to prevent exact abstract-family leakage."""
    substituted = _NUMBER.sub("#", question.strip())
    substituted = _CAPITALISED.sub("@", substituted)
    return " ".join(_TOKEN.findall(substituted.casefold()))


def family_fold(question: str, *, folds: int = 5) -> int:
    if folds < 2:
        raise ValueError("generalization requires at least two folds")
    digest = hashlib.sha256(abstract_question(question).encode()).digest()
    return int.from_bytes(digest[:8], "big") % folds


@dataclass(frozen=True)
class EvaluationView:
    case_id: str
    question: str
    passage: str


@dataclass(frozen=True)
class TrainingView(EvaluationView):
    gold: str


def evaluation_view(example: TrainingView) -> EvaluationView:
    """Erase the denotation by type and construction before compiler evaluation."""
    return EvaluationView(case_id=example.case_id, question=example.question, passage=example.passage)
