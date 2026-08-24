# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D49 bounded proof dossier over exact claim fibers -- promoted to core (2026-08-18).

This, together with `claim_composer.py` (D48) and `lossless_proof_answer.py` (D84), is the
zero-LLM composition pipeline this project's own private research validated as the winning
mechanism for turning a large candidate pool into a compact, byte-budgeted, fully re-verifiable
answer: extraction -> per-fiber ranking -> submodular budget-fill merge -> lossless rendering.
Ported from `lab/proof_dossier.py`; see that module's own history (this project's private
research notes) for the full experimental trail behind each parameter below. Every optional
parameter defaults to `None`/`False`, so a caller who does not pass them gets the original
rank-major merge -- the promotion changes nothing for a caller who does not opt in.

`dedup_threshold` (D135, corrected): a full per-query calibration step was tried and found to
always resolve to the same tactic in practice -- moderate near-duplicate rejection (Jaccard >0.6)
-- so this ships as a plain constant threshold, no calibration machinery required.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math

from .claim_composer import AuthorizedClaim, ClaimSource, ContextIntent, _anchors, \
    extract_authorized_claims
from .materialized_proof_pressure_search import MaterializedIndependentHorizonSearchEngine
from .raw_causal_channels import RawCausalDocument, observe_raw_text


def _content_tokens(text: str) -> frozenset[str]:
    return frozenset(observe_raw_text(text).lexical)


def _jaccard(a: str, b: str) -> float:
    ta, tb = _content_tokens(a), _content_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _obligation_coverage(intent_text: str, claim_surface: str) -> float:
    """Lightweight proxy for `compile_question_obligations`'s own typed coverage (not available
    here -- `build_proof_dossier` only receives a plain `ContextIntent`, deliberately decoupled
    from question compilation so any routing mechanism can produce intents). Token-overlap
    fraction of the intent's own vocabulary the claim covers. See `_obligation_claim_affinity`
    for the richer signal `global_sort_alpha` uses."""
    intent_tokens = _content_tokens(intent_text)
    if not intent_tokens:
        return 0.0
    return len(intent_tokens & _content_tokens(claim_surface)) / len(intent_tokens)


def _obligation_claim_affinity(
    intent_tokens: frozenset[str], intent_anchors: frozenset[str], claim: AuthorizedClaim,
) -> float:
    """Pure token-overlap (`_obligation_coverage`) cannot tell a decoy from an answer when both
    share the same topic vocabulary (a real case: a hedged distractor sharing heavy lexical
    overlap with the query outranked the actual answer on overlap alone). Two already-computed
    `AuthorizedClaim` fields fix this: `modality` (a hedge is 'modal'; a direct answer is
    'asserted') and `anchors` (entity/number spans -- the real answer's anchors are the correct
    ones, a distractor's are not). `asserted_bonus=0.3` was calibrated directly against that real
    failure case (see this project's own research notes) -- large enough to break a modal
    distractor's lexical-overlap lead, not so large it overrides a claim with dramatically better
    lexical/anchor support."""
    lexical = len(intent_tokens & claim.lexical) / len(intent_tokens) if intent_tokens else 0.0
    anchor_overlap = len(intent_anchors & claim.anchors)
    asserted_bonus = 0.3 if claim.modality == "asserted" else 0.0
    return lexical + 0.35 * anchor_overlap + asserted_bonus


def _intent_signal(intent_text: str) -> tuple[frozenset[str], frozenset[str]]:
    channels = observe_raw_text(intent_text, question=True)
    return frozenset(channels.lexical), _anchors(intent_text, channels)


def _anchor_specificity_scores(candidate_claims: list) -> dict[str, float]:
    """IDF-style rarity of each claim's own anchors, computed over the LOCAL candidate pool for
    one call -- a claim whose own anchors are rare within the pool (appear in few other claims)
    is more likely to carry the one genuinely distinguishing fact, regardless of whether that
    anchor is a number, acronym, or proper noun. A classical extractive-summarization technique
    (TF-IDF salience), not invented here. Returns {claim_id: specificity_score}; a claim with no
    anchors scores 0.0."""
    claims = [claim for _rank, _intent, _source, claim in candidate_claims]
    unique_claims = {claim.claim_id: claim for claim in claims}
    n = len(unique_claims)
    if n == 0:
        return {}
    document_frequency: dict[str, int] = {}
    for claim in unique_claims.values():
        for anchor in claim.anchors:
            document_frequency[anchor] = document_frequency.get(anchor, 0) + 1
    scores: dict[str, float] = {}
    for claim_id, claim in unique_claims.items():
        total = 0.0
        for anchor in claim.anchors:
            df = document_frequency[anchor]
            total += math.log(1.0 + (n - df + 0.5) / (df + 0.5))
        scores[claim_id] = total
    return scores


