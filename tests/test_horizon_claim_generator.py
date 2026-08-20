# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""FH-06.1 -- ClaimGenerator: sentence-level claim candidates, D137/D138-derived scoring."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from horizon_memory import (
    ClaimGenerator, HorizonConfig, HorizonMemory, HorizonVerifier, QueryEnvelope, RouteDocument,
    RouteState, RoutingIndex, SemanticRouter, claim_spans,
)

KEY = b"claim-generator-contract-key-32!"
SCOPE = 11


class ClaimSpansTests(unittest.TestCase):
    def test_splits_on_sentence_boundaries(self):
        spans = claim_spans("First sentence. Second sentence! Third one?")
        text = "First sentence. Second sentence! Third one?"
        surfaces = [text[start:end] for start, end in spans]
        self.assertEqual(surfaces, ["First sentence.", " Second sentence!", " Third one?"])

    def test_does_not_split_decimal_numbers(self):
        # D48/D131 Phase A: the largest single effect-size fix of the whole private research
        # line traced to exactly this -- "3.8x" must stay one claim, not split at the decimal.
        text = "SeqFlow-Net achieves 3.8x faster runtime than baseline. It also uses less memory."
        spans = claim_spans(text)
        surfaces = [text[start:end] for start, end in spans]
        self.assertEqual(len(surfaces), 2)
        self.assertIn("3.8x", surfaces[0])

    def test_empty_text_yields_no_spans(self):
        self.assertEqual(claim_spans(""), ())
        self.assertEqual(claim_spans("   "), ())

    def test_does_not_split_code_punctuation(self):
        # D142 (2026-08-17): the decimal fix above doesn't cover a period followed by a letter
        # (file extensions) or `?.` with no trailing space (code operators) -- both corrupted
        # spans on real dataset_chat programming-domain scenarios before this fix.
        text = ("Setting DATABASE_URL in cargo.toml fixed the connection error. "
               "We also enabled optional chaining (?.) to avoid null pointer bugs.")
        spans = claim_spans(text)
        surfaces = [text[start:end] for start, end in spans]
        self.assertEqual(len(surfaces), 2)
        self.assertIn("cargo.toml", surfaces[0])
        self.assertIn("optional chaining (?.)", surfaces[1])

    def test_consecutive_terminal_quotes_under_segment_by_design(self):
        # Documented trade-off: requiring trailing whitespace after a terminator (needed for the
        # code-punctuation fix above) also means a period directly before a closing quote no
        # longer counts as a terminator -- three sentences each ending in a quoted word collapse
        # into one span instead of into three corrupted fragments. Accepted: not observed in the
        # real MemGym-DR corpus this was validated against.
        text = 'He said "no." She said "yes." They disagreed in the end.'
        spans = claim_spans(text)
        self.assertEqual(len(spans), 1)

    def test_splits_on_cjk_sentence_boundaries(self):
        # CJK terminators (。！？…) used to be entirely invisible to this regex -- a whole
        # multi-sentence Chinese document matched as ONE claim span, defeating this module's own
        # stated purpose of sentence-level (not whole-document) candidates for CJK text
        # (2026-08-19, found via code review, confirmed reproducible).
        text = "北京的地铁系统在2023年运送了超过一百万名乘客。上海的天气今天很好，适合出去散步。"
        spans = claim_spans(text)
        surfaces = [text[start:end] for start, end in spans]
        self.assertEqual(len(surfaces), 2)
        self.assertTrue(surfaces[0].endswith("。"))
        self.assertTrue(surfaces[1].endswith("。"))

    def test_cjk_terminators_do_not_require_trailing_whitespace(self):
        # Unlike ASCII ".", CJK writing has no space after sentence punctuation at all -- a fix
        # that only recognized a CJK terminator when followed by whitespace would never actually
        # terminate anything on real Chinese text.
        text = "第一句。第二句！第三句？"
        spans = claim_spans(text)
        self.assertEqual(len(spans), 3)

    def test_ascii_decimal_and_code_punctuation_protection_unaffected_by_cjk_terminators(self):
        # The CJK terminator addition must not weaken the existing ASCII protections above --
        # re-checked directly rather than assumed safe by construction.
        text = "SeqFlow-Net achieves 3.8x faster runtime than baseline. It also uses less memory."
        spans = claim_spans(text)
        surfaces = [text[start:end] for start, end in spans]
        self.assertEqual(len(surfaces), 2)
        self.assertIn("3.8x", surfaces[0])


