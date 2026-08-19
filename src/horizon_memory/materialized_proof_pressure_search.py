# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Query-efficient HPPS for unordered independent-document collections.

The original raw syndrome index is deliberately simple and reconstructs term
frequencies per query.  This equivalent index conserves those cold observables at
ingest time.  Independent collections have no contextual cavity edges: corpus order
cannot manufacture evidence.
"""
from __future__ import annotations

from collections import Counter
import math

from .contextual_cavity import CavityScore
from .proof_pressure_search import HorizonSearchEngine
from .raw_causal_channels import (
    RawCausalDocument, SignedChannelScore, _modal_is_confirmed_finding, observe_raw_text,
)


class MaterializedRawCausalSyndromeIndex:
    def __init__(self, documents: tuple[RawCausalDocument, ...]):
        if not documents or len({item.fact_id for item in documents}) != len(documents):
            raise ValueError("materialized raw documents require unique FactIds")
        self.documents = documents
        self.channels = {item.fact_id: observe_raw_text(item.text) for item in documents}
        self.n = len(documents)
        self.lexical_tf = {key: Counter(value.lexical) for key, value in self.channels.items()}
        self.sublexical_tf = {key: Counter(value.sublexical) for key, value in self.channels.items()}
        self.lexical_df = Counter(token for value in self.channels.values()
                                  for token in set(value.lexical))
        self.sublexical_df = Counter(token for value in self.channels.values()
                                     for token in set(value.sublexical))
        self.avgdl = sum(len(value.lexical) for value in self.channels.values()) / self.n
        self.avg_subdl = sum(len(value.sublexical) for value in self.channels.values()) / self.n
        self.entity_sets = {key: set(value.entities) for key, value in self.channels.items()}
        self.relation_sets = {key: set(value.relations) for key, value in self.channels.items()}
        self._cache: dict[str, tuple[SignedChannelScore, ...]] = {}
        # 2026-08-18: same technique already validated twice this session at other pipeline
        # stages (`lab/proof_dossier.py`'s `_anchor_specificity_scores`, `evidence.py`'s
        # `_numeric_specificity_scores`) -- IDF over each document's own distinguishing tokens
        # (entities + numbers) within the CANDIDATE POOL this index was built over, not a static
        # dictionary. Query-independent (unlike `components()`'s own per-query BM25 scores), so
        # computed once here rather than per `rank()` call. `rank()`'s own optional
        # `specificity_bonus` parameter is the first test of this signal at the ClaimGenerator
        # candidate-ranking stage specifically (distinct from the acquisition-budget stage, where
        # the same signal tested negative, and the final-render stage, where it tested positive).
        anchor_sets = {key: value.entities + value.numbers for key, value in self.channels.items()}
        anchor_df: Counter = Counter()
        for anchors in anchor_sets.values():
            anchor_df.update(set(anchors))
        self.specificity_scores: dict[int, float] = {}
        for key, anchors in anchor_sets.items():
            score = sum(math.log(1.0 + (self.n - anchor_df[a] + .5) / (anchor_df[a] + .5))
                       for a in set(anchors))
            self.specificity_scores[key] = score
        max_specificity = max(self.specificity_scores.values(), default=0.0) or 1.0
        for key in self.specificity_scores:
            self.specificity_scores[key] /= max_specificity

    def _bm25(self, query: tuple[str, ...], tf: Counter, length: int,
              df: Counter, average_length: float) -> float:
        score = 0.0
        for token in set(query):
            frequency = tf[token]
            if not frequency:
                continue
            inverse = math.log(1.0 + (self.n - df[token] + .5) / (df[token] + .5))
            score += inverse * frequency * 2.2 / (
                frequency + 1.2 * (.25 + .75 * length / max(average_length, 1e-9)))
        return score

    @staticmethod
    def _normalize(rows: list[dict], name: str) -> None:
        maximum = max((abs(row[name]) for row in rows), default=0.0) or 1.0
        for row in rows:
            row[name] /= maximum

    def components(self, query_text: str) -> tuple[SignedChannelScore, ...]:
        cached = self._cache.get(query_text)
        if cached is not None:
            return cached
        query = observe_raw_text(query_text, question=True)
        rows = []
        for document in self.documents:
            fact_id = document.fact_id
            value = self.channels[fact_id]
            observable = 0.0
            if query.interrogative == "time" and (value.temporal or value.numbers):
                observable += 1.0
            if query.interrogative in ("person", "place") and value.entities:
                observable += 1.0
            if query.interrogative == "quantity" and (value.numbers or len(value.entities) >= 2):
                observable += 1.0
            if query.numbers and set(query.numbers).intersection(value.numbers):
                observable += 1.0
            contradiction = 0.0
            # A prior version of this rule fired the FULL 1.0 contradiction penalty whenever the
            # query and a candidate both mentioned some number and those specific numbers didn't
            # match -- e.g. a MemGym-DR question giving "approximately 52% of such faults" as
            # unrelated background context, penalizing a candidate correctly discussing a totally
            # different metric ("5.3% computational area overhead") as if it contradicted the
            # question. Confirmed 2026-08-18 as a real, severe defect, not a tuning question:
            # traced a specific real case (MemGym-DR ordinal 52) where this rule alone crushed a
            # gold-critical, otherwise well-matching candidate (lexical=0.44, sublexical=0.62,
            # entity=0.67, relation=0.8) down to amplitude ~0.01, well below weaker but
            # number-free competitors. "The question and this candidate both mention some number,
            # and the numbers differ" has no reliable way to tell "these are the same fact stated
            # differently" from "these are two different facts, only one of which the question
            # even asked about" -- removed rather than re-tuned, since there is no cheap general
            # signal here to recover (this is not the same kind of fix as the polarity/modality
            # rules below, which key on an actual negation/hedge word, a real per-claim signal).
            if query.polarity == "negative" and value.polarity == "positive":
                contradiction += .5
            # 2026-08-18 (plan item 1b): don't penalize a claim reporting an already-CONFIRMED
            # finding just because it also contains a modal word ("the study demonstrated X
            # could improve Y by 15%") -- that isn't a hedge, unlike a genuine "might/could"
            # distractor with no confirming verb (the IKEA-bookshelf case this rule exists for,
            # confirmed unaffected: its query has no numbers, so this rule never fired for it
            # either way, and its own modal sentence has no confirming verb before "might").
            if (query.modality == "asserted" and value.modality == "modal"
                    and not _modal_is_confirmed_finding(document.text)):
                contradiction += .25
            rows.append({
                "fact_id": fact_id,
                "lexical": self._bm25(query.lexical, self.lexical_tf[fact_id],
                                        len(value.lexical), self.lexical_df, self.avgdl),
                "sublexical": self._bm25(query.sublexical, self.sublexical_tf[fact_id],
                                           len(value.sublexical), self.sublexical_df, self.avg_subdl),
                "entity": float(len(set(query.entities).intersection(self.entity_sets[fact_id]))),
                "relation": float(len(set(query.relations).intersection(self.relation_sets[fact_id]))),
                "observable": observable, "contradiction": contradiction,
            })
        for name in ("lexical", "sublexical", "entity", "relation", "observable"):
            self._normalize(rows, name)
        result = tuple(SignedChannelScore(**row, amplitude=0.0) for row in rows)
        self._cache[query_text] = result
        return result

    def rank(self, components: tuple[SignedChannelScore, ...], weights: tuple[float, ...], *,
             specificity_bonus: float | None = None) -> tuple[SignedChannelScore, ...]:
        # `specificity_bonus` (2026-08-18, opt-in, `None` preserves every prior digest): was a
        # `@staticmethod` before this parameter needed `self.specificity_scores` -- every call
        # site already invokes this via an instance (`index.rank(...)`), so converting to a
        # regular instance method is fully backward compatible, confirmed by grepping every
        # caller in `src/`/`tests/`/`lab/` before making the change.
        if len(weights) != 6 or any(weight < 0 for weight in weights):
            raise ValueError("six non-negative signed-field weights are required")
        rows = []
        for item in components:
            amplitude = (weights[0] * item.lexical + weights[1] * item.sublexical +
                         weights[2] * item.entity + weights[3] * item.relation +
                         weights[4] * item.observable - weights[5] * item.contradiction)
            if specificity_bonus is not None:
                amplitude *= (1.0 + specificity_bonus * self.specificity_scores.get(item.fact_id, 0.0))
            rows.append(SignedChannelScore(
                item.fact_id, amplitude, item.lexical, item.sublexical, item.entity,
                item.relation, item.observable, item.contradiction))
        return tuple(sorted(rows, key=lambda item: (-item.amplitude, item.fact_id)))


class IndependentDocumentCavity:
    """Identity topology: no corpus-order propagation and no invented witnesses."""

    def __init__(self, index: MaterializedRawCausalSyndromeIndex):
        self.index = index

    def rank(self, query_text: str, *, lexical_weight: float = 1.0,
             sublexical_weight: float = 0.0, speaker_weight: float = 0.0,
             session_weight: float = 0.0, role_weight: float = 0.0) -> tuple[CavityScore, ...]:
        if min(lexical_weight, sublexical_weight, speaker_weight,
               session_weight, role_weight) < 0:
            raise ValueError("independent cavity weights cannot be negative")
        components = self.index.components(query_text)
        rows = tuple(CavityScore(
            item.fact_id,
            lexical_weight * item.lexical + sublexical_weight * item.sublexical,
            lexical_weight * item.lexical + sublexical_weight * item.sublexical,
            0.0, (),
        ) for item in components)
        return tuple(sorted(rows, key=lambda item: (-item.amplitude, -item.direct, item.fact_id)))


class MaterializedIndependentHorizonSearchEngine(HorizonSearchEngine):
    """Same HPPS admission law with ingest-time observables and identity topology."""

    def __init__(self, documents: tuple[RawCausalDocument, ...], **kwargs):
        allowed = {"speaker_weight", "role_weight", "sublexical_weight",
                   "core_width", "frontier_width"}
        unknown = set(kwargs) - allowed
        if unknown:
            raise TypeError(f"unsupported independent search parameters: {sorted(unknown)}")
        if not documents or len({item.fact_id for item in documents}) != len(documents):
            raise ValueError("documents require unique FactIds")
        self.documents = documents
        self.by_id = {item.fact_id: item for item in documents}
        self.index = MaterializedRawCausalSyndromeIndex(documents)
        self.channels = self.index.channels
        self.cavity = IndependentDocumentCavity(self.index)
        self.speaker_weight = kwargs.get("speaker_weight", 1.0)
        self.role_weight = kwargs.get("role_weight", .2)
        self.sublexical_weight = kwargs.get("sublexical_weight", .25)
        self.core_width = kwargs.get("core_width", 1)
        self.frontier_width = kwargs.get("frontier_width", 32)
        if min(self.speaker_weight, self.role_weight, self.sublexical_weight) < 0 or \
                self.core_width < 1 or self.frontier_width < 1:
            raise ValueError("invalid independent search configuration")
        self.byte_cost = {item.fact_id: len(item.text.encode("utf-8")) for item in documents}
        self._query_cache = {}
