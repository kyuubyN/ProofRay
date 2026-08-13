# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.materialized_proof_pressure_search import (
    MaterializedIndependentHorizonSearchEngine,
    MaterializedRawCausalSyndromeIndex,
)
from horizon_memory.raw_causal_channels import (
    RawCausalDocument, RawCausalSyndromeIndex,
)


DOCS = (
    RawCausalDocument(1, "Alpha kinase reduces beta inflammation in mice.", 1, 0),
    RawCausalDocument(2, "Gamma treatment increases cellular growth by 20 percent.", 2, 0),
    RawCausalDocument(3, "No alpha effect was observed in the clinical cohort.", 3, 0),
)


def test_materialized_components_and_rank_are_exactly_legacy_equivalent():
    legacy = RawCausalSyndromeIndex(DOCS)
    materialized = MaterializedRawCausalSyndromeIndex(DOCS)
    for query in ("What reduces beta inflammation?", "Did alpha increase growth by 20 percent?"):
        left, right = legacy.components(query), materialized.components(query)
        assert left == right
        weights = (1.0, .25, .5, .2, .25, 1.0)
        assert legacy.rank(left, weights) == materialized.rank(right, weights)


def test_independent_engine_never_invents_adjacency_witnesses():
    engine = MaterializedIndependentHorizonSearchEngine(DOCS)
    result = engine.search("What reduces beta inflammation?", max_results=3,
                           exploration_reserve=3)
    assert result.fact_ids[0] == 1
    assert all(not admission.witness_fact_ids for admission in result.admissions)
