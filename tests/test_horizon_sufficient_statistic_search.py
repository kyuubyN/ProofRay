# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.hssd_query_compiler import HSSDEvidenceObservation
from horizon_memory.proof_pressure_search import HorizonSearchEngine
from horizon_memory.raw_causal_channels import RawCausalDocument
from horizon_memory.sufficient_statistic_search import (
    HorizonSufficientStatisticSearch,
    MappingHSSDEvidenceAdapter,
)


def _engine():
    return HorizonSearchEngine((
        RawCausalDocument(1, "Mina launched the expedition on Tuesday", 0, 0, "Mina"),
        RawCausalDocument(2, "Mina repaired telescope alpha", 0, 1, "Mina"),
        RawCausalDocument(3, "Mina repaired telescope beta", 0, 2, "Mina"),
        RawCausalDocument(4, "Unrelated cooking note", 1, 0, "Kai"),
    ))


def test_first_verified_prefix_becomes_a_minimal_sufficient_statistic():
    adapter = MappingHSSDEvidenceAdapter("typed-test-v1", (
        HSSDEvidenceObservation(1, lexical=("launch", "expedition"),
                                entities=("Mina",), clocks=("event_time",),
                                proof_verified=True),
    ))
    pack = HorizonSufficientStatisticSearch(_engine(), adapter).search(
        "When did Mina launch the expedition?")
    assert pack.state == "ready"
    assert pack.fact_ids == (1,)
    assert pack.closure.execution_ready
    assert pack.evidence_bytes == len(_engine().by_id[1].text.encode())


def test_retrieval_score_cannot_replace_missing_closed_world_authority():
    adapter = MappingHSSDEvidenceAdapter("typed-test-v1", (
        HSSDEvidenceObservation(2, lexical=("repair", "telescope"),
                                entities=("Mina",), distinct_keys=("alpha",),
                                proof_verified=True),
        HSSDEvidenceObservation(3, lexical=("repair", "telescope"),
                                entities=("Mina",), distinct_keys=("beta",),
                                proof_verified=True),
    ))
    pack = HorizonSufficientStatisticSearch(_engine(), adapter).search(
        "How many telescopes did Mina repair?")
    assert pack.state == "incomplete"
    assert "proof:complete" in pack.closure.residual


def test_complete_certificate_closes_count_across_multiple_factids():
    adapter = MappingHSSDEvidenceAdapter("typed-test-v1", (
        HSSDEvidenceObservation(2, lexical=("repair", "telescope"),
                                entities=("Mina",), distinct_keys=("alpha",),
                                proof_verified=True),
        HSSDEvidenceObservation(3, lexical=("repair", "telescope"),
                                entities=("Mina",), distinct_keys=("beta",),
                                proof_verified=True, complete=True),
    ))
    pack = HorizonSufficientStatisticSearch(_engine(), adapter).search(
        "How many telescopes did Mina repair?")
    assert pack.state == "ready"
    assert set(pack.fact_ids) == {2, 3}


def test_unverified_observation_cannot_cross_the_horizon():
    adapter = MappingHSSDEvidenceAdapter("typed-test-v1", (
        HSSDEvidenceObservation(1, lexical=("launch",), entities=("Mina",),
                                clocks=("event_time",), proof_verified=False),
    ))
    pack = HorizonSufficientStatisticSearch(_engine(), adapter).search(
        "When did Mina launch the expedition?")
    assert pack.state == "incomplete"
    assert "proof:identity" in pack.closure.residual


def test_hard_conflict_stops_without_consulting_later_candidates():
    adapter = MappingHSSDEvidenceAdapter("typed-test-v1", (
        HSSDEvidenceObservation(1, lexical=("launch",), entities=("Mina",),
                                clocks=("event_time",), proof_verified=True, conflict=True),
    ))
    pack = HorizonSufficientStatisticSearch(_engine(), adapter).search(
        "When did Mina launch the expedition?")
    assert pack.state == "conflict"
    assert pack.examined_fact_ids == (1,)
