# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""EngineProfile: the versioned, swappable "weights" bundle for HorizonAnswerEngine."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from horizon_memory import (
    CONVERSATIONAL_HIGH_RECALL_PROFILE, DEFAULT_PROFILE, PERSONAL_MEMORY_PROFILE,
    TEAM_MEMORY_PROFILE, EngineProfile,
)
from horizon_memory.claim_routing import DEFAULT_WEIGHTS as CLAIM_GENERATOR_DEFAULT_WEIGHTS
from horizon_memory.conformal_routing import LEXICAL_SUBLEXICAL_WEIGHTS


class DefaultProfileTests(unittest.TestCase):
    def test_default_profile_matches_published_memgym_dr_constants(self):
        # The exact values the published 0.95 judge-score result (MemGym-DR) was measured at.
        self.assertEqual(DEFAULT_PROFILE.acquisition_bytes, 65_536)
        self.assertEqual(DEFAULT_PROFILE.answer_bytes, 24_576)
        self.assertEqual(DEFAULT_PROFILE.per_fiber, 64)
        self.assertEqual(DEFAULT_PROFILE.global_sort_alpha, 0.3)
        self.assertEqual(DEFAULT_PROFILE.anchor_bonus, 0.3)
        self.assertEqual(DEFAULT_PROFILE.specificity_bonus, 0.5)
        # claim_limit raised 800 -> 8,192 (2026-08-26): the old ceiling silently truncated the
        # candidate pool on a large-corpus benchmark before the answer selector could act on the
        # full pool; see EngineProfile's own field comment for the fresh evidence behind this.
        self.assertEqual(DEFAULT_PROFILE.claim_limit, 8_192)
        self.assertEqual(DEFAULT_PROFILE.claim_weights, CLAIM_GENERATOR_DEFAULT_WEIGHTS)
        self.assertEqual(DEFAULT_PROFILE.conformal_weights, LEXICAL_SUBLEXICAL_WEIGHTS)
        self.assertEqual(DEFAULT_PROFILE.lexical_bm25_delta, 0.0)
        self.assertEqual(DEFAULT_PROFILE.sublexical_bm25_delta, 0.0)
        self.assertEqual(DEFAULT_PROFILE.answer_selector, "diversity")
        self.assertEqual(DEFAULT_PROFILE.hpps_max_results, 3)
        self.assertEqual(DEFAULT_PROFILE.hpps_exploration_reserve, 0)

    def test_default_profile_is_valid(self):
        EngineProfile()  # must not raise