def _has_numeric_anchor(anchors: frozenset[str]) -> bool:
    """`AuthorizedClaim.anchors` is noisy for a relevance bonus: most non-empty anchor sets are
    sentence-initial capitalized words, not genuine distinguishing values. Restricted to
    digit-bearing anchors specifically -- numbers are never subject to that false-positive
    pattern."""
    return any(char.isdigit() for anchor in anchors for char in anchor)


RULE = "d49.proof-dossier.v1"


@dataclass(frozen=True)
class ProofDossier:
    strategy: str
    claims: tuple[AuthorizedClaim, ...]
    evidence_bytes: int
    rendered: str
    digest: str

    def verify(self, sources: tuple[ClaimSource, ...], max_bytes: int) -> bool:
        mapping = {item.source_id: item for item in sources}
        return bool(self.claims and self.evidence_bytes <= max_bytes and
                    self.evidence_bytes == sum(len(item.surface.encode("utf-8"))
                                               for item in self.claims) and
                    all(item.verify(mapping) for item in self.claims) and
                    self.digest == _digest(self.strategy, self.claims,
                                           self.evidence_bytes, self.rendered))


def _digest(strategy: str, claims: tuple[AuthorizedClaim, ...],
            evidence_bytes: int, rendered: str) -> str:
    payload = repr((RULE, strategy, tuple((item.claim_id, item.source_sha256,
                                          item.span, item.polarity, item.modality)
                                         for item in claims),
                    evidence_bytes, rendered))
    return hashlib.sha256(payload.encode()).hexdigest()


def _rank_claims(intent: ContextIntent, claims: tuple[AuthorizedClaim, ...],
                 strategy: str, per_fiber: int) -> tuple[AuthorizedClaim, ...]:
    if not claims:
        return ()
    documents = tuple(RawCausalDocument(index + 1, item.surface, 0, index + 1)
                      for index, item in enumerate(claims))
    by_id = {index + 1: item for index, item in enumerate(claims)}
    engine = MaterializedIndependentHorizonSearchEngine(
        documents, frontier_width=max(32, len(documents)))
    if strategy == "horizon":
        total_bytes = sum(len(item.surface.encode("utf-8")) for item in claims) + 1
        run = engine.search(
            intent.text, max_results=min(per_fiber, len(claims)),
            max_bytes=total_bytes,
            exploration_reserve=min(per_fiber, len(claims)))
        return tuple(by_id[item.fact_id] for item in run.admissions)
    if strategy == "bm25":
        rows = engine.index.rank(
            engine.index.components(intent.text),
            (1.0, 0.0, 0.0, 0.0, 0.0, 0.0))
        return tuple(by_id[item.fact_id] for item in rows
                     if item.lexical > 0)[:per_fiber]
    raise ValueError("strategy must be horizon or bm25")


def _submodular_greedy_select(
    candidates: list[tuple[int, int, str, AuthorizedClaim]],
    intents: tuple[ContextIntent, ...], max_bytes: int,
) -> tuple[AuthorizedClaim, ...]:
    """Budget-constrained submodular maximization over the classic max-cover value function
    `F(S) = sum_over_obligations(|union of (intent_tokens & claim_tokens) for claim in S|)` --
    each obligation's own vocabulary is the ground set to cover, each selected claim covers
    whichever of that vocabulary's tokens it shares. Cost-benefit greedy: at each step, admit the
    remaining candidate claim whose marginal newly-covered-token count per byte is highest, until
    no positive-gain candidate fits the remaining budget. Subsumes `dedup_threshold`'s purpose
    without a Jaccard check, and rewards a claim covering a genuinely different facet of an
    obligation over one that only restates a facet another selected claim already covers, even if
    the restating claim's own raw relevance score is higher."""
    claim_by_id: dict[str, AuthorizedClaim] = {}
    claim_obligations: dict[str, list[tuple[int, frozenset[str]]]] = {}
    intent_tokens_by_index = [_content_tokens(intent.text) for intent in intents]
    for _rank, intent_index, _source_id, claim in candidates:
        claim_by_id[claim.claim_id] = claim
        overlap = intent_tokens_by_index[intent_index] & _content_tokens(claim.surface)
        claim_obligations.setdefault(claim.claim_id, []).append((intent_index, overlap))
    remaining_ids = set(claim_by_id)
    covered_by_intent = [set() for _ in intents]
    selected: list[AuthorizedClaim] = []
    used = 0
    while remaining_ids:
        best_ratio = 0.0
        best_id = None
        for claim_id in remaining_ids:
            claim = claim_by_id[claim_id]
            cost = len(claim.surface.encode("utf-8"))
            if used + cost > max_bytes:
                continue
            new_tokens = sum(len(overlap - covered_by_intent[intent_index])
                             for intent_index, overlap in claim_obligations[claim_id])
            if new_tokens <= 0:
                continue
            ratio = new_tokens / cost
            if ratio > best_ratio:
                best_ratio, best_id = ratio, claim_id
        if best_id is None:
            break
        claim = claim_by_id[best_id]
        selected.append(claim)
        used += len(claim.surface.encode("utf-8"))
        for intent_index, overlap in claim_obligations[best_id]:
            covered_by_intent[intent_index] |= overlap
        remaining_ids.discard(best_id)
    return tuple(selected)


