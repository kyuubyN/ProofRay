# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""FH-06.2 -- split conformal document routing for the FH-06 router (D133 -> D134 -> D137).

D133 (private research, 2026-08-16/17) found that routing which *chooses* a document set per
question obligation -- ranking, fusing, or even a calibrated tournament between routing tactics
(D134) -- loses decisively to an LLM control (-0.185 judge delta, CI excludes zero), because a
missed document is unrecoverable downstream (claim-level composition can never extract content
from a document it never received) while an extra, irrelevant document is cheap (claim ranking
already filters within-document noise near-ceiling). D137 reframed the problem: stop choosing,
guarantee recall statistically instead, and let claim-level composition (`ClaimGenerator`,
FH-06.1) do the precision work. Split conformal prediction (Vovk et al.) gives exactly that: a
document is included whenever its conformal p-value against a held-out calibration set exceeds
`epsilon` -- a marginal statistical coverage guarantee ("the true document is in the routed set
with probability >= 1-epsilon"), not a ranked cutoff. Paired with claim-level composition and a
priority-aware merge (`EvidencePack.budgeted_items(global_sort_alpha=...)`, D137 Variant 3), this
was the first fully oracle-free mechanism in the private research line's history to clear its own
>=0.90 end-to-end judge-score gate (0.9725 on a 120-episode MemGym-DR pilot, zero LLM anywhere in
routing or composition).

Calibration uses labeled `(query_text, true_fact_id)` pairs from a corpus strictly disjoint from
any corpus routed at inference time (the same freeze-before-you-look discipline as every other
mechanism in this project) -- oracle labels are used only to build `ConformalCalibrator`, never
at routing time. At routing/inference time, `ConformalDocumentGenerator`/`ConformalClaimGenerator`
are zero-oracle: they see only `query.text` and the eligible document pool.

Weight choice (`LEXICAL_SUBLEXICAL_WEIGHTS`, lexical+sublexical blend, no entity/relation/
observable/contradiction): D138 (LongMemEval, 2026-08-17) found lexical-only (BM25) scoring is
corpus-dependent -- on short, paraphrase-heavy conversational turns, ~25% of true-match
calibration scores land at exactly zero (no shared lexical token between question and
answer-bearing message), which pushes any reasonable epsilon below the resulting p-value floor
and admits nearly the entire corpus regardless of relevance. Blending in the sublexical
(character n-gram) channel catches near-matches BM25 misses and restores genuine selective
routing. Callers with a corpus more like MemGym-DR's technical prose (where lexical-only rarely
degenerates) may still pass a different `weights` tuple; the default is chosen for the harder,
more general case.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass

from .claim_routing import ClaimGenerator, DEFAULT_WEIGHTS as CLAIM_GENERATOR_DEFAULT_WEIGHTS
from .materialized_proof_pressure_search import MaterializedRawCausalSyndromeIndex
from .raw_causal_channels import RawCausalDocument
from .routing import Candidate, CandidateGenerator, CandidateList, RouteDocument, RoutingIndex


LEXICAL_SUBLEXICAL_WEIGHTS: tuple[float, float, float, float, float, float] = (
    0.6, 0.4, 0.0, 0.0, 0.0, 0.0)


def _document_index(
    documents: tuple[RouteDocument, ...],
) -> MaterializedRawCausalSyndromeIndex:
    raw = tuple(RawCausalDocument(doc.fact_id, doc.text, 0, 0) for doc in documents)
    return MaterializedRawCausalSyndromeIndex(raw)


def score_documents(
    query_text: str, documents: tuple[RouteDocument, ...],
    weights: tuple[float, float, float, float, float, float] = LEXICAL_SUBLEXICAL_WEIGHTS,
) -> dict[int, float]:
    """s(query, doc) for every document -- keyed by `RouteDocument.fact_id`. Convenience wrapper
    that builds a fresh index; a caller scoring many queries against the same document set should
    build `_document_index` once and call `.rank(.components(...), weights)` directly instead."""
    if not documents:
        return {}
    index = _document_index(documents)
    ranked = index.rank(index.components(query_text), weights)
    return {item.fact_id: item.amplitude for item in ranked}


@dataclass(frozen=True)
class ConformalCalibrator:
    """Split conformal (Vovk et al.) calibration set: sorted true-match scores from a held-out
    calibration corpus, strictly disjoint from any corpus routed at inference time."""
    calibration_scores: tuple[float, ...]  # pre-sorted ascending

    def __post_init__(self) -> None:
        if not self.calibration_scores:
            raise ValueError("conformal calibration requires at least one calibration score")
        if list(self.calibration_scores) != sorted(self.calibration_scores):
            raise ValueError("calibration_scores must be sorted ascending")

    def p_value(self, score: float) -> float:
        below_or_equal = bisect.bisect_right(self.calibration_scores, score)
        return (1.0 + below_or_equal) / (len(self.calibration_scores) + 1.0)


def collect_calibration_scores(
    calibration_episodes,
    weights: tuple[float, float, float, float, float, float] = LEXICAL_SUBLEXICAL_WEIGHTS,
) -> tuple[float, ...]:
    """`calibration_episodes` is an iterable of `(documents, true_pairs)`, where `documents` is a
    `tuple[RouteDocument, ...]` (one calibration episode's own corpus) and `true_pairs` is a
    tuple of `(query_text, true_fact_id)`. One calibration score is collected per true pair that
    resolves to a document in that episode's own corpus -- oracle labels are used here only,
    never at routing time. `weights` must match whatever `weights` routing will use; calibration
    and inference must share the same score function."""
    scores: list[float] = []
    for documents, true_pairs in calibration_episodes:
        if not documents or not true_pairs:
            continue
        index = _document_index(documents)
        for query_text, true_fact_id in true_pairs:
            ranked = index.rank(index.components(query_text), weights)
            for item in ranked:
                if item.fact_id == true_fact_id:
                    scores.append(item.amplitude)
                    break
    return tuple(sorted(scores))


