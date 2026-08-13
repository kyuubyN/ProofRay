# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Paired K0 evaluation records with explicit fault attribution."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .adapters import ModelRun, ModelRunState


class EvaluationArm(Enum):
    NO_MEMORY = "NO_MEMORY"
    FULL_CONTEXT = "FULL_CONTEXT"
    RAG = "RAG"
    HORIZON_ORACLE = "HORIZON_ORACLE"
    HORIZON_REAL = "HORIZON_REAL"


@dataclass(frozen=True)
class TrialSignals:
    query_id: str
    arm: EvaluationArm
    retrieval_hit_at_l: bool
    verified_evidence_hit: bool
    answer_correct: bool
    citation_correct: bool
    is_negative: bool
    route_error: bool = False
    verifier_error: bool = False
    storage_error: bool = False


@dataclass(frozen=True)
class TrialRecord:
    query_id: str
    arm: str
    retrieval_hit_at_l: bool
    verified_evidence_hit: bool
    answer_correct: bool
    citation_correct: bool
    reader_given_gold: bool
    reader_given_retrieved: bool
    reader_error: bool
    unsupported_correct: bool
    supported_wrong: bool
    abstention_correct: bool
    route_error: bool
    verifier_error: bool
    storage_error: bool
    input_tokens: int
    output_tokens: int
    prefill_seconds: float | None
    generation_seconds: float | None
    latency_seconds: float | None
    throughput_tokens_s: float | None
    peak_ram_bytes: int | None
    peak_vram_bytes: int | None
    oom: bool
    model_error: bool


def classify_trial(signals: TrialSignals, run: ModelRun) -> TrialRecord:
    support = signals.verified_evidence_hit
    abstained = run.state == ModelRunState.ABSTAINED
    return TrialRecord(
        signals.query_id, signals.arm.value, signals.retrieval_hit_at_l, support,
        signals.answer_correct, signals.citation_correct,
        signals.arm == EvaluationArm.HORIZON_ORACLE and signals.answer_correct,
        support and signals.answer_correct,
        support and not signals.answer_correct and run.state in
        (ModelRunState.GENERATED, ModelRunState.ABSTAINED),
        not support and signals.answer_correct,
        support and not signals.answer_correct,
        signals.is_negative and abstained,
        signals.route_error, signals.verifier_error, signals.storage_error,
        run.input_tokens, run.output_tokens, run.prefill_seconds, run.generation_seconds,
        run.latency_seconds, run.throughput_tokens_s, run.peak_ram_bytes, run.peak_vram_bytes,
        run.state == ModelRunState.OOM, run.state in (ModelRunState.ERROR, ModelRunState.BLOCKED),
    )


def assert_paired_query_ids(rows_by_arm: dict[EvaluationArm, tuple[str, ...]]) -> tuple[str, ...]:
    if set(rows_by_arm) != set(EvaluationArm):
        raise ValueError("all five paired arms are required")
    reference = rows_by_arm[EvaluationArm.NO_MEMORY]
    if len(reference) != len(set(reference)):
        raise ValueError("query_ids must be unique")
    if any(ids != reference for ids in rows_by_arm.values()):
        raise ValueError("paired arms must use identical ordered query_ids")
    return reference
