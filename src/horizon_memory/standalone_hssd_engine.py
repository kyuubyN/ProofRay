# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""End-to-end deterministic Horizon query over search, HSSD and causal execution."""
from __future__ import annotations

from dataclasses import dataclass

from .hssd_program_bridge import HSSDProgramCompileResult, TypedHSSDProgramCompiler
from .standalone_causal_memory import StandaloneCausalMemory
from .sufficient_statistic_search import (
    HorizonSufficientStatisticSearch,
    SufficientStatisticPack,
)
from .typed_causal_program import TypedCausalFact, TypedCausalResult


@dataclass(frozen=True)
class StandaloneHSSDResult:
    state: str
    value: str | None
    unit: str
    fact_ids: tuple[int, ...]
    pack: SufficientStatisticPack
    program_compilation: HSSDProgramCompileResult
    causal_result: TypedCausalResult | None
    reason: str


class StandaloneHSSDEngine:
    """No model/API path: verified retrieval prefix -> exact program -> proof."""

    def __init__(self, search: HorizonSufficientStatisticSearch,
                 memory: StandaloneCausalMemory,
                 facts: tuple[TypedCausalFact, ...]):
        self.search = search
        self.memory = memory
        self.facts = facts
        self.program_compiler = TypedHSSDProgramCompiler()

    def query(self, question: str, *, max_results: int = 32,
              max_bytes: int | None = None) -> StandaloneHSSDResult:
        pack = self.search.search(question, max_results=max_results, max_bytes=max_bytes)
        if pack.state != "ready":
            compilation = HSSDProgramCompileResult(
                "not_attempted", None, (), "HSSD pack is not execution-ready")
            return StandaloneHSSDResult(
                pack.state, None, "", pack.fact_ids, pack, compilation, None, pack.reason)
        compilation = self.program_compiler.compile(
            pack.query_plan, self.facts, pack.fact_ids)
        if compilation.program is None:
            return StandaloneHSSDResult(
                compilation.state, None, "", pack.fact_ids, pack, compilation, None,
                compilation.reason)
        result = self.memory.query(compilation.program)
        proofs_valid = all(self.memory.verify_proof(proof) for proof in result.proofs)
        if result.state == "resolved" and not proofs_valid:
            return StandaloneHSSDResult(
                "abstain", None, "", (), pack, compilation, result,
                "causal result proof failed final revalidation")
        return StandaloneHSSDResult(
            result.state, result.value, result.unit, result.fact_ids,
            pack, compilation, result, result.reason)
