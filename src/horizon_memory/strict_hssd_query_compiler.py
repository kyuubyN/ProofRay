# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Successor HSSD closure rules for closed-world existence and temporal operands."""
from __future__ import annotations

from dataclasses import replace

from .hssd_query_compiler import (
    HSSDClosure,
    HSSDEvidenceObservation,
    HSSDObligation,
    HSSDQueryPlan,
    StructuralHSSDQueryCompiler,
)


class StrictStructuralHSSDQueryCompiler(StructuralHSSDQueryCompiler):
    """Enforce operand cardinality instead of counting clock labels as operands."""

    def compile(self, question: str) -> HSSDQueryPlan:
        plan = super().compile(question)
        if plan.state == "compiled" and plan.operation == "exists":
            obligations = tuple(sorted(plan.obligations + (
                HSSDObligation("proof:complete", "complete"),)))
            return replace(plan, obligations=obligations, require_complete=True,
                           reason="unique structural operator syndrome; closed-world boolean")
        return plan

    @staticmethod
    def assess(plan: HSSDQueryPlan,
               evidence: tuple[HSSDEvidenceObservation, ...]) -> HSSDClosure:
        closure = StructuralHSSDQueryCompiler.assess(plan, evidence)
        if plan.state != "compiled" or plan.operation not in ("duration", "interval"):
            return closure
        temporal_operands = {
            item.fact_id for item in evidence
            if item.proof_verified and "event_time" in item.clocks and not item.conflict
        }
        if len(temporal_operands) >= 2:
            return closure
        closed = set(closure.closed)
        closed.discard("slot:clock_pair")
        residual = set(closure.residual)
        residual.add("slot:clock_pair")
        return replace(closure, state="incomplete", closed=tuple(sorted(closed)),
                       residual=tuple(sorted(residual)), execution_ready=False,
                       reason="two independently verified temporal operands are required")
