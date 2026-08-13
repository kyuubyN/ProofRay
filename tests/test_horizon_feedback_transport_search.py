# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.feedback_transport_search import (
    ConservativeFeedbackTransportHorizonSearchEngine,
    FeedbackTransportHorizonSearchEngine,
    ParetoTailFeedbackHorizonSearchEngine,
)
from horizon_memory.raw_causal_channels import RawCausalDocument


DOCS = (
    RawCausalDocument(1, "cardiac injury biomarker troponin cohort", 1, 0),
    RawCausalDocument(2, "cardiac injury biomarker mortality cohort", 2, 0),
    RawCausalDocument(3, "troponin mortality prognosis after infarction", 3, 0),
    RawCausalDocument(4, "unrelated botanical growth experiment", 4, 0),
)


def test_feedback_is_label_free_deterministic_and_only_adds_a_surface():
    engine = FeedbackTransportHorizonSearchEngine(
        DOCS, feedback_documents=2, feedback_terms=4, minimum_support=2)
    modes, witnesses = engine._surfaces("cardiac injury biomarker")
    assert "feedback" in dict(modes)
    assert engine.feedback_terms_by_query["cardiac injury biomarker"] == ("cohort",)
    assert witnesses and all(not values for values in witnesses.values())
    first = engine.search("cardiac injury biomarker", max_results=4, exploration_reserve=4)
    second = engine.search("cardiac injury biomarker", max_results=4, exploration_reserve=4)
    assert first == second


def test_feedback_terms_never_change_original_query_obligations():
    engine = FeedbackTransportHorizonSearchEngine(
        DOCS, feedback_documents=2, feedback_terms=4, minimum_support=2)
    result = engine.search("cardiac injury biomarker", max_results=4, exploration_reserve=4)
    keys = {item.key for item in result.obligations}
    assert "lexical:cohort" not in keys
    assert keys == {"lexical:biomarker", "lexical:cardiac", "lexical:injury",
                    "relation:cardiac>injury", "relation:injury>biomarker"}


def test_conservative_feedback_preserves_incumbent_prefix_and_budget():
    engine = ConservativeFeedbackTransportHorizonSearchEngine(
        DOCS, feedback_documents=2, feedback_terms=4, minimum_support=2,
        frontier_width=4)
    query = "cardiac injury biomarker"
    budget = sum(engine.byte_cost.values())
    incumbent = engine.incumbent.search(
        query, max_results=4, max_bytes=budget, exploration_reserve=2)
    result = engine.search(query, max_results=4, max_bytes=budget, exploration_reserve=2)
    assert result.fact_ids[:len(incumbent.fact_ids)] == incumbent.fact_ids
    assert result.bytes_selected <= budget
    assert result.obligations == incumbent.obligations
    assert result.residual == incumbent.residual


def test_pareto_tail_protects_declared_prefix_and_recomputes_closure():
    engine = ParetoTailFeedbackHorizonSearchEngine(
        DOCS, protected_width=2, feedback_rrf_weight=1.0,
        feedback_documents=2, feedback_terms=4, minimum_support=2,
        frontier_width=4)
    query = "cardiac injury biomarker"
    budget = sum(engine.byte_cost.values())
    incumbent = engine.incumbent.search(
        query, max_results=4, max_bytes=budget, exploration_reserve=4)
    result = engine.search(query, max_results=4, max_bytes=budget, exploration_reserve=4)
    assert result.fact_ids[:2] == incumbent.fact_ids[:2]
    assert result.bytes_selected <= budget
    assert result.residual == result.admissions[-1].residual_after
    assert result.proof_closed == (not result.residual)
    assert result == engine.search(
        query, max_results=4, max_bytes=budget, exploration_reserve=4)
