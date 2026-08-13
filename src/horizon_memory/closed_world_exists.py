# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Closed-world EXISTS with a provenance-bound completeness certificate."""
from __future__ import annotations

from dataclasses import dataclass

from .hssd_query_compiler import HSSDEvidenceObservation
from .raw_causal_channels import observe_raw_text
from .standalone_causal_memory import StandaloneCausalMemory
from .strict_hssd_query_compiler import StrictStructuralHSSDQueryCompiler
from .typed_causal_program import TypedCausalFact, TypedCausalProof


@dataclass(frozen=True, order=True)
class ClosedWorldFiberCertificate:
    subject: str
    predicate: str
    proof_fact_id: int

    def __post_init__(self) -> None:
        if not self.subject or not self.predicate or self.proof_fact_id < 0:
            raise ValueError("closed-world certificate needs a fiber and proof FactId")


@dataclass(frozen=True)
class ClosedWorldExistsResult:
    state: str
    value: bool | None
    fact_ids: tuple[int, ...]
    proofs: tuple[TypedCausalProof, ...]
    reason: str


class ClosedWorldExistsEngine:
    """Prove true from a fact and false from a verified fiber seal."""

    def __init__(self, memory: StandaloneCausalMemory, facts: tuple[TypedCausalFact, ...],
                 certificates: tuple[ClosedWorldFiberCertificate, ...]):
        if not facts or not certificates or certificates != tuple(sorted(set(certificates))):
            raise ValueError("canonical facts and completeness certificates are required")
        self.memory, self.facts, self.certificates = memory, facts, certificates
        self.by_id = {item.fact_id: item for item in facts}
        if any(item.proof_fact_id not in self.by_id for item in certificates):
            raise ValueError("completeness certificate proof FactId is absent")
        self.compiler = StrictStructuralHSSDQueryCompiler()

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set(observe_raw_text(text).lexical)

    def _proof(self, fact: TypedCausalFact) -> TypedCausalProof:
        return TypedCausalProof(fact.fact_id, fact.source_id, fact.source_sha256, fact.source_span)

    def query(self, question: str) -> ClosedWorldExistsResult:
        plan = self.compiler.compile(question)
        if plan.state != "compiled" or plan.operation != "exists":
            return ClosedWorldExistsResult("unsupported", None, (), (),
                                           "query is not a closed-world existence program")
        query_tokens = set(plan.address_atoms.lexical)
        scored = []
        for certificate in self.certificates:
            fiber_tokens = self._tokens(f"{certificate.subject} {certificate.predicate}")
            scored.append((len(query_tokens.intersection(fiber_tokens)), certificate, fiber_tokens))
        best = max(item[0] for item in scored)
        winners = [item for item in scored if item[0] == best]
        if best <= 0 or len(winners) != 1:
            return ClosedWorldExistsResult("abstain", None, (), (),
                                           "closed-world fiber is absent or ambiguous")
        _, certificate, fiber_tokens = winners[0]
        query_entities = {value.casefold() for value in plan.address_atoms.entities}
        if query_entities and certificate.subject.casefold() not in query_entities:
            return ClosedWorldExistsResult("abstain", None, (), (),
                                           "query entity does not match the certified fiber")
        seal = self.by_id[certificate.proof_fact_id]
        seal_proof = self._proof(seal)
        if not self.memory.verify_proof(seal_proof):
            return ClosedWorldExistsResult("abstain", None, (), (),
                                           "completeness proof failed revalidation")
        closure = self.compiler.assess(plan, (HSSDEvidenceObservation(
            seal.fact_id, lexical=tuple(sorted(fiber_tokens)),
            entities=(certificate.subject,), proof_verified=True, complete=True),))
        if not closure.execution_ready:
            return ClosedWorldExistsResult("incomplete", None, (seal.fact_id,), (seal_proof,),
                                           "HSSD existence obligations remain open")

        residual = query_tokens.difference(fiber_tokens)
        candidates = [item for item in self.facts
                      if (item.subject, item.predicate) == (certificate.subject,
                                                           certificate.predicate)]
        if residual:
            candidates = [item for item in candidates
                          if residual <= self._tokens(item.value)]
        by_orbit = {}
        for fact in candidates:
            by_orbit.setdefault(fact.orbit, []).append(fact)
        active = []
        for rows in by_orbit.values():
            clock = max((item.version, item.observed_at) for item in rows)
            latest = [item for item in rows if (item.version, item.observed_at) == clock]
            signatures = {(item.value, item.polarity, item.asserted) for item in latest}
            if len(signatures) != 1 or not latest[0].asserted:
                return ClosedWorldExistsResult("abstain", None, (seal.fact_id,), (seal_proof,),
                                               "existence evidence is conflicting or uncertain")
            if latest[0].polarity > 0:
                active.append(min(latest, key=lambda item: item.fact_id))
        proofs = tuple(self._proof(item) for item in active) + (seal_proof,)
        if not all(self.memory.verify_proof(proof) for proof in proofs):
            return ClosedWorldExistsResult("abstain", None, (), (),
                                           "existence proof failed final revalidation")
        return ClosedWorldExistsResult(
            "resolved", bool(active), tuple(item.fact_id for item in active) + (seal.fact_id,),
            proofs, "positive witness plus completeness seal" if active
            else "verified closed-world absence")
