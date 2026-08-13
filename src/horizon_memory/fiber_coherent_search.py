# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""HSSD retrieval whose noncompensable obligations must close in one causal fiber."""
from __future__ import annotations

from collections import defaultdict

from .hssd_query_compiler import HSSDClosure, StructuralHSSDQueryCompiler
from .proof_pressure_search import HorizonSearchEngine
from .sufficient_statistic_search import (
    HSSDEvidenceAdapter, SufficientStatisticPack,
)
from .typed_causal_program import TypedCausalFact


class FiberCoherentSufficientStatisticSearch:
    """Prevent charges from unrelated `(subject, predicate)` fibers from composing.

    Retrieval remains plural, but readiness is assessed independently inside each
    verified fiber.  This is the executable boundary required by HAFP: a matching
    entity in one fiber cannot authorize completeness, unit, role, or value in another.
    """

    def __init__(self, search_engine: HorizonSearchEngine,
                 evidence_adapter: HSSDEvidenceAdapter,
                 facts: tuple[TypedCausalFact, ...],
                 query_compiler: StructuralHSSDQueryCompiler | None = None):
        if not facts or tuple(item.fact_id for item in facts) != tuple(
                sorted({item.fact_id for item in facts})):
            raise ValueError("fiber-coherent search requires canonical typed facts")
        self.search_engine = search_engine
        self.evidence_adapter = evidence_adapter
        self.query_compiler = query_compiler or StructuralHSSDQueryCompiler()
        self._fact_by_id = {item.fact_id: item for item in facts}

    def search(self, query_text: str, *, max_results: int = 32,
               max_bytes: int | None = None,
               hard_exclusions: tuple[int, ...] = ()) -> SufficientStatisticPack:
        plan = self.query_compiler.compile(query_text)
        retrieval = self.search_engine.search(
            query_text, max_results=max_results, max_bytes=max_bytes,
            hard_exclusions=hard_exclusions, exploration_reserve=max_results)
        empty = self.query_compiler.assess(plan, ())
        if plan.state != "compiled":
            return SufficientStatisticPack(
                "unsupported", plan, retrieval, (), (), empty,
                self.evidence_adapter.adapter_id, (), 0, plan.reason)

        groups = defaultdict(list)
        closures: dict[tuple[str, str], HSSDClosure] = {}
        examined = []
        for fact_id in retrieval.fact_ids:
            examined.append(fact_id)
            fact = self._fact_by_id.get(fact_id)
            if fact is None:
                raise ValueError("retrieved FactId is absent from the coherent causal field")
            observation = self.evidence_adapter.observe(fact_id)
            if observation is None:
                continue
            if observation.fact_id != fact_id:
                raise ValueError("evidence adapter returned a mismatched FactId")
            fiber = (fact.subject, fact.predicate)
            groups[fiber].append(observation)
            closure = self.query_compiler.assess(plan, tuple(groups[fiber]))
            closures[fiber] = closure
            if closure.execution_ready or closure.state == "conflict":
                selected = tuple(groups[fiber])
                state = "ready" if closure.execution_ready else "conflict"
                reason = ("one verified causal fiber closes the HSSD" if state == "ready"
                          else closure.reason)
                return SufficientStatisticPack(
                    state, plan, retrieval, tuple(item.fact_id for item in selected),
                    selected, closure, self.evidence_adapter.adapter_id,
                    tuple(examined), sum(self.search_engine.byte_cost[item.fact_id]
                                         for item in selected), reason)

        if closures:
            # Preserve the most informative coherent residual for diagnostics. Ties are
            # deterministic and cannot turn an incomplete pack into an executable one.
            fiber = min(closures, key=lambda key: (
                len(closures[key].residual), -len(closures[key].closed), key))
            selected = tuple(groups[fiber])
            closure = closures[fiber]
        else:
            selected, closure = (), empty
        return SufficientStatisticPack(
            "incomplete", plan, retrieval,
            tuple(item.fact_id for item in selected), selected, closure,
            self.evidence_adapter.adapter_id, tuple(examined),
            sum(self.search_engine.byte_cost[item.fact_id] for item in selected),
            "no single verified causal fiber closed every HSSD obligation")
