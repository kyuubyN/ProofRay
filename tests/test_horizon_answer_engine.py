# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""HorizonAnswerEngine: the model-shaped facade over route -> verify -> compose."""
from __future__ import annotations

import unittest

from horizon_memory import (
    DEFAULT_PROFILE, EngineProfile, HorizonAnswerEngine, RouteDocument,
)

SCOPE = 1


def _doc(fact_id: int, text: str) -> RouteDocument:
    return RouteDocument(fact_id, text, SCOPE, "s1", 1, f"doc:{fact_id}")


class ResolvedAnswerTests(unittest.TestCase):
    def setUp(self):
        self.engine = HorizonAnswerEngine(profile=DEFAULT_PROFILE, scope_id=SCOPE)
        self.documents = (
            _doc(1, "The Meridian project reduced compute cost by exactly 42 percent "
                    "compared to the previous baseline architecture across every workload."),
            _doc(2, "Standard atmospheric pressure at sea level is approximately "
                    "one hundred and one thousand three hundred and twenty five pascals."),
            _doc(3, "Meridian's cost reduction came from a redesigned caching layer that "
                    "eliminated redundant recomputation across adjacent pipeline stages."),
        )

    def test_resolves_and_is_verified(self):
        result = self.engine.answer("What percent did the Meridian project reduce cost by?",
                                    self.documents)
        self.assertEqual(result.state, "RESOLVED")
        self.assertTrue(result.resolved)
        self.assertGreater(len(result.claims), 0)
        self.assertGreater(len(result.answer_lines), 0)
        self.assertGreater(len(result.sources), 0)

    def test_answer_lines_are_a_subset_of_claims(self):
        result = self.engine.answer("What percent did the Meridian project reduce cost by?",
                                    self.documents)
        claim_texts = {c.text for c in result.claims}
        for line in result.answer_lines:
            self.assertIn(line.text, claim_texts)

    def test_dossiers_independently_verify_against_sources(self):
        result = self.engine.answer("What percent did the Meridian project reduce cost by?",
                                    self.documents)
        self.assertIsNotNone(result.core_dossier)
        self.assertIsNotNone(result.ranked_dossier)
        self.assertTrue(result.core_dossier.verify(result.sources, DEFAULT_PROFILE.answer_bytes))
        self.assertTrue(
            result.ranked_dossier.verify(result.sources, DEFAULT_PROFILE.acquisition_bytes))

    def test_answer_text_property_joins_answer_lines(self):
        result = self.engine.answer("What percent did the Meridian project reduce cost by?",
                                    self.documents)
        expected = "\n".join(line.text for line in result.answer_lines)
        self.assertEqual(result.answer_text, expected)

    def test_relevant_claim_outranks_unrelated_claim_in_answer(self):
        result = self.engine.answer("What percent did the Meridian project reduce cost by?",
                                    self.documents)
        answer_text = result.answer_text
        self.assertIn("42", answer_text)
        self.assertNotIn("pascals", answer_text)


class AbstentionTests(unittest.TestCase):
    def test_abstains_when_scope_mismatches(self):
        # A document scoped differently from the engine/query can never be verified by
        # `HorizonVerifier` -- `route()` then finds zero verified items and returns ABSTENTION.
        # This is the cleanest reproducible way to exercise the facade's abstain path without
        # depending on ClaimGenerator's own relevance scoring (which returns *some* candidate
        # even for near-zero lexical overlap, so a "weak evidence" document is not guaranteed
        # to abstain at the routing stage).
        engine = HorizonAnswerEngine(profile=DEFAULT_PROFILE, scope_id=SCOPE)
        documents = (RouteDocument(
            1, "Some content the query will never be able to verify against.",
            SCOPE + 1, "s1", 1, "doc:1"),)
        result = engine.answer("What is the answer?", documents)
        self.assertNotEqual(result.state, "RESOLVED")
        self.assertFalse(result.resolved)
        self.assertEqual(result.claims, ())
        self.assertEqual(result.answer_lines, ())
        self.assertEqual(result.sources, ())
        self.assertIsNone(result.core_dossier)
        self.assertIsNone(result.ranked_dossier)
        self.assertEqual(result.answer_bytes, 0)


class ProfileIsRespectedTests(unittest.TestCase):
    def test_tighter_answer_bytes_budget_is_honored(self):
        documents = (
            _doc(1, "The Solstice engine achieves ninety nine percent accuracy on the "
                    "benchmark suite according to the independently reproduced evaluation."),
            _doc(2, "The Solstice engine's accuracy comes from a three-stage verification "
                    "pipeline that cross-checks every candidate answer against its source."),
        )
        tight = EngineProfile(name="tight", answer_bytes=1024, acquisition_bytes=8192)
        engine = HorizonAnswerEngine(profile=tight, scope_id=SCOPE)
        result = engine.answer("What accuracy does the Solstice engine achieve?", documents)
        self.assertEqual(result.state, "RESOLVED")
        self.assertLessEqual(result.answer_bytes, tight.answer_bytes)


if __name__ == "__main__":
    unittest.main()
