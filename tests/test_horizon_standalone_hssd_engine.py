# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory import (
    CausalAdapterBatch,
    JsonCausalMapping,
    JsonPointerCausalAdapter,
    StandaloneCausalMemory,
)
from horizon_memory.proof_pressure_search import HorizonSearchEngine
from horizon_memory.raw_causal_channels import RawCausalDocument
from horizon_memory.standalone_hssd_engine import StandaloneHSSDEngine
from horizon_memory.sufficient_statistic_search import HorizonSufficientStatisticSearch
from horizon_memory.typed_hssd_adapter import TypedCausalHSSDEvidenceAdapter


def _system(content, mappings, docs, *, roles=(), complete=()):
    batch = CausalAdapterBatch("source", content, "scope", mappings)
    adapter = JsonPointerCausalAdapter()
    facts = adapter.compile_batch(batch)
    memory = StandaloneCausalMemory("scope")
    assert memory.ingest(adapter, batch).state == "APPLIED"
    evidence = TypedCausalHSSDEvidenceAdapter(
        "typed-memory-v1", facts, role_schema=roles, complete_fibers=complete,
        proof_verifier=memory.verify_proof)
    search = HorizonSufficientStatisticSearch(HorizonSearchEngine(docs), evidence)
    return StandaloneHSSDEngine(search, memory, facts)


def test_json_to_search_to_hssd_to_lookup_executor_to_proof():
    content = '{"state":{"value":"ready","unit":"code"}}'
    system = _system(content, (JsonCausalMapping(
        1, "/state/value", "engine", "state", 1, 1, "/state/unit"),),
        (RawCausalDocument(1, "engine state ready", 0, 0),),
        roles=(("state", ("value",)),))
    result = system.query("What state does the engine have?")
    assert (result.state, result.value, result.unit) == ("resolved", "ready", "code")
    assert result.fact_ids == (1,)
    assert result.causal_result and result.causal_result.proofs


def test_json_to_closed_world_count_is_exact_and_provenance_bound():
    content = '{"items":["alpha","beta"]}'
    mappings = (JsonCausalMapping(1, "/items/0", "Mina", "telescope", 1, 1),
                JsonCausalMapping(2, "/items/1", "Mina", "telescope", 2, 2))
    docs = (RawCausalDocument(1, "Mina repaired telescope alpha", 0, 0),
            RawCausalDocument(2, "Mina repaired telescope beta", 0, 1))
    system = _system(content, mappings, docs,
                     complete=(("Mina", "telescope"),))
    result = system.query("How many telescopes did Mina repair?")
    assert (result.state, result.value, result.unit) == ("resolved", "2", "count")
    assert set(result.fact_ids) == {1, 2}


def test_json_to_closed_world_sum_conserves_unit():
    content = '{"costs":[{"value":7,"unit":"dollars"},{"value":5,"unit":"dollars"}]}'
    mappings = (JsonCausalMapping(1, "/costs/0/value", "mission", "cost", 1, 1,
                                  "/costs/0/unit", event_id="c1"),
                JsonCausalMapping(2, "/costs/1/value", "mission", "cost", 2, 2,
                                  "/costs/1/unit", event_id="c2"))
    docs = (RawCausalDocument(1, "mission cost 7 dollars", 0, 0),
            RawCausalDocument(2, "mission cost 5 dollars", 0, 1))
    system = _system(content, mappings, docs,
                     complete=(("mission", "cost"),))
    result = system.query("What was the total mission cost in dollars?")
    assert (result.state, result.value, result.unit) == ("resolved", "12", "dollars")


def test_structurally_known_but_executor_unsupported_operation_stays_unsupported():
    content = '{"launch":"Tuesday"}'
    system = _system(content, (JsonCausalMapping(
        1, "/launch", "Mina", "launch", 1, 1),),
        (RawCausalDocument(1, "Mina launch Tuesday", 0, 0),))
    result = system.query("When did Mina launch?")
    assert result.state == "unsupported"
    assert result.causal_result is None
