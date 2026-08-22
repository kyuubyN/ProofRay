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
            # The base compiler closes `slot:clock_pair` only when it sees 2 *distinct clock
            # labels* (line ~233 in hssd_query_compiler.py); two verified temporal operands that
            # happen to share the same label (e.g. both "event_time") leave it open there even
            # though this class's own, stricter criterion -- 2 independently verified operands,
            # regardless of label -- is already satisfied. Left unclosed, a valid duration/
            # interval query stayed permanently `execution_ready=False` (found via code review,
            # 2026-08-2x). Close it ourselves when that's the only thing still open.
            if "slot:clock_pair" not in closure.residual:
                return closure
            closed = set(closure.closed)
            closed.add("slot:clock_pair")
            residual = set(closure.residual)
            residual.discard("slot:clock_pair")
            execution_ready = not residual
            return replace(closure, state="ready" if execution_ready else "incomplete",
                           closed=tuple(sorted(closed)), residual=tuple(sorted(residual)),
                           execution_ready=execution_ready,
                           reason=("all obligations closed" if execution_ready
                                   else closure.reason))
        closed = set(closure.closed)
        closed.discard("slot:clock_pair")
        residual = set(closure.residual)
        residual.add("slot:clock_pair")
        return replace(closure, state="incomplete", closed=tuple(sorted(closed)),
                       residual=tuple(sorted(residual)), execution_ready=False,
                       reason="two independently verified temporal operands are required")