def build_proof_dossier(*, sources: tuple[ClaimSource, ...],
                        intents: tuple[ContextIntent, ...], strategy: str,
                        per_fiber: int = 12, max_bytes: int = 8192,
                        include_paragraphs: bool = False,
                        preserve_sources: bool = False,
                        dedup_threshold: float | None = None,
                        source_priority: dict[str, float] | None = None,
                        global_sort_alpha: float | None = None,
                        submodular_budget_fill: bool = False,
                        anchor_bonus: float | None = None,
                        specificity_bonus: float | None = None) -> ProofDossier:
    """`source_priority`: an optional `{source_id: score}` map. Within each rank tier,
    higher-priority sources are placed before lower-priority ones in the merge queue -- e.g. a
    conformal-routing score, so a strongly-relevant source's 2nd/3rd-best claim is offered budget
    before a marginally-included source's 1st-best claim. Defaults to None (every source has
    equal priority), which reduces to the original rank-major order and preserves every digest
    computed before this parameter existed.

    `global_sort_alpha`: even with `source_priority`, the merge is still rank-major -- every
    included source's own rank-1 claim is offered budget before any source's rank-2 claim, so
    with many included sources a genuinely relevant source's 2nd/3rd-best claim still queues
    behind every other source's single best claim. When set (float in [0,1]), replaces rank-major
    entirely with one global sort key per candidate claim: `alpha * source_priority[source] +
    (1-alpha) * claim_affinity` -- blending document-level confidence with claim-level relevance
    to the specific obligation, filling budget in that single order with no rank-tier grouping at
    all. Defaults to None, preserving the original rank-major (optionally priority-tiebroken)
    order and every digest computed before this parameter existed.

    `submodular_budget_fill`: `global_sort_alpha` still scores and admits each candidate claim
    independently -- it never reconsiders a claim's value in light of what has already been
    selected. When True, replaces the sort-then-greedy-fill merge entirely with
    `_submodular_greedy_select`: cost-benefit greedy maximization of the classic max-cover value
    function. Mutually exclusive with `dedup_threshold`, `source_priority` and
    `global_sort_alpha` -- combining them would leave it ambiguous which selection logic actually
    governs the merge. Defaults to False, preserving every digest computed before this parameter
    existed.

    `anchor_bonus`: only used when `global_sort_alpha` is set. `_obligation_claim_affinity`
    measures topical overlap with the intent text, not whether a claim carries a concrete factual
    value -- this project's own private research found that lets purely topical, anchor-free
    claims systematically outscore claims carrying the answer's own distinguishing anchor. When
    set, a claim's own `combined` sort score is multiplied by `(1 + anchor_bonus)` if
    `_has_numeric_anchor(claim.anchors)`. Defaults to None, preserving every digest computed
    before this parameter existed.

    `specificity_bonus`: `anchor_bonus` only rewards digit-bearing anchors, missing claims whose
    distinguishing content is a rare acronym or proper noun instead. When set, a claim's own
    `combined` score is multiplied by `(1 + specificity_bonus * normalized_specificity)`, where
    `normalized_specificity` is the claim's own anchor-IDF score (see
    `_anchor_specificity_scores`, computed once per call over the LOCAL candidate pool) divided
    by the pool's own maximum score. Compatible with `anchor_bonus` (both may be set; they
    multiply). Defaults to None, preserving every digest computed before this parameter existed.
    """
    if not sources or not intents or per_fiber < 1 or max_bytes < 256:
        raise ValueError("build_proof_dossier requires bounded sources, intents and budgets")
    if global_sort_alpha is not None and not (0.0 <= global_sort_alpha <= 1.0):
        raise ValueError("global_sort_alpha must be in [0,1]")
    if anchor_bonus is not None and anchor_bonus < 0:
        raise ValueError("anchor_bonus must be non-negative")
    if specificity_bonus is not None and specificity_bonus < 0:
        raise ValueError("specificity_bonus must be non-negative")
    if submodular_budget_fill and (dedup_threshold is not None or source_priority is not None
                                   or global_sort_alpha is not None):
        raise ValueError(
            "submodular_budget_fill is mutually exclusive with dedup_threshold, "
            "source_priority and global_sort_alpha")
    known = frozenset(item.source_id for item in sources)
    if any(not item.verify(known) for item in intents):
        raise ValueError("intent/source topology failed authority")
    claims = extract_authorized_claims(
        sources, include_paragraphs=include_paragraphs, preserve_sources=preserve_sources)
    by_source = {source.source_id: tuple(
        item for item in claims if item.source_id == source.source_id)
                 for source in sources}
    candidates = []
    for intent_index, intent in enumerate(intents):
        for source_id in sorted(intent.source_ids):
            ranked = _rank_claims(
                intent, by_source[source_id], strategy, per_fiber)
            candidates.extend((rank, intent_index, source_id, item)
                              for rank, item in enumerate(ranked, 1))
    if submodular_budget_fill:
        selected = list(_submodular_greedy_select(candidates, intents, max_bytes))
        used = sum(len(claim.surface.encode("utf-8")) for claim in selected)
    else:
        if global_sort_alpha is not None:
            intent_signal_by_index = {index: _intent_signal(intent.text)
                                      for index, intent in enumerate(intents)}
            specificity_scores: dict[str, float] = {}
            max_specificity = 0.0
            if specificity_bonus is not None:
                specificity_scores = _anchor_specificity_scores(candidates)
                max_specificity = max(specificity_scores.values(), default=0.0) or 1.0

            def _sort_key(item):
                _rank, intent_index, source_id, claim = item
                doc_score = (source_priority.get(source_id, 0.0)
                            if source_priority is not None else 0.0)
                intent_tokens, intent_anchors = intent_signal_by_index[intent_index]
                claim_affinity = _obligation_claim_affinity(intent_tokens, intent_anchors, claim)
                combined = global_sort_alpha * doc_score + (1 - global_sort_alpha) * claim_affinity
                if anchor_bonus is not None and _has_numeric_anchor(claim.anchors):
                    combined *= (1.0 + anchor_bonus)
                if specificity_bonus is not None:
                    normalized = specificity_scores.get(claim.claim_id, 0.0) / max_specificity
                    combined *= (1.0 + specificity_bonus * normalized)
                return (-combined, intent_index, source_id, claim.span, claim.claim_id)
            ordered_candidates = sorted(candidates, key=_sort_key)
        else:
            ordered_candidates = sorted(
                candidates, key=lambda item: (
                    item[0],
                    -source_priority.get(item[2], 0.0) if source_priority is not None else 0.0,
                    item[1], item[2], item[3].span, item[3].claim_id))
        selected = []
        selected_ids = set()
        used = 0
        for _rank, _intent, _source, claim in ordered_candidates:
            cost = len(claim.surface.encode("utf-8"))
            if claim.claim_id in selected_ids or used + cost > max_bytes:
                continue
            if dedup_threshold is not None and any(
                    _jaccard(claim.surface, item.surface) > dedup_threshold
                    for item in selected):
                continue
            selected.append(claim)
            selected_ids.add(claim.claim_id)
            used += cost
    if not selected:
        raise ValueError("dossier contains no authorized claim")
    frozen = tuple(selected)
    rows = ["Evidence dossier:"]
    for index, claim in enumerate(frozen, 1):
        rows.append(
            f"{index}. [{claim.polarity}/{claim.modality}] {claim.surface} "
            f"[source {claim.source_id} span {claim.span[0]}:{claim.span[1]}]")
    rendered = "\n".join(rows)
    digest = _digest(strategy, frozen, used, rendered)
    dossier = ProofDossier(strategy, frozen, used, rendered, digest)
    if not dossier.verify(sources, max_bytes):
        raise ValueError("dossier failed verification")
    return dossier


__all__ = ["ProofDossier", "RULE", "build_proof_dossier"]
