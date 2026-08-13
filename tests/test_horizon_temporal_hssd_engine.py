# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import json

from horizon_memory.causal_adapter_protocol import CausalAdapterBatch
from horizon_memory.json_causal_adapter import JsonCausalMapping, JsonPointerCausalAdapter
from horizon_memory.proof_pressure_search import HorizonSearchEngine
from horizon_memory.raw_causal_channels import RawCausalDocument
from horizon_memory.standalone_causal_memory import StandaloneCausalMemory
from horizon_memory.strict_hssd_query_compiler import StrictStructuralHSSDQueryCompiler
from horizon_memory.sufficient_statistic_search import HorizonSufficientStatisticSearch
from horizon_memory.temporal_hssd_engine import StandaloneTemporalHSSDEngine
from horizon_memory.typed_hssd_adapter import TypedCausalHSSDEvidenceAdapter


def _system():
    content = json.dumps({"events": ["started", "finished"]}, separators=(",", ":"))
    mappings = (JsonCausalMapping(1, "/events/0", "mission", "start", 1, 10),
                JsonCausalMapping(2, "/events/1", "mission", "finish", 2, 37))
    batch = CausalAdapterBatch("temporal-source", content, "scope", mappings)
    adapter = JsonPointerCausalAdapter(); facts = adapter.compile_batch(batch)
    memory = StandaloneCausalMemory("scope")
    assert memory.ingest(adapter, batch).state == "APPLIED"
    evidence = TypedCausalHSSDEvidenceAdapter(
        "temporal-v1", facts, proof_verifier=memory.verify_proof)
    documents = (RawCausalDocument(1, "mission start", 0, 0),
                 RawCausalDocument(2, "mission finish", 0, 1))
    search = HorizonSufficientStatisticSearch(
        HorizonSearchEngine(documents), evidence, StrictStructuralHSSDQueryCompiler())
    return StandaloneTemporalHSSDEngine(search, memory, facts)


def test_one_fact_with_two_clock_labels_does_not_fake_two_temporal_operands():
    system = _system()
    pack = system.search.search("How long did the mission last?", max_results=1)
    assert pack.state == "incomplete"
    assert "slot:clock_pair" in pack.closure.residual


def test_duration_uses_two_independent_verified_factids():
    result = _system().query("How long did the mission last?")
    assert (result.state, result.value, result.unit) == ("resolved", "27", "tick")
    assert set(result.fact_ids) == {1, 2}
    assert len(result.proofs) == 2


def test_interval_has_the_same_exact_clock_algebra():
    result = _system().query("What was the time between mission start and finish?")
    assert (result.state, result.value) == ("resolved", "27")


def test_lookup_time_projects_event_clock_not_surface_value():
    result = _system().query("When did the mission start?")
    assert (result.state, result.value, result.unit) == ("resolved", "10", "tick")
