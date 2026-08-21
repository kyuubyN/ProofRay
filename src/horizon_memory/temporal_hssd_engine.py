# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exact HSSD projection of event clocks and temporal differences."""
from __future__ import annotations

from dataclasses import dataclass

from .hssd_query_compiler import HSSDQueryPlan
from .raw_causal_channels import observe_raw_text
from .standalone_causal_memory import StandaloneCausalMemory
from .sufficient_statistic_search import HorizonSufficientStatisticSearch, SufficientStatisticPack
from .typed_causal_program import TypedCausalFact, TypedCausalProof, TypedCausalResult


@dataclass(frozen=True)
class TemporalHSSDProgram:
    operator: str
    fibers: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class TemporalHSSDResult:
    state: str
    value: str | None
    unit: str
    fact_ids: tuple[int, ...]
    pack: SufficientStatisticPack
    program: TemporalHSSDProgram | None
    proofs: tuple[TypedCausalProof, ...]
    reason: str


class TemporalHSSDProgramCompiler:
    _SUPPORTED = {"lookup_time": "PROJECT_EVENT_TIME", "duration": "DURATION",
                  "interval": "INTERVAL"}

    @staticmethod
    def _tokens(fact: TypedCausalFact) -> set[str]:
        return set(observe_raw_text(f"{fact.subject} {fact.predicate}").lexical)

    def compile(self, plan: HSSDQueryPlan, facts: tuple[TypedCausalFact, ...],
                fact_ids: tuple[int, ...]) -> TemporalHSSDProgram | None:
        operator = self._SUPPORTED.get(plan.operation)
        if operator is None:
            return None
        by_id = {item.fact_id: item for item in facts}
        if any(fact_id not in by_id for fact_id in fact_ids):
            raise ValueError("temporal HSSD FactId absent from causal field")
        query = set(plan.address_atoms.lexical)
        fibers = {}
        for fact_id in fact_ids:
            fact = by_id[fact_id]
            fiber = (fact.subject, fact.predicate)
            fibers.setdefault(fiber, self._tokens(fact))
        ranked = sorted(fibers, key=lambda fiber: (
            -len(query.intersection(fibers[fiber])), fiber))
        needed = 1 if operator == "PROJECT_EVENT_TIME" else 2
        if len(ranked) < needed:
            return None
        chosen = tuple(ranked[:needed])
        # Every chosen fiber must relate to the query, not just the first: for a 2-operand
        # program (duration/interval), a query naming only one entity still fills the second
        # slot from whatever fiber sorts next -- checking only chosen[0] let a fiber with ZERO
        # query overlap (an unrelated support document) become the second operand, producing a
        # numerically "resolved" but meaningless duration between unrelated entities (found via
        # code review, 2026-08-2x). Fall back to "unsupported" instead of guessing.
        if any(not query.intersection(fibers[fiber]) for fiber in chosen):
            return None
        return TemporalHSSDProgram(operator, chosen)


class StandaloneTemporalHSSDEngine:
    def __init__(self, search: HorizonSufficientStatisticSearch,
                 memory: StandaloneCausalMemory, facts: tuple[TypedCausalFact, ...]):
        self.search, self.memory, self.facts = search, memory, facts
        self.compiler = TemporalHSSDProgramCompiler()

    def _latest(self, fiber: tuple[str, str]) -> TypedCausalFact | None:
        # Group by orbit (event identity, `TypedCausalFact.orbit`) before resolving "latest",
        # not by fiber alone: a fiber is (subject, predicate), and the design explicitly allows
        # multiple distinct events under one fiber (e.g. "John visited Paris" and "John visited
        # London" share the fiber (John, visited) but are different orbits). Computing one
        # max(version, observed_at) across all of them mixed together made two genuinely
        # different, individually unambiguous events look like one internally-conflicting report
        # (their event_times legitimately differ), wrongly aborting recurring-action queries that
        # had a perfectly good single answer (found via code review, 2026-08-2x; mirrors the same
        # per-orbit collapse already used by typed_causal_program.TypedCausalExecutor).
        rows = [item for item in self.facts if (item.subject, item.predicate) == fiber]
        if not rows:
            return None
        by_orbit: dict[str, list[TypedCausalFact]] = {}
        for item in rows:
            by_orbit.setdefault(item.orbit, []).append(item)
        candidates = []
        for orbit_rows in by_orbit.values():
            clock = max((item.version, item.observed_at) for item in orbit_rows)
            latest = [item for item in orbit_rows if (item.version, item.observed_at) == clock]
            signatures = {(item.event_time, item.polarity, item.asserted) for item in latest}
            if len(signatures) != 1:
                continue  # conflicting reports within this one event's own history
            candidate = min(latest, key=lambda item: item.fact_id)
            if candidate.asserted and candidate.polarity > 0:
                candidates.append(candidate)
        if len(candidates) != 1:
            return None
        return candidates[0]

    def query(self, question: str, *, max_results: int = 32,
              max_bytes: int | None = None) -> TemporalHSSDResult:
        pack = self.search.search(question, max_results=max_results, max_bytes=max_bytes)
        if pack.state != "ready":
            return TemporalHSSDResult(pack.state, None, "", pack.fact_ids, pack, None, (), pack.reason)
        program = self.compiler.compile(pack.query_plan, self.facts, pack.fact_ids)
        if program is None:
            return TemporalHSSDResult("unsupported", None, "", pack.fact_ids, pack, None, (),
                                      "no exact temporal program mapping")
        operands = tuple(self._latest(fiber) for fiber in program.fibers)
        if any(item is None for item in operands):
            return TemporalHSSDResult("abstain", None, "", (), pack, program, (),
                                      "temporal operand is absent, conflicting or uncertain")
        typed = tuple(item for item in operands if item is not None)
        proofs = tuple(TypedCausalProof(item.fact_id, item.source_id, item.source_sha256,
                                        item.source_span) for item in typed)
        if not all(self.memory.verify_proof(proof) for proof in proofs):
            return TemporalHSSDResult("abstain", None, "", (), pack, program, (),
                                      "temporal proof failed revalidation")
        if program.operator == "PROJECT_EVENT_TIME":
            value, unit = str(typed[0].event_time), "tick"
        else:
            value, unit = str(abs(typed[1].event_time - typed[0].event_time)), "tick"
        return TemporalHSSDResult("resolved", value, unit,
                                  tuple(item.fact_id for item in typed), pack, program, proofs,
                                  "exact verified temporal projection")
