# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression coverage for the clean-answer selection fix in `answer_engine.py`.

Real-world motivation (MemGym-DR ordinal 382, found 2026-08-19): the pre-fix greedy formula
`len(new_words) * (0.3 + relevance)` let a long, low-relevance sentence about a system called
"UCEF" (45 new words, relevance 0.583, gain 39.7) outrank the single most relevant claim in the
whole pool -- a short sentence correctly about "BARM" (14 new words, relevance 0.991, gain 18.1).
The fix gates candidates by relevance, computed once from the top of the sorted shortlist, before
the greedy diversity pick ever runs.
"""
from __future__ import annotations

import unittest

from horizon_memory import DEFAULT_PROFILE, HorizonAnswerEngine, RouteDocument

SCOPE = 1


def _doc(fact_id: int, text: str) -> RouteDocument:
    return RouteDocument(fact_id, text, SCOPE, "s1", 1, f"doc:{fact_id}")


class RelevanceGateRegressionTests(unittest.TestCase):
    """Reproduces the BARM/UCEF relevance/word-count shape with synthetic claims."""

    def setUp(self):
        self.engine = HorizonAnswerEngine(profile=DEFAULT_PROFILE, scope_id=SCOPE)
        # Short, highly specific, highest-relevance claim -- the correct answer.
        self.on_topic = (
            "BARM adopts a Bayesian inference-based framework with priors and optional "
            "correlation structures for the estimation task described in the question.")
        # Long, low-relevance claim about a different system that shares only incidental
        # vocabulary with the question -- engineered to have a high raw new-word count so it
        # would have won under the pre-fix formula.
        self.off_topic_but_wordy = (
            "Using a bi-convex continuous prediction based extended likelihood and "
            "spike-and-exponential priors, researchers separately develop an entirely "
            "different algorithm called Unified Covariance Estimation Framework for joint "
            "continuous prediction and covariance selection across unrelated benchmark suites, "
            "achieving several magnitudes faster throughput than competing approaches.")
        self.documents = (
            _doc(1, self.on_topic),
            _doc(2, self.off_topic_but_wordy),
            _doc(3, "The regional climate report for the coastal district indicates "
                    "above-average rainfall throughout the observed measurement period."),
        )

    def test_highest_relevance_claim_wins_the_clean_answer(self):
        result = self.engine.answer(
            "What framework does BARM adopt for its estimation task?", self.documents)
        self.assertEqual(result.state, "RESOLVED")
        self.assertTrue(any("BARM" in line.text for line in result.answer_lines))

    def test_low_relevance_wordy_claim_does_not_win_alone(self):
        result = self.engine.answer(
            "What framework does BARM adopt for its estimation task?", self.documents)
        # The wordy off-topic claim must not be the ONLY thing in the clean answer -- if BARM's
        # own claim is excluded while the low-relevance claim is kept, the fix has regressed.
        texts = [line.text for line in result.answer_lines]
        if any("Unified Covariance Estimation Framework" in t for t in texts):
            self.assertTrue(any("BARM" in t for t in texts),
                            "UCEF-style claim present without the higher-relevance BARM claim")

    def test_full_claim_pool_still_contains_both(self):
        # The fix only changes the *clean answer* selection -- the full verified pool (what a
        # caller sees via `include_sources`/`claims`) must still contain everything routing and
        # verification found, unfiltered by the relevance gate.
        result = self.engine.answer(
            "What framework does BARM adopt for its estimation task?", self.documents)
        all_text = " ".join(c.text for c in result.claims)
        self.assertIn("BARM", all_text)
        self.assertIn("Unified Covariance Estimation Framework", all_text)


class AdaptiveLengthTests(unittest.TestCase):
    """The clean answer is no longer a hardcoded sentence count (the old code capped it at
    exactly `ANSWER_SENTENCES=4` regardless of how much relevant content existed) -- length
    should track how much genuinely distinct, relevant content a question's evidence supports."""

    def test_many_equally_relevant_claims_exceed_the_old_fixed_cap_of_four(self):
        engine = HorizonAnswerEngine(profile=DEFAULT_PROFILE, scope_id=SCOPE)
        documents = tuple(
            _doc(i, f"The Kestrel measurement axis number {word} records a distinct "
                    f"property of the Kestrel benchmark suite that no other axis "
                    f"captures, specifically axis {word}.")
            for i, word in enumerate(
                ["one", "two", "three", "four", "five", "six", "seven"], start=1))
        result = engine.answer("What are all of the distinct Kestrel measurement axes?",
                               documents)
        self.assertEqual(result.state, "RESOLVED")
        self.assertGreater(len(result.answer_lines), 4,
                           "seven equally relevant, mutually distinct claims should not be "
                           "truncated to the old hardcoded four-sentence cap")

    def test_single_relevant_claim_does_not_pad_to_four(self):
        engine = HorizonAnswerEngine(profile=DEFAULT_PROFILE, scope_id=SCOPE)
        documents = (
            _doc(1, "The Kestrel measurement axis number three records a distinct "
                    "property of the Kestrel benchmark suite that no other axis "
                    "captures at all."),
            _doc(2, "Unrelated notes on regional agricultural yield forecasts for the "
                    "upcoming growing season across several distinct farming "
                    "cooperatives."),
            _doc(3, "A separate report on municipal water treatment infrastructure "
                    "upgrades scheduled for the next fiscal year across multiple "
                    "districts."),
            _doc(4, "Historical archive records describing nineteenth century "
                    "maritime trade routes between several coastal port cities in "
                    "the region."),
        )
        result = engine.answer("What is Kestrel measurement axis three?", documents)
        self.assertEqual(result.state, "RESOLVED")
        self.assertEqual(len(result.answer_lines), 1,
                         "a question with exactly one genuinely relevant claim should not "
                         "be padded with unrelated content to reach a fixed count")


if __name__ == "__main__":
    unittest.main()
