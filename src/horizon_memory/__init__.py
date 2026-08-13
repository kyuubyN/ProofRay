# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Horizon Memory — standalone, model-agnostic memory for AI systems.

The public entry point is :class:`HorizonMemory`. The package has no VTE,
LLM, hosted-model, or network dependency in its core execution path.
"""
from __future__ import annotations

from .api import HorizonMemory
from .config import HorizonConfig, VALUE_MAX, VALUE_MIN
from .partition import (
    CausalPartitioner, PartitionContext, PartitionIndex, PartitionResult, PartitionStrategy,
)
from .routing import (
    Candidate, CandidateList, CausalWeaveGenerator, DenseGenerator, HorizonVerifier, HybridGenerator, LexicalGenerator,
    BM25Generator, QueryEnvelope, RouteDocument, RoutedResult, RouteState, RouteTrace, RoutingIndex,
    SemanticRouter, TemporalCausalWeaveGenerator,
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
from .durable_causal_memory import CausalDeleteReceipt, DurableCausalMemory
from .concurrent_durable_memory import ConcurrentDurableCausalMemory
from .authority_closed_readout import AuthorityClosedOutput, AuthorityClosedReadout
from .fiber_coherent_search import FiberCoherentSufficientStatisticSearch
from .authorized_fiber_search import AuthorizedFiberRoute, AuthorizedFiberSearchEngine
from .hssd_query_compiler import StructuralHSSDQueryCompiler
from .standalone_hssd_engine import StandaloneHSSDEngine, StandaloneHSSDResult
from .sufficient_statistic_search import SufficientStatisticPack
from .typed_hssd_adapter import TypedCausalHSSDEvidenceAdapter
from .types import (
    AuditReport, CompactResult, CompactState, ExportResult, ExportedFact, Provenance,
    QueryResult, QueryState, ReadResult, ReadState, ReadViewHandle, RecoverResult, RecoverState,
    WriteResult, WriteState,
)

__all__ = [
    "HorizonMemory", "HorizonConfig", "VALUE_MAX", "VALUE_MIN",
    "WriteResult", "WriteState", "ReadResult", "ReadState", "ReadViewHandle",
    "QueryResult", "QueryState", "Provenance", "CompactResult", "CompactState",
    "RecoverResult", "RecoverState", "ExportResult", "ExportedFact", "AuditReport",
    "PartitionContext", "PartitionResult", "PartitionStrategy", "CausalPartitioner",
    "PartitionIndex",
    "QueryEnvelope", "RouteDocument", "Candidate", "CandidateList", "RoutingIndex", "BM25Generator",
    "LexicalGenerator", "DenseGenerator", "HybridGenerator", "CausalWeaveGenerator",
    "TemporalCausalWeaveGenerator",
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
    "ConcurrentDurableCausalMemory",
    "StructuralHSSDQueryCompiler", "TypedCausalHSSDEvidenceAdapter",
    "FiberCoherentSufficientStatisticSearch", "SufficientStatisticPack",
    "AuthorizedFiberRoute", "AuthorizedFiberSearchEngine",
    "StandaloneHSSDEngine", "StandaloneHSSDResult",
    "classify_trial", "assert_paired_query_ids",
]

__version__ = "0.1.0a1"
