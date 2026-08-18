# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Opt-in hard supersession collapse -- excludes superseded value restatements from an already-
verified EvidenceItem pool, never wired into any default routing/ranking path."""
from __future__ import annotations

import hashlib
import inspect
import unittest

from horizon_memory import EvidenceItem
from horizon_memory.research import SUPERSESSION_DEFAULT_RELEVANCE_FLOOR, collapse_evidence_items


def _item(fact_id: int, text: str, source: str = "s") -> EvidenceItem:
    digest = hashlib.sha256(text.encode()).hexdigest()
    return EvidenceItem(fact_id, source, 1, 1, content=text, verifier_state="verified",
                        content_span=(0, len(text)), parent_sha256=digest)


class SupersessionCollapseTests(unittest.TestCase):
    def test_collapse_keeps_only_the_latest_restated_value(self):
        items = (
            _item(1, "The launch will happen on day 10, the team confirmed."),
            _item(2, "The launch was delayed to day 15 due to a server problem."),
            _item(3, "The final launch date is day 25, confirmed by the whole team."),
        )
        kept, report = collapse_evidence_items(items, "What is the final launch date?")
        self.assertGreaterEqual(report.groups_detected, 1)
        self.assertGreaterEqual(report.resolved_groups, 1)
        kept_ids = {item.fact_id for item in kept}
        self.assertIn(3, kept_ids)
        self.assertNotIn(1, kept_ids)
        self.assertNotIn(2, kept_ids)
        self.assertEqual(report.superseded_keys,
                         frozenset({(1, items[0].content_span), (2, items[1].content_span)}))

    def test_no_group_when_only_one_relevant_item(self):
        items = (_item(1, "The final launch date is day 25, confirmed by the team."),)
        kept, report = collapse_evidence_items(items, "What is the final launch date?")
        self.assertEqual(report.groups_detected, 0)
        self.assertEqual(kept, items)

    def test_modal_restatement_does_not_supersede_an_asserted_value(self):
        items = (
            _item(1, "The final launch date is day 25, confirmed by the whole team."),
            _item(2, "The launch date might possibly move to day 30 next quarter."),
        )
        kept, report = collapse_evidence_items(items, "What is the final launch date?")
        self.assertEqual(report.superseded_keys, frozenset())
        self.assertEqual({item.fact_id for item in kept}, {1, 2})

    def test_conflicting_same_item_restatement_abstains(self):
        items = (
            _item(1, "The final launch date is day 25 according to the memo, "
                     "but the final launch date is day 30 according to the calendar invite."),
        )
        kept, report = collapse_evidence_items(items, "What is the final launch date?")
        self.assertEqual(report.superseded_keys, frozenset())
        self.assertEqual(kept, items)

    def test_unrelated_context_item_is_never_excluded(self):
        items = (
            _item(1, "The launch will happen on day 10, the team confirmed."),
            _item(2, "The final launch date is day 25, confirmed by the whole team."),
            _item(3, "The catering budget for the launch party remains unchanged."),
        )
        kept, report = collapse_evidence_items(items, "What is the final launch date?")
        kept_ids = {item.fact_id for item in kept}
        self.assertIn(3, kept_ids)
        self.assertNotIn((3, items[2].content_span), report.superseded_keys)

    def test_unverified_items_are_never_considered(self):
        text = "The final launch date is day 25, confirmed by the whole team."
        unverified = EvidenceItem(1, "s", 1, 1, content=text, verifier_state="unverified")
        items = (
            unverified,
            _item(2, "The launch will happen on day 10, the team confirmed."),
        )
        kept, report = collapse_evidence_items(items, "What is the final launch date?")
        self.assertEqual(report.groups_detected, 0)
        self.assertEqual(kept, items)

    def test_cjk_text_detects_and_resolves_a_group(self):
        # CJK has no letter-casing, so a capitalization-based entity/anchor signal is
        # structurally blind to it -- this exercises the word-segmentation anchor path instead
        # (see test_zh_segmentation_only_counts_real_dictionary_words below for the segmenter in
        # isolation). Sentences kept longer than the short-example version tried first: with
        # only a 745-word calibration dictionary, a short sentence can easily contain zero
        # dictionary words at all -- these specific sentences were confirmed to carry enough
        # real dictionary anchors ("会议"/"决定"/"最终"/"计划"/"北京") for both claims.
        items = (
            _item(1, "我们计划在北京举行这次重要会议，大家都同意了。"),
            _item(2, "经过反复讨论，最终决定改在上海举行这次重要会议。"),
        )
        kept, report = collapse_evidence_items(items, "最终决定在哪里举行这次会议？")
        self.assertEqual(report.groups_detected, 1)
        self.assertEqual(report.resolved_groups, 1)
        self.assertEqual({item.fact_id for item in kept}, {2})

    def test_zh_segmentation_only_counts_real_dictionary_words(self):
        # D142 (2026-08-18): regression test for the word-segmentation redesign. "北京" (Beijing)
        # is a real, calibrated dictionary word and must survive as a multi-character anchor; no
        # single leftover character should ever appear as an anchor on its own -- that
        # combinatorial-saturation failure mode (raw character bigrams) is exactly what this
        # redesign replaced. See RESEARCH.md for the full empirical trace.
        from horizon_memory.supersession_collapse import _cjk_anchors

        anchors = _cjk_anchors("我们计划在北京举行这次重要会议。")
        self.assertIn("北京", anchors)
        self.assertIn("会议", anchors)
        self.assertFalse(any(len(a) == 1 for a in anchors))

    def test_no_distractor_or_gold_answer_parameter_exists(self):
        params = set(inspect.signature(collapse_evidence_items).parameters)
        self.assertFalse(any("distractor" in p or "gold" in p for p in params))

    def test_default_relevance_floor_is_a_named_constant(self):
        self.assertEqual(SUPERSESSION_DEFAULT_RELEVANCE_FLOOR, 0.0)


if __name__ == "__main__":
    unittest.main()
