# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Experimental retrieval surfaces.

These APIs are reproducible research components and may change independently
from the stable storage API. They remain offline and model-agnostic.
"""

from ..feedback_transport_search import (
    ConservativeFeedbackTransportHorizonSearchEngine,
    FeedbackTransportHorizonSearchEngine,
    ParetoTailFeedbackHorizonSearchEngine,
)
from ..materialized_proof_pressure_search import (
    MaterializedIndependentHorizonSearchEngine,
    MaterializedRawCausalSyndromeIndex,
)
from ..proof_pressure_search import (
    HorizonSearchEngine,
    ProofPressureResult,
    SearchAdmission,
    SearchObligation,
)
from ..supersession_collapse import (
    DEFAULT_RELEVANCE_FLOOR as SUPERSESSION_DEFAULT_RELEVANCE_FLOOR,
    SupersessionReport, collapse_evidence_items,
)
from ..pragmatic_negation import PragmaticNegationResult, detect_pragmatic_negation
from ..phonetic_pt import phi_pt
from ..portuguese_atomic_relations import (
    RoleReadResult, read as read_pt_atomic_relation,
    resolve_surface_role as resolve_pt_surface_role,
)
from ..narrative_composition import (
    AggregatedNarrative, ClassifiedPair, DiscourseFact, DiscourseRelation, NarrativeComponent,
    NarrativePlan, RealizedFact, RenderedNarrative, aggregate_same_subject_facts,
    build_discourse_facts, classify_relation, connector_style, current_value_fact,
    plan_narrative, realize_fact, render_narrative, render_pair,
)

__all__ = [
    "HorizonSearchEngine",
    "ProofPressureResult",
    "SearchAdmission",
    "SearchObligation",
    "MaterializedIndependentHorizonSearchEngine",
    "MaterializedRawCausalSyndromeIndex",
    "FeedbackTransportHorizonSearchEngine",
    "ConservativeFeedbackTransportHorizonSearchEngine",
    "ParetoTailFeedbackHorizonSearchEngine",
    "collapse_evidence_items",
    "SupersessionReport",
    "SUPERSESSION_DEFAULT_RELEVANCE_FLOOR",
    "detect_pragmatic_negation",
    "PragmaticNegationResult",
    "phi_pt",
    "RoleReadResult",
    "read_pt_atomic_relation",
    "resolve_pt_surface_role",
    "RealizedFact",
    "AggregatedNarrative",
    "aggregate_same_subject_facts",
    "realize_fact",
    "DiscourseFact",
    "DiscourseRelation",
    "ClassifiedPair",
    "classify_relation",
    "connector_style",
    "render_pair",
    "build_discourse_facts",
    "NarrativeComponent",
    "NarrativePlan",
    "plan_narrative",
    "current_value_fact",
    "RenderedNarrative",
    "render_narrative",
]
