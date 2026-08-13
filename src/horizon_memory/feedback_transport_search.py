# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Label-free lexical transport as an additional HPPS proposal surface."""
from __future__ import annotations

from collections import Counter
import math

from .materialized_proof_pressure_search import MaterializedIndependentHorizonSearchEngine
from .proof_pressure_search import ProofPressureResult, SearchAdmission
from .raw_causal_channels import observe_raw_text


class FeedbackTransportHorizonSearchEngine(MaterializedIndependentHorizonSearchEngine):
    """Expand address light through terms conserved across ballistic top documents.

    Feedback affects only one ranking surface.  Search obligations and proof closure
    remain compiled from the original query, so an expanded term cannot become proof.
    No qrels, answer labels, model, external vocabulary or API enters this transport.
    """

    def __init__(self, documents, *, feedback_documents: int = 5,
                 feedback_terms: int = 8, minimum_support: int = 2,
                 feedback_weight: float = .35, **kwargs):
        if feedback_documents < 2 or feedback_terms < 1 or minimum_support < 2 or \
                minimum_support > feedback_documents or not 0 < feedback_weight <= 2:
            raise ValueError("invalid feedback transport configuration")
        super().__init__(documents, **kwargs)
        self.feedback_documents = feedback_documents
        self.feedback_terms = feedback_terms
        self.minimum_support = minimum_support
        self.feedback_weight = feedback_weight
        self.feedback_terms_by_query: dict[str, tuple[str, ...]] = {}

    def _expansion(self, query_text: str, direct: tuple[int, ...]) -> tuple[str, ...]:
        query_terms = set(observe_raw_text(query_text, question=True).lexical)
        support: Counter[str] = Counter()
        strength: Counter[str] = Counter()
        for fact_id in direct[:self.feedback_documents]:
            term_frequency = self.index.lexical_tf[fact_id]
            length = max(1, sum(term_frequency.values()))
            for term, frequency in term_frequency.items():
                if term in query_terms or self.index.lexical_df[term] > max(10, self.index.n * .2):
                    continue
                support[term] += 1
                inverse = math.log(1.0 + (self.index.n - self.index.lexical_df[term] + .5) /
                                   (self.index.lexical_df[term] + .5))
                strength[term] += inverse * (1.0 + math.log(frequency)) / length
        candidates = (term for term in strength if support[term] >= self.minimum_support)
        return tuple(sorted(candidates, key=lambda term: (-strength[term], -support[term], term))
                     [:self.feedback_terms])

    def _surfaces(self, query_text: str):
        modes, witnesses = super()._surfaces(query_text)
        direct = dict(modes)["direct"]
        expansion = self._expansion(query_text, direct)
        self.feedback_terms_by_query[query_text] = expansion
        components = {item.fact_id: item for item in self.index.components(query_text)}
        expansion_scores = {}
        for document in self.documents:
            fact_id = document.fact_id
            term_frequency = self.index.lexical_tf[fact_id]
            length = max(1, sum(term_frequency.values()))
            score = 0.0
            for term in expansion:
                frequency = term_frequency[term]
                if not frequency:
                    continue
                inverse = math.log(1.0 + (self.index.n - self.index.lexical_df[term] + .5) /
                                   (self.index.lexical_df[term] + .5))
                score += inverse * (1.0 + math.log(frequency)) / length
            expansion_scores[fact_id] = score
        maximum = max(expansion_scores.values(), default=0.0) or 1.0
        ranking = tuple(sorted(
            (document.fact_id for document in self.documents),
            key=lambda fact_id: (-(components[fact_id].lexical + self.feedback_weight *
                                   expansion_scores[fact_id] / maximum), fact_id),
        ))
        return tuple(sorted(modes + (("feedback", ranking),))), witnesses


