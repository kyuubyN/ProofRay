# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""FH-06.1 — sentence-level claim candidates for the FH-06 router (V25 -> D137/D138 lineage).

Every prior `CandidateGenerator` in `routing.py` (lexical, BM25, dense, causal weave) proposes a
*whole document* as a candidate. `ClaimGenerator` proposes sentence-level spans within eligible
documents instead, each verified by `HorizonVerifier` as an exact substring of its parent
document's own authoritative text (`Candidate.claim_span`, `EvidenceItem.content_span` /
`parent_sha256` -- FH-06.1's only change to the routing contract itself; see `routing.py` and
`evidence.py`).

Why this exists, and why it scores claims the way it does -- this is a direct, zero-LLM port of
the private research line's own D137/D138 findings, not new theory:

- **Sentence-level extraction, not whole-document candidates**, because D133-D137 (private
  research) found whole-document routing under a real byte budget floods `EvidencePack`'s
  budget-fill with a document's own weakest sentences crowding out a different document's
  strongest one -- the fix (D137) was ranking and budgeting at claim granularity, which this
  generator brings natively into FH-06 rather than as an external post-processing step.
- **Lexical + sublexical channel blend, not lexical alone** (`(0.6, 0.4, ...)`), because D138
  found pure lexical (BM25) scoring silently admits almost nothing on paraphrase-heavy
  conversational corpora: ~25% of true-match calibration scores landed at exactly zero (no
  shared lexical token between a question and its own answer-bearing sentence), while blending
  in the sublexical (character n-gram) channel recovered a genuine, non-zero signal for the
  same pairs.
- **A non-zero contradiction weight**, because D138 also found pure lexical/sublexical overlap
  cannot distinguish a hedged distractor from a direct answer when both share heavy topic
  vocabulary (a real case: "IKEA coffee tables... *might take* around 1-2 hours" outranked "I
  ...assembled an IKEA bookshelf... it *took* me 4 hours" on lexical overlap alone). The
  `MaterializedRawCausalSyndromeIndex`'s own `contradiction` channel already penalizes a modal
  claim ("might", "could"...) against a non-modal (asserted-reading) query -- it was simply
  never given a non-zero weight by any existing generator. `DEFAULT_WEIGHTS`' contradiction
  weight (0.5) was chosen by checking it against that exact case (see
  `tests/test_horizon_claim_generator.py`), mirroring how D138's own `asserted_bonus=0.3` was
  calibrated against the same real failure in the private research line's `lab/proof_dossier.py`.

Sentence boundary rule (`_SENTENCE`) matches D48/D131 Phase A exactly: a decimal point inside a
number ("3.8x faster") must not be read as a sentence break -- the single largest effect-size fix
of the private research line's entire D103-D137 run traced to exactly this regex.
"""
from __future__ import annotations

import re

from .materialized_proof_pressure_search import MaterializedRawCausalSyndromeIndex
from .raw_causal_channels import RawCausalDocument
from .routing import Candidate, CandidateGenerator, CandidateList


# D142 (2026-08-17): kept in sync with the identical copy in lab/deterministic_claim_composer.py
# -- generalizes D129 Phase A's decimal-only `\.(?=\d)` protection to any terminator not
# followed by whitespace/EOL (file extensions, code operators like `(?.)`), validated against
# 8,956 real MemGym-DR excerpts with zero fragments worse than the prior regex. See that file's
# own comment for the full rationale and the accepted terminal-quote under-segmentation trade-off.
_SENTENCE = re.compile(r"(?:[^\n.!?]|[.!?](?!\s|\Z))+(?:[.!?]+(?=\s|\Z)|(?=\n|\Z))", re.UNICODE)

# (lexical, sublexical, entity, relation, observable, contradiction)
DEFAULT_WEIGHTS: tuple[float, float, float, float, float, float] = (0.6, 0.4, 0.0, 0.0, 0.0, 0.5)


def claim_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Deterministic sentence-level spans over `text`. Never splits a decimal number."""
    spans = []
    for match in _SENTENCE.finditer(text):
        start, end = match.span()
        if text[start:end].strip():
            spans.append((start, end))
    return tuple(spans)


class ClaimGenerator(CandidateGenerator):
    """FH-06.1: proposes one candidate per sentence-level claim span instead of per document."""
    channel = "claim"

    def __init__(self, weights: tuple[float, float, float, float, float, float] = DEFAULT_WEIGHTS,
                 *, specificity_bonus: float | None = None):
        if len(weights) != 6 or any(weight < 0 for weight in weights):
            raise ValueError("six non-negative channel weights are required")
        self.weights = weights
        # `specificity_bonus` (2026-08-18, opt-in, `None` preserves prior behavior): threads
        # through to `MaterializedRawCausalSyndromeIndex.rank()`'s own new parameter -- see its
        # docstring for what it computes. Not yet validated at this specific call site (candidate
        # ranking, as opposed to the acquisition-budget or final-render stages where the same
        # signal was already tested); exposed as opt-in so a caller can test it without changing
        # default behavior for anything else that constructs `ClaimGenerator`.
        self.specificity_bonus = specificity_bonus

    def generate(self, query, index, limit, same_session=True):
        eligible = index.eligible(query, same_session)
        if not eligible:
            return CandidateList(())
        claims: list[tuple[int, tuple[int, int], str]] = []
        for document in eligible:
            for span in claim_spans(document.text):
                start, end = span
                claims.append((document.fact_id, span, document.text[start:end]))
        if not claims:
            return CandidateList(())
        raw_documents = tuple(
            RawCausalDocument(position, surface, 0, 0)
            for position, (_fact_id, _span, surface) in enumerate(claims))
        claim_index = MaterializedRawCausalSyndromeIndex(raw_documents)
        ranked = claim_index.rank(claim_index.components(query.text), self.weights,
                                  specificity_bonus=self.specificity_bonus)
        namespace = "scope_session" if same_session else "scope_fallback"
        candidates = []
        for rank, row in enumerate(ranked[:limit], 1):
            fact_id, span, _surface = claims[row.fact_id]
            candidates.append(Candidate(fact_id, row.amplitude, self.channel, rank, namespace,
                                        claim_span=span))
        return CandidateList(tuple(candidates))


__all__ = ["ClaimGenerator", "DEFAULT_WEIGHTS", "claim_spans"]