class ClaimGeneratorScoringTests(unittest.TestCase):
    def test_rejects_invalid_weights(self):
        for bad_weights in ((0.5, 0.5), (-0.1, 0.5, 0, 0, 0, 0.5), (1, 1, 1, 1, 1)):
            with self.assertRaises(ValueError):
                ClaimGenerator(weights=bad_weights)

    def test_prefers_asserted_claim_over_modal_distractor_with_similar_lexical_overlap(self):
        # Real case that motivated the contradiction weight: a hedged distractor ("might take")
        # shares heavy lexical overlap with the query but is not the direct answer.
        doc = RouteDocument(
            1,
            "Some IKEA coffee tables with storage, like the LACK series, are relatively simple "
            "to assemble and might take around 1-2 hours to put together. I just assembled an "
            "IKEA bookshelf recently and it took me 4 hours, which was longer than expected.",
            1, "s1", 1, "note")
        index = RoutingIndex((doc,))
        query = QueryEnvelope("q1", "How long did it take me to assemble the IKEA bookshelf?",
                              1, "s1", 10)
        result = ClaimGenerator().generate(query, index, 8)
        self.assertGreaterEqual(len(result.candidates), 2)
        top_start, top_end = result.candidates[0].claim_span
        self.assertIn("4 hours", doc.text[top_start:top_end])

    def test_zero_contradiction_weight_no_longer_discriminates_the_same_case(self):
        # Sanity/negative control: without the contradiction weight, the modal distractor's
        # stronger raw lexical overlap can still win -- confirms the weight is doing real work,
        # not a vacuous default.
        doc = RouteDocument(
            1,
            "Some IKEA coffee tables with storage, like the LACK series, are relatively simple "
            "to assemble and might take around 1-2 hours to put together. I just assembled an "
            "IKEA bookshelf recently and it took me 4 hours, which was longer than expected.",
            1, "s1", 1, "note")
        index = RoutingIndex((doc,))
        query = QueryEnvelope("q1", "How long did it take me to assemble the IKEA bookshelf?",
                              1, "s1", 10)
        lexical_only = ClaimGenerator(weights=(1.0, 0.0, 0.0, 0.0, 0.0, 0.0))
        result = lexical_only.generate(query, index, 8)
        top_start, top_end = result.candidates[0].claim_span
        self.assertNotIn("4 hours", doc.text[top_start:top_end])


class ClaimGeneratorRouterIntegrationTests(unittest.TestCase):
    def _memory(self):
        root = Path(tempfile.mkdtemp()) / "hm"
        memory = HorizonMemory.create(HorizonConfig(str(root), SCOPE, KEY))
        memory.put(SCOPE, 1, 1, 10)
        return memory

    def test_end_to_end_through_semantic_router(self):
        memory = self._memory()
        generation = memory.get(SCOPE, 1).generation_id
        index = RoutingIndex((
            RouteDocument(1, "Aldren activates Zephyra directly. Meridian reduces errors by "
                             "18 percent after the rollout.", SCOPE, "s1", 1, "note-a", generation),
        ))
        query = QueryEnvelope("q1", "How does Aldren activate Zephyra?", SCOPE, "s1", 10)
        result = SemanticRouter(index, ClaimGenerator(), HorizonVerifier(memory, index)).route(
            query, 2, allow_scope_fallback=False)
        self.assertEqual(result.state, RouteState.EVIDENCE)
        self.assertTrue(all(item.content_span is not None for item in result.evidence.items))
        self.assertTrue(any("Aldren activates Zephyra" in item.content
                            for item in result.evidence.items))
        memory.close()

    def test_candidates_are_deterministic_and_deduplicated(self):
        memory = self._memory()
        generation = memory.get(SCOPE, 1).generation_id
        index = RoutingIndex((
            RouteDocument(1, "First claim here. Second claim here. Third claim here.",
                          SCOPE, "s1", 1, "note-a", generation),
        ))
        query = QueryEnvelope("q1", "claim", SCOPE, "s1", 10)
        generator = ClaimGenerator()
        first = generator.generate(query, index, 8)
        second = generator.generate(query, index, 8)
        self.assertEqual(first, second)
        identities = [(c.fact_id, c.claim_span) for c in first.candidates]
        self.assertEqual(len(identities), len(set(identities)))
        memory.close()

    def test_no_eligible_documents_yields_empty_candidate_list(self):
        memory = self._memory()
        index = RoutingIndex((
            RouteDocument(1, "irrelevant content", SCOPE, "other-session", 1, "note-a"),
        ))
        query = QueryEnvelope("q1", "query", SCOPE, "s1", 10)
        result = ClaimGenerator().generate(query, index, 8, same_session=True)
        self.assertEqual(result.candidates, ())
        memory.close()


if __name__ == "__main__":
    unittest.main()
