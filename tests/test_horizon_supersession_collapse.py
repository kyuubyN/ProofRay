# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Opt-in hard supersession collapse -- excludes superseded value restatements from an already-
verified EvidenceItem pool, never wired into any default routing/ranking path."""
from __future__ import annotations

import hashlib
import inspect
import unittest

from horizon_memory import EvidenceItem, SUPERSESSION_DEFAULT_RELEVANCE_FLOOR, collapse_evidence_items


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

    def test_no_distractor_or_gold_answer_parameter_exists(self):
        params = set(inspect.signature(collapse_evidence_items).parameters)
        self.assertFalse(any("distractor" in p or "gold" in p for p in params))

    def test_default_relevance_floor_is_a_named_constant(self):
        self.assertEqual(SUPERSESSION_DEFAULT_RELEVANCE_FLOOR, 0.0)


if __name__ == "__main__":
    unittest.main()