class ConformalDocumentGenerator(CandidateGenerator):
    """FH-06.2: includes every eligible document whose conformal p-value against `query.text`
    exceeds `epsilon` -- a marginal statistical coverage guarantee, not a ranked top-k cutoff.

    Bounded to `limit` candidates like every other `CandidateGenerator`: when more than `limit`
    documents clear epsilon, only the `limit` highest-scoring are returned. `SemanticRouter.
    route()`'s own candidate-count ceiling was historically fixed at 32 (`{1,2,4,8,16,32}`); it
    was relaxed to any positive integer 2026-08-17 specifically so a large byte budget (tens of
    KB) can be filled with enough claim-level candidates without the candidate count binding
    before the budget does -- pass a `limit` sized to the target budget (roughly
    `budget_bytes / expected_claim_bytes`) rather than relying on the historical default."""
    channel = "conformal"

    def __init__(self, calibrator: ConformalCalibrator, epsilon: float,
                weights: tuple[float, float, float, float, float, float]
                = LEXICAL_SUBLEXICAL_WEIGHTS):
        if not (0.0 < epsilon < 1.0):
            raise ValueError("epsilon must be in (0,1)")
        if len(weights) != 6 or any(weight < 0 for weight in weights):
            raise ValueError("six non-negative channel weights are required")
        self.calibrator = calibrator
        self.epsilon = epsilon
        self.weights = weights

    def generate(self, query, index, limit, same_session=True):
        eligible = index.eligible(query, same_session)
        if not eligible:
            return CandidateList(())
        raw_index = _document_index(eligible)
        ranked = raw_index.rank(raw_index.components(query.text), self.weights)
        included = [item for item in ranked
                   if self.calibrator.p_value(item.amplitude) > self.epsilon]
        namespace = "scope_session" if same_session else "scope_fallback"
        return CandidateList(tuple(
            Candidate(item.fact_id, item.amplitude, self.channel, rank, namespace)
            for rank, item in enumerate(included[:limit], 1)))


class ConformalClaimGenerator(CandidateGenerator):
    """FH-06.2 composed with FH-06.1: restricts claim-level extraction (`ClaimGenerator`) to only
    the documents `ConformalDocumentGenerator` includes -- D137's exact winning pipeline (recall-
    guaranteed document routing feeding claim-level precision ranking), expressed as a single
    `CandidateGenerator` so it drops into `SemanticRouter` like any other. Returns an empty
    `CandidateList` when no document clears the conformal bar -- an intentional abstention (per
    the project's own abstention-over-guessing ground rule), not a silent widen to a bigger
    epsilon.

    `document_router` is kept as a public attribute so a caller building an
    `EvidencePack.budgeted_items(source_priority=...)` map can reuse the exact same document-level
    routing this generator used internally -- see `document_priority_by_source`."""
    channel = "conformal_claim"

    def __init__(self, calibrator: ConformalCalibrator, epsilon: float,
                document_weights: tuple[float, float, float, float, float, float]
                = LEXICAL_SUBLEXICAL_WEIGHTS,
                claim_weights: tuple[float, float, float, float, float, float]
                = CLAIM_GENERATOR_DEFAULT_WEIGHTS):
        self.document_router = ConformalDocumentGenerator(calibrator, epsilon, document_weights)
        self.claim_generator = ClaimGenerator(claim_weights)

    def generate(self, query, index, limit, same_session=True):
        # Request a document pool at least as large as the caller's own claim-level `limit`
        # (floor 32, `SemanticRouter.route()`'s pre-FH-06.2 default ceiling) -- filling a large
        # byte budget needs many short claims, which in turn needs at least that many candidate
        # documents to extract them from; a tight claim-level limit should not also throttle
        # document recall below the historical 32-document floor.
        document_pool = max(limit, 32)
        routed = self.document_router.generate(query, index, document_pool, same_session)
        if not routed.candidates:
            return CandidateList(())
        included_ids = {candidate.fact_id for candidate in routed.candidates}
        filtered_documents = tuple(doc for doc in index.eligible(query, same_session)
                                   if doc.fact_id in included_ids)
        sub_index = RoutingIndex(filtered_documents)
        return self.claim_generator.generate(query, sub_index, limit, same_session)


def document_priority_by_source(routed_documents: CandidateList,
                                index: RoutingIndex) -> dict[str, float]:
    """Builds a `{RouteDocument.source: score}` map from a document-level `CandidateList` (e.g.
    `ConformalClaimGenerator.document_router`'s own last routing), for use as
    `EvidencePack.budgeted_items(source_priority=...)` (D137 Variant 1/3). Max score wins when
    multiple documents share the same `source` label."""
    priority: dict[str, float] = {}
    for candidate in routed_documents.candidates:
        document = index.by_id.get(candidate.fact_id)
        if document is None:
            continue
        priority[document.source] = max(priority.get(document.source, float("-inf")),
                                        candidate.score)
    return priority


__all__ = [
    "ConformalCalibrator", "ConformalClaimGenerator", "ConformalDocumentGenerator",
    "LEXICAL_SUBLEXICAL_WEIGHTS", "collect_calibration_scores", "document_priority_by_source",
    "score_documents",
]