class ConservativeFeedbackTransportHorizonSearchEngine(FeedbackTransportHorizonSearchEngine):
    """Monotone incumbent prefix plus feedback only in genuinely unused capacity."""

    def __init__(self, documents, **kwargs):
        super().__init__(documents, **kwargs)
        incumbent_keys = {key: kwargs[key] for key in
                          ("speaker_weight", "role_weight", "sublexical_weight",
                           "core_width", "frontier_width") if key in kwargs}
        self.incumbent = MaterializedIndependentHorizonSearchEngine(documents, **incumbent_keys)

    def search(self, query_text: str, *, max_results: int = 32,
               max_bytes: int | None = None, hard_exclusions: tuple[int, ...] = (),
               exploration_reserve: int = 0, core_width: int | None = None) -> ProofPressureResult:
        base = self.incumbent.search(
            query_text, max_results=max_results, max_bytes=max_bytes,
            hard_exclusions=hard_exclusions, exploration_reserve=exploration_reserve,
            core_width=core_width)
        if len(base.fact_ids) >= max_results:
            return base
        feedback = dict(self._surfaces(query_text)[0])["feedback"]
        selected = list(base.fact_ids)
        admissions = list(base.admissions)
        used_bytes = base.bytes_selected
        excluded = set(hard_exclusions)
        for rank, fact_id in enumerate(feedback[:self.frontier_width * 4]):
            if len(selected) >= max_results:
                break
            if fact_id in selected or fact_id in excluded:
                continue
            cost = self.byte_cost[fact_id]
            if max_bytes is not None and used_bytes + cost > max_bytes:
                continue
            selected.append(fact_id)
            used_bytes += cost
            admissions.append(SearchAdmission(
                fact_id=fact_id, mode="feedback", reason="conservative_unused_capacity",
                closed=(), residual_after=base.residual,
                pressure_gain=round(1.0 / (1.0 + rank), 9), byte_cost=cost,
            ))
        return ProofPressureResult(
            tuple(selected), base.obligations, tuple(admissions), base.residual,
            base.proof_closed, used_bytes, base.excluded)


class ParetoTailFeedbackHorizonSearchEngine(FeedbackTransportHorizonSearchEngine):
    """Protect a ballistic incumbent prefix and weave only the plural tail."""

    def __init__(self, documents, *, protected_width: int = 8,
                 feedback_rrf_weight: float = 1.0, **kwargs):
        if protected_width < 1 or feedback_rrf_weight <= 0:
            raise ValueError("invalid Pareto tail configuration")
        super().__init__(documents, **kwargs)
        incumbent_keys = {key: kwargs[key] for key in
                          ("speaker_weight", "role_weight", "sublexical_weight",
                           "core_width", "frontier_width") if key in kwargs}
        self.incumbent = MaterializedIndependentHorizonSearchEngine(documents, **incumbent_keys)
        self.protected_width = protected_width
        self.feedback_rrf_weight = feedback_rrf_weight

    def search(self, query_text: str, *, max_results: int = 32,
               max_bytes: int | None = None, hard_exclusions: tuple[int, ...] = (),
               exploration_reserve: int = 0, core_width: int | None = None) -> ProofPressureResult:
        base = self.incumbent.search(
            query_text, max_results=max_results, max_bytes=max_bytes,
            hard_exclusions=hard_exclusions, exploration_reserve=exploration_reserve,
            core_width=core_width)
        feedback = dict(self._surfaces(query_text)[0])["feedback"]
        protected = min(self.protected_width, max_results, len(base.fact_ids))
        selected = list(base.fact_ids[:protected])
        used_bytes = sum(self.byte_cost[fact_id] for fact_id in selected)
        incumbent_rank = {fact_id: rank for rank, fact_id in enumerate(base.fact_ids, 1)}
        feedback_rank = {fact_id: rank for rank, fact_id in
                         enumerate(feedback[:self.frontier_width * 4], 1)}
        pool = set(base.fact_ids[protected:]) | set(feedback_rank)
        pool.difference_update(selected)

        def score(fact_id: int) -> float:
            incumbent = (1.0 / (60 + incumbent_rank[fact_id])
                         if fact_id in incumbent_rank else 0.0)
            transported = (self.feedback_rrf_weight / (60 + feedback_rank[fact_id])
                           if fact_id in feedback_rank else 0.0)
            return incumbent + transported

        ordered = sorted(pool, key=lambda fact_id: (
            -score(fact_id), incumbent_rank.get(fact_id, 10**9),
            feedback_rank.get(fact_id, 10**9), fact_id))
        for fact_id in ordered:
            if len(selected) >= max_results:
                break
            cost = self.byte_cost[fact_id]
            if max_bytes is None or used_bytes + cost <= max_bytes:
                selected.append(fact_id)
                used_bytes += cost

        obligations = base.obligations
        required = {item.key for item in obligations}
        closed: set[str] = set()
        admissions = []
        for position, fact_id in enumerate(selected):
            newly_closed = self._coverage(fact_id, obligations).difference(closed)
            closed.update(newly_closed)
            admissions.append(SearchAdmission(
                fact_id=fact_id,
                mode="incumbent" if position < protected else "pareto_tail",
                reason="protected_incumbent_prefix" if position < protected
                else "reciprocal_tail_consensus",
                closed=tuple(sorted(newly_closed)),
                residual_after=tuple(sorted(required.difference(closed))),
                pressure_gain=round(1.0 if position < protected else score(fact_id), 9),
                byte_cost=self.byte_cost[fact_id],
            ))
        residual = tuple(sorted(required.difference(closed)))
        return ProofPressureResult(
            tuple(selected), obligations, tuple(admissions), residual, not residual,
            used_bytes, base.excluded)
