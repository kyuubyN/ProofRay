# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Opt-in pragmatic negation / sarcasm detection -- never wired into any default routing/
ranking/ingestion path. See the module's own docstring for the design history: a first,
externally-reviewed version scored "positive word + negative word anywhere in the text" as
sarcasm and failed 4/7 of an independently-constructed adversarial set (flagging ordinary
adversity-then-recovery narratives as sarcasm). The fix -- same-referent scope awareness, not
more lexicon -- is what this test suite exists to lock in."""
from __future__ import annotations

import unittest

from horizon_memory.research import detect_pragmatic_negation


class SincereAdversityRecoveryTests(unittest.TestCase):
    """The exact failure class the original (rejected) design got wrong: a positive word on the
    OTHER side of a contrastive/concessive marker from a negative word describes the response to
    a problem, not the problem itself -- sincere narrative structure, not sarcasm."""

    def test_but_coordinated_recovery_is_not_sarcastic(self):
        result = detect_pragmatic_negation(
            "The deploy crashed at first, but the team recovered brilliantly and fixed it "
            "in an hour.")
        self.assertFalse(result.is_sarcastic)

    def test_however_coordinated_recovery_is_not_sarcastic(self):
        result = detect_pragmatic_negation(
            "There was a bug in production, however the fix shipped in record time -- "
            "great work.")
        self.assertFalse(result.is_sarcastic)

    def test_concessive_marker_with_intervening_negative_clause_is_not_sarcastic(self):
        # "Apesar de X, Y" -- X (negative) and Y (positive) are different referents even though
        # both technically fall "after" the marker in a naive before/after split.
        result = detect_pragmatic_negation(
            "Apesar do erro inicial, a equipe fez um trabalho excelente para resolver "
            "tudo rápido.")
        self.assertFalse(result.is_sarcastic)

    def test_portuguese_mas_coordinated_recovery_is_not_sarcastic(self):
        result = detect_pragmatic_negation(
            "O sistema travou de manhã, mas o suporte foi incrível e resolveu em minutos.")
        self.assertFalse(result.is_sarcastic)

    def test_plain_negative_statement_is_not_sarcastic(self):
        result = detect_pragmatic_negation("O servidor caiu de novo e ninguém sabe por quê.")
        self.assertFalse(result.is_sarcastic)

    def test_plain_positive_statement_is_not_sarcastic(self):
        result = detect_pragmatic_negation(
            "The project is going great, the whole team is thrilled with the results.")
        self.assertFalse(result.is_sarcastic)


class SameReferentDisparityTests(unittest.TestCase):
    """No contrastive/concessive marker separates the positive word from the negative event it
    modifies -- the positive word evaluates the SAME thing the negative word describes, the real
    echoic-mention signature of irony."""

    def test_positive_word_directly_praising_a_failure_is_sarcastic(self):
        result = detect_pragmatic_negation(
            "Great, the server crashed again, real professional work there.")
        self.assertTrue(result.is_sarcastic)
        self.assertEqual(result.reason, "same_referent_disparity")

    def test_portuguese_direct_praise_of_a_failure_is_sarcastic(self):
        result = detect_pragmatic_negation(
            "Ótimo, caiu de novo bem na hora da demo. Show de bola.")
        self.assertTrue(result.is_sarcastic)

    def test_no_marker_needed_when_disparity_is_in_the_same_clause(self):
        result = detect_pragmatic_negation(
            "Nada como debugar em produção numa sexta às 18h, adoro viver perigosamente.")
        self.assertTrue(result.is_sarcastic)


class ExplicitMarkerAndEmojiTests(unittest.TestCase):
    def test_explicit_inversion_phrase_is_sarcastic(self):
        result = detect_pragmatic_negation("Yeah right, that's totally going to work.")
        self.assertTrue(result.is_sarcastic)
        self.assertEqual(result.reason, "explicit_marker")

    def test_portuguese_explicit_inversion_phrase_is_sarcastic(self):
        result = detect_pragmatic_negation("Aham sei, confia que vai dar certo dessa vez.")
        self.assertTrue(result.is_sarcastic)

    def test_incongruent_emoji_with_lexicon_hit_is_sarcastic(self):
        result = detect_pragmatic_negation("The deploy went great 🙃 nothing broke at all 🙃")
        self.assertTrue(result.is_sarcastic)
        self.assertEqual(result.reason, "incongruent_emoji")

    def test_bare_emoji_with_no_lexicon_hit_is_not_sarcastic(self):
        # An ironic-shaped emoji alone, with nothing in either lexicon, is too weak a signal on
        # its own -- confirms the emoji path requires an actual lexicon hit, not just presence.
        result = detect_pragmatic_negation("See you at the meeting tomorrow 🙄")
        self.assertFalse(result.is_sarcastic)


class NegatedNegativeMaskingTests(unittest.TestCase):
    def test_zero_bugs_is_a_success_metric_not_a_negative_situation(self):
        result = detect_pragmatic_negation(
            "Great news, zero reported bugs in this release, the team did an amazing job.")
        self.assertFalse(result.is_sarcastic)


if __name__ == "__main__":
    unittest.main()
