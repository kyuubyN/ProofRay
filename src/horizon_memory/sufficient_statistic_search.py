# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compose HPPS retrieval with structural HSSD closure through a standalone adapter."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .hssd_query_compiler import (
    HSSDClosure,
    HSSDEvidenceObservation,
    HSSDQueryPlan,
    StructuralHSSDQueryCompiler,
)
from .proof_pressure_search import HorizonSearchEngine, ProofPressureResult


@runtime_checkable
class HSSDEvidenceAdapter(Protocol):
    """Translate a retrieved FactId into independently verified typed charges."""

    @property
    def adapter_id(self) -> str: ...

    def observe(self, fact_id: int) -> HSSDEvidenceObservation | None: ...


@dataclass(frozen=True)
class MappingHSSDEvidenceAdapter:
    adapter_id: str
    observations: tuple[HSSDEvidenceObservation, ...]

    def __post_init__(self) -> None:
        if not self.adapter_id or len({item.fact_id for item in self.observations}) != len(
                self.observations):
            raise ValueError("adapter id and unique observation FactIds are required")

    def observe(self, fact_id: int) -> HSSDEvidenceObservation | None:
        return next((item for item in self.observations if item.fact_id == fact_id), None)


@dataclass(frozen=True)
class SufficientStatisticPack:
    state: str
    query_plan: HSSDQueryPlan
    retrieval: ProofPressureResult
    fact_ids: tuple[int, ...]
    observations: tuple[HSSDEvidenceObservation, ...]
    closure: HSSDClosure
    adapter_id: str
    examined_fact_ids: tuple[int, ...]
    evidence_bytes: int
    reason: str


class HorizonSufficientStatisticSearch:
    """Stop retrieval at the first verified prefix that closes the typed query."""

    def __init__(self, search_engine: HorizonSearchEngine,
                 evidence_adapter: HSSDEvidenceAdapter,
                 query_compiler: StructuralHSSDQueryCompiler | None = None):
        if not isinstance(evidence_adapter, HSSDEvidenceAdapter):
            raise TypeError("a runtime-compatible HSSD evidence adapter is required")
        if not evidence_adapter.adapter_id:
            raise ValueError("evidence adapter id is required")
        self.search_engine = search_engine
        self.evidence_adapter = evidence_adapter
        self.query_compiler = query_compiler or StructuralHSSDQueryCompiler()

    def search(self, query_text: str, *, max_results: int = 32,
               max_bytes: int | None = None,
               hard_exclusions: tuple[int, ...] = ()) -> SufficientStatisticPack:
        plan = self.query_compiler.compile(query_text)
        retrieval = self.search_engine.search(
            query_text, max_results=max_results, max_bytes=max_bytes,
            hard_exclusions=hard_exclusions, exploration_reserve=max_results)
        if plan.state != "compiled":
            closure = self.query_compiler.assess(plan, ())
            return SufficientStatisticPack(
                "unsupported", plan, retrieval, (), (), closure,
                self.evidence_adapter.adapter_id, (), 0, plan.reason)

        observations = []
        examined = []
        closure = self.query_compiler.assess(plan, ())
        for fact_id in retrieval.fact_ids:
            examined.append(fact_id)
            observation = self.evidence_adapter.observe(fact_id)
            if observation is None:
                continue
            if observation.fact_id != fact_id:
                raise ValueError("evidence adapter returned a mismatched FactId")
            observations.append(observation)
            closure = self.query_compiler.assess(plan, tuple(observations))
            if closure.execution_ready or closure.state == "conflict":
                break

        fact_ids = tuple(item.fact_id for item in observations)
        evidence_bytes = sum(self.search_engine.byte_cost[fact_id] for fact_id in fact_ids)
        if closure.state == "conflict":
            state, reason = "conflict", closure.reason
        elif closure.execution_ready:
            state, reason = "ready", "minimal retrieved prefix closes the HSSD"
        else:
            state, reason = "incomplete", "retrieval exhausted before typed closure"
        return SufficientStatisticPack(
            state, plan, retrieval, fact_ids, tuple(observations), closure,
            self.evidence_adapter.adapter_id, tuple(examined), evidence_bytes, reason)
