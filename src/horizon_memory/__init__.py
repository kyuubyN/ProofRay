# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ProofRay — standalone, model-agnostic memory for AI systems.

The historical ``horizon_memory`` namespace remains supported for compatibility.
New integrations may import the same API through :mod:`proofray`. The package has
no VTE, LLM, hosted-model, or network dependency in its core execution path.
"""
from __future__ import annotations

from .api import HorizonMemory
from .config import HorizonConfig, VALUE_MAX, VALUE_MIN
from .engine_profile import (
    CONVERSATIONAL_HIGH_RECALL_PROFILE, DEFAULT_PROFILE, PERSONAL_MEMORY_PROFILE,
    TEAM_MEMORY_PROFILE, EngineProfile,
)
from .answer_engine import (
    AnswerContextIntent, AnsweredClaim, AnsweredResult, ContextualDirectAnswerCertificate,
    ContextualDirectAnswerResolver, DirectAnswer, DirectAnswerCertificate,
    DirectAnswerProposal, DirectAnswerReader, DirectAnswerResolution, DirectAnswerResolver,
    HorizonAnswerEngine,
)
from .partition import (
    CausalPartitioner, PartitionContext, PartitionIndex, PartitionResult, PartitionStrategy,
)
from .routing import (
    Candidate, CandidateList, CausalWeaveGenerator, DenseGenerator, HorizonVerifier, HybridGenerator, LexicalGenerator,
    BM25Generator, QueryEnvelope, RouteDocument, RoutedResult, RouteState, RouteTrace, RoutingIndex,
    SemanticRouter, TemporalCausalWeaveGenerator,
)
from .conversational_recall import (
    CalendarInterval, ConversationalRecallConfig, ConversationalRecallGenerator,
    ConversationalRecallTrace, compile_explicit_calendar_interval,
    expand_session_neighbors, observe_exhaustive_recall, protected_rank_merge,
    reciprocal_rank_fusion, stable_speaker_partition,
)
from .witness_frontload import (
    QueryWitnessFrontloadConfig, frontload_query_witnesses, frontload_text_lines,
    utf8_line_prefix,
)
from .proof_convergent_executor import (
    AttestedScalarLedger, ConvergentScalarAnswer, IntegratedConvergentAnswer,
    compact_scalar_answer, integrate_with_deterministic_fallback,
    open_compact_scalar_answer, render_convergent_answer,
)
from .proof_convergent_resolver import (
    ProofConvergentCertificate, ProofConvergentResolver,
)
from .explanatory_obligation_proof import (
    ExplanatoryIntent, ExplanatoryProofCertificate, ExplanatoryProofConfig,
    ExplanatoryProofResult, ExplanatorySource, JoinClosure, ObligationGraph,
    ObligationNode, WitnessBinding, WitnessedBridge, compile_obligation_graph,
    solve_explanatory_obligations,
)
from .explanatory_proof_resolver import (
    ExplanatoryDirectAnswerCertificate, ExplanatoryProofResolver,
    ProofCascadeResolver,
)
from .claim_routing import ClaimGenerator, DEFAULT_WEIGHTS as CLAIM_GENERATOR_DEFAULT_WEIGHTS, claim_spans
from .conformal_routing import (
    ConformalCalibrator, ConformalClaimGenerator, ConformalDocumentGenerator,
    LEXICAL_SUBLEXICAL_WEIGHTS, collect_calibration_scores as collect_conformal_calibration_scores,
    document_priority_by_source, score_documents as conformal_score_documents,
)
from .evidence import EvidenceItem, EvidencePack
from .codec import (CompressionReport, ExactExecution, ProofCarryingCodec, compile_query_equation,
                    execute_exact, semantic_charges)
from .evaluation import EvaluationArm, TrialRecord, TrialSignals, assert_paired_query_ids, classify_trial
from .typed_causal_program import (
    CausalSelector, TypedCausalExecutor, TypedCausalFact, TypedCausalProgram, TypedCausalProof,
    TypedCausalResult,
)
from .typed_causal_ingest import (
    CausalSourceEnvelope, DeterministicCausalCompiler, StructuredCausalDeclaration,
)
from .json_causal_adapter import (
    JsonCausalMapping, JsonLeaf, JsonPointerCausalAdapter, JsonSourceMap,
)
from .causal_adapter_protocol import CausalAdapterBatch, CausalIngestAdapter
from .standalone_causal_memory import CausalIngestReceipt, StandaloneCausalMemory
from .typed_sidecar import (
    AttestedCompletenessClaim, AttestedSidecarFact, AuthorizedSidecarMemory,
    CompletenessCertificate, SidecarAuthority, SidecarCompilation, SidecarIngestAdapter,
    AuthorizedAdapterBridge, DeclarativeSidecarAdapter, SidecarCompletenessDeclaration,
    SidecarFactDeclaration,
    SidecarLifecycle,
    SidecarObservedIntent,
    SidecarRouteMetadata,
    SidecarIngestReceipt,
    SidecarLimits,
)
from .durable_causal_memory import CausalDeleteReceipt, DurableCausalMemory
from .concurrent_durable_memory import ConcurrentDurableCausalMemory
from .durable_typed_sidecar import (
    AuthorizedSidecarRecordStore, DurableAuthorizedSidecarMemory,
    FileAuthorizedSidecarRecordStore, MemoryAuthorizedSidecarRecordStore,
)
from .open_text_memory import (
    MEMGYM_REFERENCE_PROFILE, OpenTextAtomicRelationResult, OpenTextAtomicRelationResultPT,
    OpenTextEvidenceResult, OpenTextHorizonMemory,
)
from .english_atomic_relations import (
    BinaryQueryDemand, BinarySpanReading, EnglishAtomicRelationCompiler,
    EnglishAtomicRelationProof, EnglishAtomicRelationResult, VERB_EXCEPTIONS_SHA256,
    compact_english_atomic_relation, open_compact_english_atomic_relation,
)
from .portuguese_atomic_relations import (
    RoleReadResult, read as read_pt_atomic_relation,
    resolve_surface_role as resolve_pt_surface_role,
)
from .authority_closed_readout import AuthorityClosedOutput, AuthorityClosedReadout
from .fiber_coherent_search import FiberCoherentSufficientStatisticSearch
from .authorized_fiber_search import AuthorizedFiberRoute, AuthorizedFiberSearchEngine
from .hssd_query_compiler import HSSDQueryLattice, StructuralHSSDQueryCompiler
from .standalone_hssd_engine import StandaloneHSSDEngine, StandaloneHSSDResult
from .passage_difference_proof import (
    BoundDifferenceOperand, PassageHomogeneousDifferenceProof,
    compile_passage_homogeneous_difference,
)
from .event_date_interval import (
    EventDateAlignment, EventDateIntervalProof, compile_event_date_interval,
)
from .field_goal_extremum_proof import (
    FieldGoalDistance, FieldGoalExtremumProof, compile_field_goal_extremum,
)
from .field_goal_count_proof import FieldGoalCountProof, compile_field_goal_count
from .final_score_sum_proof import (
    FinalScoreMarginProof, FinalScoreSumProof, compile_final_score_margin,
    compile_final_score_sum,
)
from .sufficient_statistic_search import SufficientStatisticPack
from .typed_hssd_adapter import TypedCausalHSSDEvidenceAdapter
from .types import (
    AuditReport, CompactResult, CompactState, ExportResult, ExportedFact, Provenance,
    QueryResult, QueryState, ReadResult, ReadState, ReadViewHandle, RecoverResult, RecoverState,
    WriteResult, WriteState,
)

__all__ = [
    "HorizonMemory", "HorizonConfig", "VALUE_MAX", "VALUE_MIN",
    "CONVERSATIONAL_HIGH_RECALL_PROFILE", "DEFAULT_PROFILE", "PERSONAL_MEMORY_PROFILE",
    "TEAM_MEMORY_PROFILE", "EngineProfile",
    "AnswerContextIntent", "AnsweredClaim", "AnsweredResult", "DirectAnswer",
    "ContextualDirectAnswerCertificate", "ContextualDirectAnswerResolver",
    "DirectAnswerCertificate", "DirectAnswerProposal", "DirectAnswerReader",
    "DirectAnswerResolution", "DirectAnswerResolver", "HorizonAnswerEngine",
    "WriteResult", "WriteState", "ReadResult", "ReadState", "ReadViewHandle",
    "QueryResult", "QueryState", "Provenance", "CompactResult", "CompactState",
    "RecoverResult", "RecoverState", "ExportResult", "ExportedFact", "AuditReport",
    "PartitionContext", "PartitionResult", "PartitionStrategy", "CausalPartitioner",
    "PartitionIndex",
    "QueryEnvelope", "RouteDocument", "Candidate", "CandidateList", "RoutingIndex", "BM25Generator",
    "LexicalGenerator", "DenseGenerator", "HybridGenerator", "CausalWeaveGenerator",
    "TemporalCausalWeaveGenerator", "ClaimGenerator", "CLAIM_GENERATOR_DEFAULT_WEIGHTS", "claim_spans",
    "CalendarInterval", "ConversationalRecallConfig", "ConversationalRecallGenerator",
    "ConversationalRecallTrace", "compile_explicit_calendar_interval",
    "expand_session_neighbors", "observe_exhaustive_recall", "protected_rank_merge",
    "reciprocal_rank_fusion", "stable_speaker_partition",
    "QueryWitnessFrontloadConfig", "frontload_query_witnesses", "frontload_text_lines",
    "utf8_line_prefix",
    "AttestedScalarLedger", "ConvergentScalarAnswer", "IntegratedConvergentAnswer",
    "compact_scalar_answer", "integrate_with_deterministic_fallback",
    "open_compact_scalar_answer", "render_convergent_answer",
    "ProofConvergentCertificate", "ProofConvergentResolver",
    "ExplanatoryIntent", "ExplanatoryProofCertificate", "ExplanatoryProofConfig",
    "ExplanatoryProofResult", "ExplanatorySource", "JoinClosure", "ObligationGraph",
    "ObligationNode", "WitnessBinding", "WitnessedBridge", "compile_obligation_graph",
    "solve_explanatory_obligations", "ExplanatoryDirectAnswerCertificate",
    "ExplanatoryProofResolver", "ProofCascadeResolver",
    "ConformalCalibrator", "ConformalClaimGenerator", "ConformalDocumentGenerator",
    "LEXICAL_SUBLEXICAL_WEIGHTS", "collect_conformal_calibration_scores",
    "document_priority_by_source", "conformal_score_documents",
    "HorizonVerifier", "SemanticRouter",
    "RoutedResult", "RouteState", "RouteTrace",
    "EvidenceItem", "EvidencePack", "EvaluationArm", "TrialSignals", "TrialRecord",
    "CompressionReport", "ProofCarryingCodec", "compile_query_equation", "semantic_charges",
    "ExactExecution", "execute_exact",
    "TypedCausalFact", "CausalSelector", "TypedCausalProgram", "TypedCausalResult",
    "TypedCausalProof", "TypedCausalExecutor", "CausalSourceEnvelope",
    "StructuredCausalDeclaration", "DeterministicCausalCompiler",
    "JsonLeaf", "JsonSourceMap", "JsonCausalMapping", "JsonPointerCausalAdapter",
    "CausalAdapterBatch", "CausalIngestAdapter", "CausalIngestReceipt",
    "StandaloneCausalMemory", "DurableCausalMemory", "CausalDeleteReceipt",
    "SidecarAuthority", "AttestedSidecarFact", "SidecarIngestAdapter",
    "AttestedCompletenessClaim", "SidecarCompilation", "CompletenessCertificate",
    "SidecarLifecycle",
    "SidecarObservedIntent",
    "SidecarRouteMetadata",
    "SidecarFactDeclaration", "SidecarCompletenessDeclaration", "DeclarativeSidecarAdapter",
    "AuthorizedAdapterBridge",
    "SidecarIngestReceipt", "AuthorizedSidecarMemory",
    "SidecarLimits",
    "ConcurrentDurableCausalMemory",
    "AuthorizedSidecarRecordStore", "DurableAuthorizedSidecarMemory",
    "FileAuthorizedSidecarRecordStore", "MemoryAuthorizedSidecarRecordStore",
    "MEMGYM_REFERENCE_PROFILE", "OpenTextAtomicRelationResult", "OpenTextAtomicRelationResultPT",
    "OpenTextEvidenceResult",
    "OpenTextHorizonMemory", "BinaryQueryDemand", "BinarySpanReading",
    "EnglishAtomicRelationCompiler", "EnglishAtomicRelationProof",
    "EnglishAtomicRelationResult", "VERB_EXCEPTIONS_SHA256",
    "compact_english_atomic_relation", "open_compact_english_atomic_relation",
    "RoleReadResult", "read_pt_atomic_relation", "resolve_pt_surface_role",
    "HSSDQueryLattice", "StructuralHSSDQueryCompiler", "TypedCausalHSSDEvidenceAdapter",
    "FiberCoherentSufficientStatisticSearch", "SufficientStatisticPack",
    "AuthorizedFiberRoute", "AuthorizedFiberSearchEngine",
    "StandaloneHSSDEngine", "StandaloneHSSDResult",
    "BoundDifferenceOperand", "PassageHomogeneousDifferenceProof",
    "compile_passage_homogeneous_difference",
    "EventDateAlignment", "EventDateIntervalProof", "compile_event_date_interval",
    "FieldGoalDistance", "FieldGoalExtremumProof", "compile_field_goal_extremum",
    "FieldGoalCountProof", "compile_field_goal_count",
    "FinalScoreMarginProof", "FinalScoreSumProof", "compile_final_score_margin",
    "compile_final_score_sum",
    "classify_trial", "assert_paired_query_ids",
]

__version__ = "0.1.0a1"

# ProofRay public spelling.  These aliases intentionally preserve the mature Horizon
# class identities, serialized records and import paths during the alpha rebrand.
ProofRayMemory = HorizonMemory
ProofRayConfig = HorizonConfig
ProofRayAnswerEngine = HorizonAnswerEngine
ProofRayVerifier = HorizonVerifier
OpenTextProofRayMemory = OpenTextHorizonMemory
__all__ += [
    "ProofRayMemory", "ProofRayConfig", "ProofRayAnswerEngine", "ProofRayVerifier",
    "OpenTextProofRayMemory",
]