class ValidationTests(unittest.TestCase):
    def test_rejects_unsupported_schema(self):
        with self.assertRaises(ValueError):
            EngineProfile(schema="engine-profile.v0")

    def test_rejects_empty_name(self):
        with self.assertRaises(ValueError):
            EngineProfile(name="")

    def test_rejects_wrong_length_claim_weights(self):
        with self.assertRaises(ValueError):
            EngineProfile(claim_weights=(0.5, 0.5))

    def test_rejects_negative_claim_weight(self):
        with self.assertRaises(ValueError):
            EngineProfile(claim_weights=(-0.1, 0.4, 0.0, 0.0, 0.0, 0.5))

    def test_rejects_wrong_length_conformal_weights(self):
        with self.assertRaises(ValueError):
            EngineProfile(conformal_weights=(0.5,))

    def test_rejects_negative_claim_specificity_bonus(self):
        with self.assertRaises(ValueError):
            EngineProfile(claim_specificity_bonus=-0.1)

    def test_rejects_invalid_bm25_params(self):
        with self.assertRaises(ValueError):
            EngineProfile(bm25_k1=0.0)
        with self.assertRaises(ValueError):
            EngineProfile(bm25_b=1.5)
        with self.assertRaises(ValueError):
            EngineProfile(lexical_bm25_delta=-0.1)

    def test_rejects_non_positive_claim_limit(self):
        with self.assertRaises(ValueError):
            EngineProfile(claim_limit=0)

    def test_rejects_tiny_byte_budgets(self):
        with self.assertRaises(ValueError):
            EngineProfile(acquisition_bytes=100)
        with self.assertRaises(ValueError):
            EngineProfile(answer_bytes=100)

    def test_rejects_answer_bytes_exceeding_acquisition_bytes(self):
        with self.assertRaises(ValueError):
            EngineProfile(acquisition_bytes=4096, answer_bytes=8192)

    def test_rejects_non_positive_per_fiber(self):
        with self.assertRaises(ValueError):
            EngineProfile(per_fiber=0)

    def test_rejects_out_of_range_global_sort_alpha(self):
        with self.assertRaises(ValueError):
            EngineProfile(global_sort_alpha=1.5)
        with self.assertRaises(ValueError):
            EngineProfile(global_sort_alpha=-0.1)

    def test_rejects_negative_anchor_or_specificity_bonus(self):
        with self.assertRaises(ValueError):
            EngineProfile(anchor_bonus=-0.1)
        with self.assertRaises(ValueError):
            EngineProfile(specificity_bonus=-0.1)

    def test_rejects_out_of_range_dedup_threshold(self):
        with self.assertRaises(ValueError):
            EngineProfile(dedup_threshold=1.5)

    def test_rejects_non_positive_shortlist_size(self):
        with self.assertRaises(ValueError):
            EngineProfile(answer_shortlist_size=0)

    def test_rejects_invalid_answer_selector(self):
        with self.assertRaises(ValueError):
            EngineProfile(answer_selector="oracle")
        with self.assertRaises(ValueError):
            EngineProfile(hpps_max_results=0)
        with self.assertRaises(ValueError):
            EngineProfile(hpps_max_results=3, hpps_exploration_reserve=-1)
        with self.assertRaises(ValueError):
            EngineProfile(hpps_max_results=3, hpps_exploration_reserve=4)

    def test_rejects_out_of_range_gate_ratio(self):
        with self.assertRaises(ValueError):
            EngineProfile(answer_relevance_gate_ratio=1.5)
        with self.assertRaises(ValueError):
            EngineProfile(answer_relevance_gate_ratio=-0.1)

    def test_rejects_negative_completeness_bonus(self):
        with self.assertRaises(ValueError):
            EngineProfile(answer_completeness_bonus=-0.1)

    def test_completeness_bonus_defaults_to_none(self):
        self.assertIsNone(EngineProfile().answer_completeness_bonus)
        EngineProfile(answer_completeness_bonus=0.0)  # zero is a valid, explicit opt-in

    def test_rejects_empty_length_tiers(self):
        with self.assertRaises(ValueError):
            EngineProfile(answer_min_length_tiers=())

    def test_rejects_non_positive_tier_length(self):
        with self.assertRaises(ValueError):
            EngineProfile(answer_min_length_tiers=((0, True),))


class SerializationTests(unittest.TestCase):
    def test_round_trip_through_dict(self):
        profile = EngineProfile(name="custom", anchor_bonus=0.4, per_fiber=32)
        restored = EngineProfile.from_dict(profile.to_dict())
        self.assertEqual(profile, restored)

    def test_round_trip_through_file(self):
        profile = EngineProfile(name="custom-file", global_sort_alpha=0.25)
        with tempfile.TemporaryDirectory() as workdir:
            path = Path(workdir) / "profile.json"
            profile.save(path)
            restored = EngineProfile.load(path)
        self.assertEqual(profile, restored)

    def test_saved_file_is_readable_json_with_sorted_keys(self):
        with tempfile.TemporaryDirectory() as workdir:
            path = Path(workdir) / "profile.json"
            DEFAULT_PROFILE.save(path)
            text = path.read_text(encoding="utf-8")
        self.assertIn('"schema"', text)
        self.assertIn('"answer_relevance_gate_ratio"', text)


