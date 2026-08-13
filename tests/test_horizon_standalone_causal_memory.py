# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory import (
    CausalAdapterBatch, CausalSelector, JsonCausalMapping, JsonPointerCausalAdapter,
    StandaloneCausalMemory, TypedCausalProgram,
)


def _batch(fid=1, content='{"state":{"value":7,"unit":"code"}}', scope="scope",
           event_id="state", causes=(), source_id="source"):
    return CausalAdapterBatch(source_id, content, scope, (JsonCausalMapping(
        fid, "/state/value", "engine", "state", fid, fid, "/state/unit",
        version=fid, event_id=event_id, causes=causes),))


def test_any_conforming_adapter_can_publish_and_query_a_provenance_bound_fact():
    memory = StandaloneCausalMemory("scope")
    receipt = memory.ingest(JsonPointerCausalAdapter(), _batch())
    result = memory.query(TypedCausalProgram(
        "LOOKUP", CausalSelector("engine", "state")))
    assert receipt.state == "APPLIED"
    assert (result.state, result.value, result.unit) == ("resolved", "7", "code")
    assert result.proofs[0].source_id == "source"


def test_identical_retry_is_idempotent_but_factid_collision_fails_closed():
    memory = StandaloneCausalMemory("scope")
    assert memory.ingest(JsonPointerCausalAdapter(), _batch()).state == "APPLIED"
    assert memory.ingest(JsonPointerCausalAdapter(), _batch()).state == "IDEMPOTENT"
    collision = _batch(content='{"state":{"value":8,"unit":"code"}}', source_id="source-2")
    assert memory.ingest(JsonPointerCausalAdapter(), collision).state == "REJECTED_COLLISION"
    assert memory.fact_count == 1


def test_source_identity_cannot_be_rebound_to_different_bytes():
    memory = StandaloneCausalMemory("scope")
    assert memory.ingest(JsonPointerCausalAdapter(), _batch()).state == "APPLIED"
    rebound = _batch(fid=2, content='{"state":{"value":8,"unit":"code"}}')
    assert memory.ingest(JsonPointerCausalAdapter(), rebound).state == \
        "REJECTED_SOURCE_COLLISION"
    result = memory.query(TypedCausalProgram("LOOKUP", CausalSelector("engine", "state")))
    assert result.value == "7"
    assert memory.verify_proof(result.proofs[0])


def test_scope_and_missing_cause_rejections_are_atomic():
    memory = StandaloneCausalMemory("scope")
    assert memory.ingest(JsonPointerCausalAdapter(), _batch(scope="other")).state == \
        "REJECTED_SCOPE"
    assert memory.ingest(JsonPointerCausalAdapter(), _batch(causes=(99,))).state == \
        "REJECTED_CAUSAL"
    assert memory.fact_count == 0


def test_cause_can_cross_batches_only_after_authoritative_fact_exists():
    memory = StandaloneCausalMemory("scope")
    cause = _batch(fid=1, content='{"state":{"value":7,"unit":"code"}}', event_id="cause")
    effect = CausalAdapterBatch("effect-source", '{"state":{"value":8,"unit":"code"}}',
                                "scope", (JsonCausalMapping(
                                    2, "/state/value", "engine", "effect", 2, 2,
                                    "/state/unit", version=2, event_id="effect", causes=(1,)),))
    assert memory.ingest(JsonPointerCausalAdapter(), cause).state == "APPLIED"
    assert memory.ingest(JsonPointerCausalAdapter(), effect).state == "APPLIED"
    result = memory.query(TypedCausalProgram(
        "EXPLAIN_CAUSE", CausalSelector("engine", "effect")))
    assert result.value == "7"
    assert result.fact_ids == (2, 1)


def test_query_on_empty_memory_abstains_explicitly():
    result = StandaloneCausalMemory("scope").query(TypedCausalProgram(
        "LOOKUP", CausalSelector("engine", "state")))
    assert (result.state, result.reason) == ("abstain", "empty_causal_memory")
