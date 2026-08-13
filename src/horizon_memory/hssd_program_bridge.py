# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compile a ready HSSD pack into the existing exact typed causal DSL."""
from __future__ import annotations

from dataclasses import dataclass

from .hssd_query_compiler import HSSDQueryPlan
from .raw_causal_channels import observe_raw_text
from .typed_causal_program import CausalSelector, TypedCausalFact, TypedCausalProgram


@dataclass(frozen=True)
class HSSDProgramCompileResult:
    state: str
    program: TypedCausalProgram | None
    candidate_fibers: tuple[tuple[str, str], ...]
    reason: str


class TypedHSSDProgramCompiler:
    """Choose a unique retrieved causal fiber; never invent subject or predicate."""

    _SUPPORTED = {
        "lookup": "LOOKUP",
        "count_distinct": "COUNT_DISTINCT",
        "sum": "SUM",
        "explain_cause": "EXPLAIN_CAUSE",
    }

    @staticmethod
    def _fiber_tokens(fact: TypedCausalFact) -> set[str]:
        return set(observe_raw_text(f"{fact.subject} {fact.predicate}").lexical)

    def compile(self, plan: HSSDQueryPlan, facts: tuple[TypedCausalFact, ...],
                selected_fact_ids: tuple[int, ...]) -> HSSDProgramCompileResult:
        if plan.state != "compiled":
            return HSSDProgramCompileResult("unsupported", None, (), plan.reason)
        operator = self._SUPPORTED.get(plan.operation)
        if operator is None:
            return HSSDProgramCompileResult(
                "unsupported", None, (), "HSSD operation has no exact typed executor mapping")
        by_id = {item.fact_id: item for item in facts}
        if any(fact_id not in by_id for fact_id in selected_fact_ids):
            raise ValueError("selected HSSD FactId is absent from the typed causal field")
        selected = tuple(by_id[fact_id] for fact_id in selected_fact_ids)
        query_tokens = set(plan.address_atoms.lexical)
        fibers: dict[tuple[str, str], list[TypedCausalFact]] = {}
        for fact in selected:
            fibers.setdefault((fact.subject, fact.predicate), []).append(fact)
        scored = []
        for fiber, rows in fibers.items():
            overlap = len(query_tokens.intersection(self._fiber_tokens(rows[0])))
            scored.append((overlap, len(rows), fiber))
        if not scored:
            return HSSDProgramCompileResult("abstain", None, (), "no typed fiber in HSSD pack")
        best_overlap = max(item[0] for item in scored)
        winners = tuple(sorted(item[2] for item in scored if item[0] == best_overlap))
        if best_overlap <= 0 or len(winners) != 1:
            return HSSDProgramCompileResult(
                "abstain", None, winners, "typed causal fiber is absent or ambiguous")
        subject, predicate = winners[0]
        unit = ""
        if operator == "SUM":
            units = {fact.unit for fact in fibers[winners[0]] if fact.unit}
            if len(units) != 1:
                return HSSDProgramCompileResult(
                    "abstain", None, winners, "sum unit is absent or conflicting")
            unit = next(iter(units))
        program = TypedCausalProgram(
            operator, CausalSelector(subject, predicate), unit=unit,
            closed_world=operator in ("COUNT_DISTINCT", "SUM"))
        return HSSDProgramCompileResult(
            "compiled", program, winners, "unique retrieved typed causal fiber")
