# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import hashlib

from horizon_memory import (
    CausalAdapterBatch,
    JsonCausalMapping,
    JsonPointerCausalAdapter,
    StandaloneCausalMemory,
)
from horizon_memory.proof_pressure_search import HorizonSearchEngine
from horizon_memory.raw_causal_channels import RawCausalDocument
from horizon_memory.sufficient_statistic_search import HorizonSufficientStatisticSearch
from horizon_memory.typed_hssd_adapter import TypedCausalHSSDEvidenceAdapter


def _real_boundary():
    content = '{"state":{"value":7,"unit":"code"}}'
    batch = CausalAdapterBatch("source", content, "scope", (JsonCausalMapping(
        1, "/state/value", "engine", "state", 1, 1, "/state/unit",
        event_id="engine-state"),))
    ingest_adapter = JsonPointerCausalAdapter()
    facts = ingest_adapter.compile_batch(batch)
    memory = StandaloneCausalMemory("scope")
    assert memory.ingest(ingest_adapter, batch).state == "APPLIED"
    return content, facts, memory


def test_real_standalone_proof_crosses_into_hssd_without_reopening_raw_history():
    content, facts, memory = _real_boundary()
    adapter = TypedCausalHSSDEvidenceAdapter(
        "typed-memory-v1", facts,
        role_schema=(("state", ("value",)),),
        proof_verifier=memory.verify_proof)
    observation = adapter.observe(1)
    assert observation is not None and observation.proof_verified
    assert observation.quantities == ("state",)
    assert observation.units == ("code",)

    search = HorizonSufficientStatisticSearch(
        HorizonSearchEngine((RawCausalDocument(1, content, 0, 0, "engine"),)), adapter)
    pack = search.search("What state does the engine have?")
    assert pack.state == "ready"
    assert pack.fact_ids == (1,)


def test_completeness_cannot_be_inferred_from_having_all_current_rows():
    _, facts, memory = _real_boundary()
    open_adapter = TypedCausalHSSDEvidenceAdapter(
        "typed-memory-v1", facts, proof_verifier=memory.verify_proof)
    sealed_adapter = TypedCausalHSSDEvidenceAdapter(
        "typed-memory-v1", facts, complete_fibers=(("engine", "state"),),
        proof_verifier=memory.verify_proof)
    assert not open_adapter.observe(1).complete
    assert sealed_adapter.observe(1).complete


def test_missing_proof_verifier_fails_closed():
    _, facts, _ = _real_boundary()
    observation = TypedCausalHSSDEvidenceAdapter("typed-memory-v1", facts).observe(1)
    assert observation is not None and not observation.proof_verified
