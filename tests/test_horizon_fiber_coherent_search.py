# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.causal_adapter_protocol import CausalAdapterBatch
from horizon_memory.fiber_coherent_search import (
    FiberCoherentSufficientStatisticSearch,
)
from horizon_memory.json_causal_adapter import (
    JsonCausalMapping, JsonPointerCausalAdapter,
)
from horizon_memory.proof_pressure_search import HorizonSearchEngine
from horizon_memory.raw_causal_channels import RawCausalDocument
from horizon_memory.standalone_causal_memory import StandaloneCausalMemory
from horizon_memory.typed_hssd_adapter import TypedCausalHSSDEvidenceAdapter


def _field(include_target_file=True):
    content = '{"author":"Ada","wrong":"noise","right":"target.txt"}'
    mappings = (
        JsonCausalMapping(1, "/author", "CommitTARGET", "author", 1, 1),
        JsonCausalMapping(2, "/wrong", "CommitWRONG", "changed_file", 1, 1),
    ) + ((JsonCausalMapping(3, "/right", "CommitTARGET", "changed_file", 1, 1),)
         if include_target_file else ())
    batch = CausalAdapterBatch("git:test", content, "scope", mappings)
    adapter = JsonPointerCausalAdapter()
    facts = adapter.compile_batch(batch)
    memory = StandaloneCausalMemory("scope")
    assert memory.ingest(adapter, batch).state == "APPLIED"
    docs = (
        RawCausalDocument(1, "CommitTARGET author Ada", 0, 0),
        RawCausalDocument(2, "CommitWRONG changed file target", 0, 1),
    ) + ((RawCausalDocument(3, "CommitTARGET changed file target", 0, 2),)
         if include_target_file else ())
    evidence = TypedCausalHSSDEvidenceAdapter(
        "fiber-test", facts,
        complete_fibers=(("CommitTARGET", "changed_file"),
                         ("CommitWRONG", "changed_file")),
        proof_verifier=memory.verify_proof)
    return FiberCoherentSufficientStatisticSearch(
        HorizonSearchEngine(docs), evidence, facts)


def test_unrelated_entity_and_complete_fiber_cannot_compose():
    result = _field(False).search(
        "How many changed files did CommitTARGET record?", max_results=3)
    assert result.state == "incomplete"
    assert not result.closure.execution_ready


def test_matching_complete_fiber_closes_without_foreign_fact():
    result = _field(True).search(
        "How many changed files did CommitTARGET record?", max_results=3)
    assert result.state == "ready"
    assert result.fact_ids == (3,)
