# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Expose verified TypedCausalFacts as HSSD evidence observations."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Callable

from .hssd_query_compiler import HSSDEvidenceObservation
from .raw_causal_channels import observe_raw_text
from .typed_causal_program import TypedCausalFact, TypedCausalProof


@dataclass(frozen=True)
class TypedCausalHSSDEvidenceAdapter:
    """Adapter from the exact causal field to query-independent HSSD charges.

    Role schemas and completeness are authoritative inputs.  They cannot be inferred
    from retrieval scores or from absence in the fact set.
    """

    adapter_id: str
    facts: tuple[TypedCausalFact, ...]
    role_schema: tuple[tuple[str, tuple[str, ...]], ...] = ()
    complete_fibers: tuple[tuple[str, str], ...] = ()
    proof_verifier: Callable[[TypedCausalProof], bool] | None = None

    def __post_init__(self) -> None:
        if not self.adapter_id or not self.facts:
            raise ValueError("adapter id and typed causal facts are required")
        if tuple(item.fact_id for item in self.facts) != tuple(
                sorted({item.fact_id for item in self.facts})):
            raise ValueError("typed HSSD facts must be FactId-canonical")
        if self.role_schema != tuple(sorted(self.role_schema)) or len(dict(self.role_schema)) != len(
                self.role_schema):
            raise ValueError("role schema must be predicate-canonical")
        if self.complete_fibers != tuple(sorted(set(self.complete_fibers))):
            raise ValueError("complete fibers must be canonical")
        object.__setattr__(self, "_by_id", {item.fact_id: item for item in self.facts})

    @staticmethod
    def _is_quantity(value: str) -> bool:
        try:
            Decimal(value)
            return True
        except InvalidOperation:
            return False

    def observe(self, fact_id: int) -> HSSDEvidenceObservation | None:
        fact = self._by_id.get(fact_id)
        if fact is None:
            return None
        proof = TypedCausalProof(fact.fact_id, fact.source_id, fact.source_sha256,
                                 fact.source_span)
        verified = bool(self.proof_verifier and self.proof_verifier(proof))
        channels = observe_raw_text(f"{fact.subject} {fact.predicate} {fact.value}")
        roles = dict(self.role_schema).get(fact.predicate, ())
        signatures = {
            (item.value, item.unit, item.polarity, item.asserted, item.event_time, item.causes)
            for item in self.facts
            if item.orbit == fact.orbit and item.version == fact.version
            and item.observed_at == fact.observed_at
        }
        return HSSDEvidenceObservation(
            fact_id=fact.fact_id,
            lexical=channels.lexical,
            entities=tuple(sorted({fact.subject, fact.value})),
            roles=roles,
            clocks=("event_time", "observed_at"),
            quantities=(fact.predicate,) if self._is_quantity(fact.value) else (),
            units=(fact.unit,) if fact.unit else (),
            causal_edges=len(fact.causes),
            distinct_keys=(fact.value,),
            proof_verified=verified,
            complete=(fact.subject, fact.predicate) in self.complete_fibers,
            conflict=len(signatures) > 1,
        )
