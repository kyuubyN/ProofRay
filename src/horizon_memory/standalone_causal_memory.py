# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adapter-neutral standalone facade over the verified typed causal boundary."""
from __future__ import annotations

from dataclasses import dataclass

from .causal_adapter_protocol import CausalAdapterBatch, CausalIngestAdapter
from .typed_causal_ingest import CausalSourceEnvelope, DeterministicCausalCompiler
from .typed_causal_program import (
    TypedCausalExecutor, TypedCausalFact, TypedCausalProgram, TypedCausalResult,
)


@dataclass(frozen=True)
class CausalIngestReceipt:
    state: str
    adapter_id: str
    fact_ids: tuple[int, ...]
    source_sha256: str
    reason: str


class StandaloneCausalMemory:
    """Own truth/proof semantics while adapters remain fully removable.

    The current implementation is an in-memory causal boundary.  Batches are compiled
    and committed atomically; the query index is rebuilt on ingest, never per query.
    Durable typed-fact publication remains a separate gate from the already validated
    u8 Horizon store.
    """

    def __init__(self, scope: str):
        if not scope:
            raise ValueError("standalone causal memory needs one explicit scope")
        self.scope = scope
        self._facts: dict[int, TypedCausalFact] = {}
        self._sources: dict[str, CausalSourceEnvelope] = {}
        self._executor: TypedCausalExecutor | None = None

    def ingest(self, adapter: CausalIngestAdapter,
               batch: CausalAdapterBatch) -> CausalIngestReceipt:
        adapter_id = getattr(adapter, "adapter_id", "")
        if not adapter_id or not isinstance(adapter, CausalIngestAdapter):
            raise TypeError("adapter must implement the causal ingest protocol")
        if batch.scope != self.scope:
            return CausalIngestReceipt("REJECTED_SCOPE", adapter_id, (), batch.source_sha256,
                                       "batch scope differs from memory scope")
        try:
            source = CausalSourceEnvelope.seal(batch.source_id, batch.content)
            proposed = adapter.compile_batch(batch)
        except (TypeError, ValueError) as error:
            return CausalIngestReceipt("REJECTED_ADAPTER", adapter_id, (), batch.source_sha256,
                                       str(error))
        if not proposed or tuple(fact.fact_id for fact in proposed) != \
                tuple(sorted({fact.fact_id for fact in proposed})):
            return CausalIngestReceipt("REJECTED_ADAPTER", adapter_id, (), source.sha256,
                                       "adapter output must be non-empty and FactId-canonical")
        if any(fact.scope != self.scope or fact.source_id != source.source_id or
               fact.source_sha256 != source.sha256 for fact in proposed):
            return CausalIngestReceipt("REJECTED_AUTHORITY", adapter_id, (), source.sha256,
                                       "adapter cannot forge scope or source authority")
        if any(not DeterministicCausalCompiler.verify(fact, source) for fact in proposed):
            return CausalIngestReceipt("REJECTED_PROOF", adapter_id, (), source.sha256,
                                       "one or more proposed microcitations do not reopen")
        existing_source = self._sources.get(source.source_id)
        if existing_source is not None and existing_source.sha256 != source.sha256:
            return CausalIngestReceipt("REJECTED_SOURCE_COLLISION", adapter_id, (), source.sha256,
                                       "source identity is immutable across batches")
        collisions = tuple(fact.fact_id for fact in proposed
                           if fact.fact_id in self._facts and self._facts[fact.fact_id] != fact)
        if collisions:
            return CausalIngestReceipt("REJECTED_COLLISION", adapter_id, collisions, source.sha256,
                                       "FactId collision is not an update")
        novel = tuple(fact for fact in proposed if fact.fact_id not in self._facts)
        if not novel:
            return CausalIngestReceipt("IDEMPOTENT", adapter_id,
                                       tuple(fact.fact_id for fact in proposed), source.sha256,
                                       "identical batch already committed")
        prospective = {**self._facts, **{fact.fact_id: fact for fact in novel}}
        known = set(prospective)
        missing = tuple(sorted({cause for fact in novel for cause in fact.causes if cause not in known}))
        if missing:
            return CausalIngestReceipt("REJECTED_CAUSAL", adapter_id, missing, source.sha256,
                                       "causal edge references an unknown FactId")
        # Construct before publication: failure cannot leave a partial batch.
        executor = TypedCausalExecutor(tuple(sorted(prospective.values())), self.scope)
        self._facts = prospective
        self._sources[source.source_id] = source
        self._executor = executor
        return CausalIngestReceipt("APPLIED", adapter_id,
                                   tuple(fact.fact_id for fact in novel), source.sha256,
                                   "batch atomically committed and query index published")

    def query(self, program: TypedCausalProgram) -> TypedCausalResult:
        if self._executor is None:
            return TypedCausalResult("abstain", None, "", (), "empty_causal_memory")
        result = self._executor.execute(program)
        if any(not DeterministicCausalCompiler.verify(
                self._executor.by_id[proof.fact_id], self._sources[proof.source_id])
               for proof in result.proofs):
            return TypedCausalResult("abstain", None, "", (),
                                     "published_proof_failed_revalidation",
                                     examined_fact_ids=result.examined_fact_ids)
        return result

    @property
    def fact_count(self) -> int:
        return len(self._facts)

    def verify_proof(self, proof) -> bool:
        if self._executor is None or proof.fact_id not in self._executor.by_id:
            return False
        source = self._sources.get(proof.source_id)
        return bool(source and DeterministicCausalCompiler.verify(
            self._executor.by_id[proof.fact_id], source))