class NamedPresetTests(unittest.TestCase):
    """`TEAM_MEMORY_PROFILE`/`PERSONAL_MEMORY_PROFILE` -- named deployment presets found
    2026-08-22 against two real MongoDB-backed corpora. Neither is an automatic detector (corpus
    size was found NOT to reliably separate "safe to loosen" from "needs the tight defaults" --
    a real technical-QA corpus's own candidate-pool size overlapped with a real MemGym-DR
    episode's) -- both are deliberate, named choices a deployment picks for itself."""

    def test_presets_are_valid(self):
        EngineProfile(**{**CONVERSATIONAL_HIGH_RECALL_PROFILE.to_dict()})
        EngineProfile(**{**TEAM_MEMORY_PROFILE.to_dict()})  # must not raise
        EngineProfile(**{**PERSONAL_MEMORY_PROFILE.to_dict()})  # must not raise

    def test_conversational_high_recall_preserves_24k_and_freezes_cut_64(self):
        self.assertEqual(CONVERSATIONAL_HIGH_RECALL_PROFILE.claim_limit, 64)
        self.assertEqual(CONVERSATIONAL_HIGH_RECALL_PROFILE.answer_bytes, 24_576)
        self.assertEqual(CONVERSATIONAL_HIGH_RECALL_PROFILE.acquisition_bytes, 65_536)
        # This preset's own frozen 64-cut is independent of DEFAULT_PROFILE's own claim_limit
        # (raised to 8,192, 2026-08-26); assert only that they remain distinct values.
        self.assertNotEqual(CONVERSATIONAL_HIGH_RECALL_PROFILE.claim_limit,
                            DEFAULT_PROFILE.claim_limit)

    def test_team_memory_is_a_real_middle_ground_not_default_or_personal(self):
        self.assertLess(TEAM_MEMORY_PROFILE.answer_relevance_gate_ratio,
                        DEFAULT_PROFILE.answer_relevance_gate_ratio)
        self.assertGreater(TEAM_MEMORY_PROFILE.answer_relevance_gate_ratio,
                           PERSONAL_MEMORY_PROFILE.answer_relevance_gate_ratio)
        self.assertGreater(TEAM_MEMORY_PROFILE.answer_shortlist_size,
                           DEFAULT_PROFILE.answer_shortlist_size)
        self.assertLess(TEAM_MEMORY_PROFILE.answer_shortlist_size,
                       PERSONAL_MEMORY_PROFILE.answer_shortlist_size)

    def test_personal_memory_matches_the_validated_mongo_configuration(self):
        # The exact values 31/32, 19/20 and 12/12 real questions were measured at.
        self.assertEqual(PERSONAL_MEMORY_PROFILE.answer_relevance_gate_ratio, 0.0)
        self.assertEqual(PERSONAL_MEMORY_PROFILE.answer_shortlist_size, 500)
        self.assertEqual(PERSONAL_MEMORY_PROFILE.answer_bytes, 40_000)

    def test_default_profile_is_unaffected_by_the_new_presets(self):
        # `DEFAULT_PROFILE` must stay exactly what the published D144/LongMemEval byte-identical
        # reproductions depend on -- introducing named presets must never touch it.
        self.assertEqual(DEFAULT_PROFILE.answer_shortlist_size, 50)
        self.assertEqual(DEFAULT_PROFILE.answer_relevance_gate_ratio, 0.3)
        self.assertEqual(DEFAULT_PROFILE.answer_bytes, 24_576)
        self.assertIsNone(DEFAULT_PROFILE.answer_completeness_bonus)

    def test_team_and_personal_memory_carry_the_calibrated_completeness_bonus(self):
        # Added 2026-08-23 -- calibrated on a disjoint half of two fresh external HuggingFace
        # corpora, reconfirmed on the other, held-out half; see engine_profile.py's own comment
        # and docs/BENCHMARKS.md for the full numbers.
        self.assertEqual(TEAM_MEMORY_PROFILE.answer_completeness_bonus, 0.5)
        self.assertEqual(PERSONAL_MEMORY_PROFILE.answer_completeness_bonus, 0.5)

    def test_presets_round_trip_through_file(self):
        for profile in (TEAM_MEMORY_PROFILE, PERSONAL_MEMORY_PROFILE):
            with tempfile.TemporaryDirectory() as workdir:
                path = Path(workdir) / "profile.json"
                profile.save(path)
                restored = EngineProfile.load(path)
            self.assertEqual(profile, restored)


if __name__ == "__main__":
    unittest.main()
