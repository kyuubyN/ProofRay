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

import dataclasses
import unittest

from horizon_memory import DEFAULT_PROFILE, HorizonAnswerEngine, PERSONAL_MEMORY_PROFILE, RouteDocument

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


class CompletenessBonusRegressionTests(unittest.TestCase):
    """`answer_completeness_bonus` opt-in fix (2026-08-23) -- found on two fresh external
    HuggingFace corpora (a Brazilian-Portuguese legislative/news corpus and an English
    public-domain-fiction corpus split into fixed-length windows): `_pick_clean_answer`'s tiered
    fallback can exclude the single highest-relevance claim outright, purely for not "looking like
    a complete sentence" (starting lowercase / lacking a trailing period, as a genuine dialogue or
    windowed-text fragment routinely does), and never falls back to a looser tier because the
    stricter tier already returned something non-empty from a much less relevant, but
    complete-looking, claim.
    """

    def setUp(self):
        self.fragment_doc = _doc(
            1, "the Zorbex compound reacts violently with chlorine gas at room "
               "temperature, the Zorbex compound reacts violently with chlorine gas "
               "at room temperature")
        self.complete_but_generic_doc = _doc(
            2, "The laboratory safety manual describes general storage procedures "
               "for various chemical compounds kept in the facility.")
        self.documents = (self.fragment_doc, self.complete_but_generic_doc)
        self.question = "How does the Zorbex compound react with chlorine gas?"

    def test_default_profile_reproduces_the_known_gap(self):
        # DEFAULT_PROFILE leaves `answer_completeness_bonus=None` -- the historical tiered
        # cascade stays byte-for-byte unchanged, so this specific known gap still reproduces here.
        # This test exists to catch an accidental behavior change to the *default* path, not to
        # bless the gap itself.
        engine = HorizonAnswerEngine(profile=DEFAULT_PROFILE, scope_id=SCOPE)
        result = engine.answer(self.question, self.documents)
        self.assertEqual(result.state, "RESOLVED")
        texts = [line.text for line in result.answer_lines]
        self.assertFalse(any("Zorbex" in t for t in texts),
                          "DEFAULT_PROFILE's behavior changed -- update this test deliberately, "
                          "not by accident, if the tiered cascade itself was intentionally revised")

    def test_completeness_bonus_recovers_the_highest_relevance_fragment(self):
        profile = dataclasses.replace(
            DEFAULT_PROFILE, name="completeness-bonus-test", answer_completeness_bonus=0.5)
        engine = HorizonAnswerEngine(profile=profile, scope_id=SCOPE)
        result = engine.answer(self.question, self.documents)
        self.assertEqual(result.state, "RESOLVED")
        texts = [line.text for line in result.answer_lines]
        self.assertTrue(any("Zorbex" in t for t in texts),
                         "the highest-relevance claim should win once completeness is a bonus, "
                         "not a hard gate")

    def test_completeness_bonus_still_respects_the_relevance_gate(self):
        # A tight relevance gate (DEFAULT_PROFILE's own 0.3) must still exclude a genuinely
        # irrelevant claim even under the new selector -- the fix must not turn into "always
        # include everything regardless of relevance."
        irrelevant_doc = _doc(
            3, "A totally unrelated municipal water treatment report describes "
               "infrastructure upgrades scheduled for next fiscal year.")
        profile = dataclasses.replace(
            DEFAULT_PROFILE, name="completeness-bonus-gate-test", answer_completeness_bonus=0.5)
        engine = HorizonAnswerEngine(profile=profile, scope_id=SCOPE)
        result = engine.answer(
            self.question, (self.fragment_doc, self.complete_but_generic_doc, irrelevant_doc))
        texts = [line.text for line in result.answer_lines]
        self.assertFalse(any("water treatment" in t for t in texts),
                          "an irrelevant claim must still be excluded by the relevance gate")

    def test_bonus_none_is_the_dataclass_default(self):
        # Every named profile constant must default this field to None unless a deployment
        # explicitly opts in -- guards against a future edit accidentally flipping the default.
        self.assertIsNone(DEFAULT_PROFILE.answer_completeness_bonus)


class ParagraphContextRegressionTests(unittest.TestCase):
    def test_personal_profile_preserves_cross_line_attribution_in_one_exact_span(self):
        documents = (
            _doc(1, "A political and cultural history of\nmodern Europe. SEE Hayes,\n"
                    "Carlton J. H.\n\nA separate catalogue record."),
            _doc(2, "A modern municipal history describes cultural programs in Europe."),
        )
        result = HorizonAnswerEngine(
            profile=PERSONAL_MEMORY_PROFILE, scope_id=SCOPE).answer(
                "Who wrote the political and cultural history of modern Europe?", documents)
        self.assertTrue(any(
            "Carlton J. H." in line.text and line.fact_id == 1
            for line in result.answer_lines))
        ranked_surfaces = [claim.surface for claim in result.ranked_dossier.claims]
        self.assertEqual(
            len(ranked_surfaces), len(set(ranked_surfaces)),
            "nested paragraph/sentence representations must not create duplicate evidence",
        )

    def test_default_profile_keeps_paragraph_context_opt_in(self):
        self.assertFalse(DEFAULT_PROFILE.claim_paragraph_context)

    def test_verified_paragraph_relevance_survives_modal_narrative_wording(self):
        documents = (
            _doc(1, "Written by the authors, the song tells of a man who didn't want to fall "
                    "in love, only to learn that he was in love with his former girlfriend: "
                    "if it isn't love, why does she stay on his mind?\n\n"
                    "The later chorus repeats the dilemma."),
            _doc(2, "The song has a memorable breakdown where the singer plainly admits a "
                    "mistake, while the band answers in the background."),
        )
        result = HorizonAnswerEngine(
            profile=PERSONAL_MEMORY_PROFILE, scope_id=SCOPE).answer(
                "What is the story of the song If It Isn't Love?", documents)
        self.assertTrue(any(
            line.fact_id == 1 and "former girlfriend" in line.text
            for line in result.answer_lines),
            "routed paragraph relevance must participate in the dossier merge",
        )

    def test_sublexical_acronym_route_survives_empty_lexical_max_cover(self):
        documents = (
            _doc(1, "The Financial Services Authority of the UK performs the corresponding "
                    "financial regulatory role for securities markets."),
            _doc(2, "The municipal services authority publishes annual road maintenance data."),
        )
        result = HorizonAnswerEngine(
            profile=PERSONAL_MEMORY_PROFILE, scope_id=SCOPE).answer(
                "What is the UK equivalent of the SEC?", documents)
        self.assertEqual(result.state, "RESOLVED")
        self.assertTrue(any(
            line.fact_id == 1 and "Financial Services Authority" in line.text
            for line in result.answer_lines),
            "verified sublexical evidence must survive an empty lexical max-cover core",
        )


if __name__ == "__main__":
    unittest.main()
