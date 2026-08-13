# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
import hashlib

from horizon_memory.typed_causal_program import (
    CausalSelector,
    TypedCausalExecutor,
    TypedCausalFact,
    TypedCausalProgram,
)


def _fact(fid, predicate, value, event_time, **kwargs):
    source_id = kwargs.pop("source_id", f"source-{fid}")
    digest = hashlib.sha256(value.encode()).hexdigest()
    return TypedCausalFact(fid, "scope", kwargs.pop("subject", "Ada"), predicate, value,
                           kwargs.pop("observed_at", event_time), event_time, **kwargs,
                           source_id=source_id, source_sha256=digest,
                           source_span=(0, len(value)))


def test_latest_state_is_invariant_to_fact_identity_ordering():
    facts = (_fact(10, "status", "draft", 1), _fact(20, "status", "released", 2))
    result = TypedCausalExecutor(facts, "scope").execute(
        TypedCausalProgram("LOOKUP", CausalSelector("Ada", "status")))
    assert (result.state, result.value, result.fact_ids) == ("resolved", "released", (20,))
    assert result.examined_fact_ids == (20,)
    assert result.proofs[0].source_sha256 == hashlib.sha256(b"released").hexdigest()


def test_future_fact_does_not_cross_the_query_light_cone():
    facts = (_fact(1, "status", "draft", 1),
             _fact(2, "status", "released", 9, observed_at=9))
    result = TypedCausalExecutor(facts, "scope").execute(
        TypedCausalProgram("LOOKUP", CausalSelector("Ada", "status"), at_time=5))
    assert result.value == "draft"


def test_conflicting_latest_orbit_abstains():
    facts = (_fact(1, "status", "draft", 1, event_id="release", version=2),
             _fact(2, "status", "released", 1, event_id="release", version=2))
    result = TypedCausalExecutor(facts, "scope").execute(
        TypedCausalProgram("LOOKUP", CausalSelector("Ada", "status")))
    assert (result.state, result.reason) == ("abstain", "conflicting_latest_orbit")


def test_closed_world_is_required_for_aggregation():
    executor = TypedCausalExecutor((_fact(1, "cost", "4", 1, unit="USD"),), "scope")
    result = executor.execute(TypedCausalProgram(
        "SUM", CausalSelector("Ada", "cost"), unit="USD"))
    assert (result.state, result.reason) == ("unsupported", "closed_world_required")


def test_duplicate_reports_share_one_event_orbit_and_do_not_double_sum():
    facts = (_fact(1, "cost", "4", 1, unit="USD", event_id="purchase-a"),
             _fact(2, "cost", "4", 1, unit="USD", event_id="purchase-a"),
             _fact(3, "cost", "6", 2, unit="USD", event_id="purchase-b"))
    result = TypedCausalExecutor(facts, "scope").execute(TypedCausalProgram(
        "SUM", CausalSelector("Ada", "cost"), unit="USD", closed_world=True))
    assert (result.state, result.value, result.fact_ids) == ("resolved", "10", (1, 3))


def test_unit_mismatch_is_a_hard_gate():
    facts = (_fact(1, "distance", "4", 1, unit="mile"),
             _fact(2, "distance", "6", 2, unit="kilometer"))
    result = TypedCausalExecutor(facts, "scope").execute(TypedCausalProgram(
        "SUM", CausalSelector("Ada", "distance"), unit="mile", closed_world=True))
    assert (result.state, result.reason) == ("abstain", "unit_not_conserved")


def test_count_distinct_collapses_duplicate_values():
    facts = (_fact(1, "visited", "museum-a", 1, event_id="visit-a"),
             _fact(2, "visited", "museum-a", 2, event_id="visit-b"),
             _fact(3, "visited", "museum-b", 3, event_id="visit-c"))
    result = TypedCausalExecutor(facts, "scope").execute(TypedCausalProgram(
        "COUNT_DISTINCT", CausalSelector("Ada", "visited"), closed_world=True))
    assert (result.value, result.unit) == ("2", "count")


def test_exact_interval_uses_causal_clocks_and_both_provenances():
    facts = (_fact(1, "started", "project", 10), _fact(2, "ended", "project", 17))
    result = TypedCausalExecutor(facts, "scope").execute(TypedCausalProgram(
        "INTERVAL", CausalSelector("Ada", "started"),
        (CausalSelector("Ada", "ended"),), unit="day"))
    assert (result.value, result.unit, result.fact_ids) == ("7", "day", (1, 2))


def test_explicit_cause_returns_effect_and_cause_provenance():
    facts = (_fact(1, "motivation", "protect the habitat", 1),
             _fact(2, "action", "joined the project", 2, causes=(1,)))
    result = TypedCausalExecutor(facts, "scope").execute(TypedCausalProgram(
        "EXPLAIN_CAUSE", CausalSelector("Ada", "action")))
    assert result.state == "resolved"
    assert result.value == "protect the habitat"
    assert result.fact_ids == (2, 1)


def test_unknown_cause_and_uncertain_evidence_fail_closed():
    with pytest.raises(ValueError, match="causal edge"):
        TypedCausalExecutor((_fact(2, "action", "joined", 2, causes=(99,)),), "scope")
    result = TypedCausalExecutor((_fact(1, "status", "maybe", 1, asserted=False),),
                                 "scope").execute(
        TypedCausalProgram("LOOKUP", CausalSelector("Ada", "status")))
    assert (result.state, result.reason) == ("abstain", "uncertain_fact")


def test_lookup_budget_is_independent_of_unrelated_history_and_old_versions():
    facts = []
    for index in range(2_000):
        subject = "Ada" if index < 1_000 else f"other-{index}"
        facts.append(_fact(index + 1, "status", f"state-{index}", index + 1,
                           subject=subject, event_id="ada-state" if subject == "Ada"
                           else f"state-{index}"))
    result = TypedCausalExecutor(tuple(facts), "scope").execute(
        TypedCausalProgram("LOOKUP", CausalSelector("Ada", "status")))
    assert result.value == "state-999"
    assert result.examined_fact_ids == (1000,)
