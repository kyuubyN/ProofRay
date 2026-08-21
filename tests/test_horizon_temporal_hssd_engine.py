# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import hashlib
import json

from horizon_memory.causal_adapter_protocol import CausalAdapterBatch
from horizon_memory.hssd_query_compiler import HSSDAddressAtoms, HSSDQueryPlan
from horizon_memory.json_causal_adapter import JsonCausalMapping, JsonPointerCausalAdapter
from horizon_memory.proof_pressure_search import HorizonSearchEngine
from horizon_memory.raw_causal_channels import RawCausalDocument, observe_raw_text
from horizon_memory.standalone_causal_memory import StandaloneCausalMemory
from horizon_memory.strict_hssd_query_compiler import StrictStructuralHSSDQueryCompiler
from horizon_memory.sufficient_statistic_search import HorizonSufficientStatisticSearch
from horizon_memory.temporal_hssd_engine import (
    StandaloneTemporalHSSDEngine, TemporalHSSDProgramCompiler,
)
from horizon_memory.typed_causal_program import TypedCausalFact
from horizon_memory.typed_hssd_adapter import TypedCausalHSSDEvidenceAdapter


def _fact(fid, predicate, value, event_time, **kwargs):
    source_id = kwargs.pop("source_id", f"source-{fid}")
    digest = hashlib.sha256(value.encode()).hexdigest()
    return TypedCausalFact(fid, "scope", kwargs.pop("subject", "John"), predicate, value,
                           kwargs.pop("observed_at", event_time), event_time, **kwargs,
                           source_id=source_id, source_sha256=digest,
                           source_span=(0, len(value)))


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


def test_compile_rejects_a_second_operand_with_zero_query_overlap():
    """A duration/interval program needs 2 operands; ranking can fill the second slot from
    whatever fiber sorts next even with zero query overlap (an unrelated support document).
    Checking only the first chosen fiber let that unrelated fiber become an operand and
    produce a numerically "resolved" but meaningless duration (2026-08-2x, found via code
    review)."""
    fact_a = _fact(1, "started", "value-a", 100, subject="Roman Empire")
    fact_b = _fact(2, "founded", "value-b", 50, subject="Apple")
    query_tokens = tuple(observe_raw_text("Roman Empire started").lexical)
    plan = HSSDQueryPlan(
        state="compiled", operation="duration", target="",
        address_atoms=HSSDAddressAtoms(lexical=query_tokens, entities=(), numbers=(),
                                       temporal=(), relations=()),
        obligations=(), require_complete=False, reason="")
    program = TemporalHSSDProgramCompiler().compile(plan, (fact_a, fact_b), (1, 2))
    assert program is None


def test_latest_resolves_past_a_retracted_report_tied_in_clock_from_a_different_orbit():
    """`_latest` used to compute one global max(version, observed_at) across every row for a
    fiber before checking consistency, ignoring event/orbit identity. Two distinct events
    (different `event_id`) that happen to tie on (version, observed_at) -- plausible for
    facts ingested in the same batch -- made a genuinely valid, current report look like an
    internally-conflicting one merely because an unrelated, retracted report tied its clock
    (2026-08-2x, found via code review)."""
    valid = _fact(1, "visited", "Paris", 100, subject="John", event_id="trip-paris",
                  observed_at=5, version=1, asserted=True, polarity=1)
    retracted = _fact(2, "visited", "London", 200, subject="John",
                      event_id="trip-london-retracted", observed_at=5, version=1,
                      asserted=False, polarity=1)
    engine = StandaloneTemporalHSSDEngine(None, None, (valid, retracted))
    assert engine._latest(("John", "visited")) == valid


def test_latest_still_abstains_when_two_valid_events_genuinely_tie():
    """Two distinct, individually-valid events that tie in clock are genuinely ambiguous for
    a fiber-level (not orbit-specific) query -- the fix above must not over-resolve this."""
    trip_a = _fact(1, "visited", "Paris", 100, subject="John", event_id="trip-paris",
                   observed_at=5, version=1, asserted=True, polarity=1)
    trip_b = _fact(2, "visited", "London", 200, subject="John", event_id="trip-london",
                   observed_at=5, version=1, asserted=True, polarity=1)
    engine = StandaloneTemporalHSSDEngine(None, None, (trip_a, trip_b))
    assert engine._latest(("John", "visited")) is None
