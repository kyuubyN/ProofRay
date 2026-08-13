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
]
