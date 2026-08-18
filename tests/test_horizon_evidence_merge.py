# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""EvidencePack.budgeted_items(global_sort_alpha=..., source_priority=..., dedup_threshold=...) --
D137 Variant 3 / D135 merge-layer ports onto the core's own byte-budget-fill step."""
from __future__ import annotations

import unittest

from horizon_memory import EvidenceItem, EvidencePack


def _pack(items):
    return EvidencePack.build("q1", items, generation_id=None, recovery_reason="bulk")


class RelevanceScoreDefaultTests(unittest.TestCase):
    def test_relevance_score_defaults_to_none(self):
        item = EvidenceItem(1, "docA", 1, None, content="hello")
        self.assertIsNone(item.relevance_score)


class GlobalSortAlphaTests(unittest.TestCase):
    """`budgeted_items()` always RETURNS the chosen subset in canonical pack order (documented,
    unchanged behavior) -- `global_sort_alpha` only changes which items are eligible to fit under
    a tight budget, not the order of the returned tuple. Every test here therefore uses a budget
    that fits exactly one of two equal-sized blocks, so the surviving fact_id reveals which item
    the merge order actually admitted first."""

    def _equal_block_items(self, retrieval_rank_a=2, retrieval_rank_b=1,
                           relevance_a=0.9, relevance_b=0.1):
        item_a = EvidenceItem(1, "S1", 1, None, content="w" * 20,
                              retrieval_rank=retrieval_rank_a, relevance_score=relevance_a)
        item_b = EvidenceItem(2, "S2", 1, None, content="q" * 20,
                              retrieval_rank=retrieval_rank_b, relevance_score=relevance_b)
        pack = _pack([item_a, item_b])
        block = len(f"[{pack.citations[0]}]\n{item_a.content}")
        self.assertEqual(block, len(f"[{pack.citations[1]}]\n{item_b.content}"))
        return pack, block

    def test_default_none_preserves_retrieval_rank_order(self):
        pack, block = self._equal_block_items()
        selected = pack.budgeted_items(max_chars=block)
        self.assertEqual([item.fact_id for item in selected], [2])  # rank 1 wins rank-major

    def test_alpha_zero_orders_by_relevance_score_alone(self):
        pack, block = self._equal_block_items()
        selected = pack.budgeted_items(max_chars=block, global_sort_alpha=0.0)
        self.assertEqual([item.fact_id for item in selected], [1])  # higher relevance_score wins

    def test_alpha_one_orders_by_source_priority_alone(self):
        pack, block = self._equal_block_items()
        selected = pack.budgeted_items(
            max_chars=block, global_sort_alpha=1.0,
            source_priority={"S1": 0.1, "S2": 5.0})
        self.assertEqual([item.fact_id for item in selected], [2])  # higher source_priority wins

    def test_missing_source_priority_defaults_to_zero(self):
        pack, block = self._equal_block_items()
        # alpha=1 with no source_priority map at all -> every doc_score is 0.0, tie broken by
        # canonical item order (fact_id), not by relevance_score.
        selected = pack.budgeted_items(max_chars=block, global_sort_alpha=1.0)
        self.assertEqual([item.fact_id for item in selected], [1])

    def test_rejects_out_of_range_alpha(self):
        pack, block = self._equal_block_items()
        with self.assertRaises(ValueError):
            pack.budgeted_items(max_chars=block, global_sort_alpha=1.5)
        with self.assertRaises(ValueError):
            pack.budgeted_items(max_chars=block, global_sort_alpha=-0.1)


class RenderUntrustedForwardsMergeParamsTests(unittest.TestCase):
    def test_render_untrusted_respects_global_sort_alpha(self):
        item_a = EvidenceItem(1, "S1", 1, None, content="w" * 20,
                              retrieval_rank=2, relevance_score=0.9)
        item_b = EvidenceItem(2, "S2", 1, None, content="q" * 20,
                              retrieval_rank=1, relevance_score=0.1)
        pack = _pack([item_a, item_b])
        block = len(f"[{pack.citations[0]}]\n{item_a.content}")
        default_text = pack.render_untrusted(max_chars=block)
        alpha_text = pack.render_untrusted(max_chars=block, global_sort_alpha=0.0)
        self.assertIn("q" * 20, default_text)
        self.assertNotIn("w" * 20, default_text)
        self.assertIn("w" * 20, alpha_text)
        self.assertNotIn("q" * 20, alpha_text)

    def test_render_untrusted_respects_dedup_threshold(self):
        item_a = EvidenceItem(1, "docA", 1, None,
                              content="The quick brown fox jumps over the lazy dog")
        item_b = EvidenceItem(2, "docB", 1, None,
                              content="The quick brown fox jumps over a lazy dog today")
        pack = _pack([item_a, item_b])
        no_dedup = pack.render_untrusted(max_chars=500)
        deduped = pack.render_untrusted(max_chars=500, dedup_threshold=0.6)
        self.assertIn("docA", no_dedup)
        self.assertIn("docB", no_dedup)
        self.assertIn("docA", deduped)
        self.assertNotIn("docB", deduped)


class DedupThresholdTests(unittest.TestCase):
    def _items(self):
        item_a = EvidenceItem(1, "docA", 1, None,
                              content="The quick brown fox jumps over the lazy dog",
                              retrieval_rank=1)
        item_b = EvidenceItem(2, "docB", 1, None,
                              content="The quick brown fox jumps over a lazy dog today",
                              retrieval_rank=2)
        return item_a, item_b

    def test_no_dedup_keeps_both_near_duplicates(self):
        item_a, item_b = self._items()
        pack = _pack([item_a, item_b])
        selected = pack.budgeted_items(max_chars=500)
        self.assertEqual(len(selected), 2)

    def test_moderate_dedup_rejects_near_duplicate(self):
        item_a, item_b = self._items()
        pack = _pack([item_a, item_b])
        selected = pack.budgeted_items(max_chars=500, dedup_threshold=0.6)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].fact_id, 1)

    def test_strict_dedup_also_rejects(self):
        item_a, item_b = self._items()
        pack = _pack([item_a, item_b])
        selected = pack.budgeted_items(max_chars=500, dedup_threshold=0.4)
        self.assertEqual(len(selected), 1)

    def test_distinct_content_survives_dedup(self):
        item_a = EvidenceItem(1, "docA", 1, None, content="completely unrelated content here")
        item_b = EvidenceItem(2, "docB", 1, None, content="a totally different sentence entirely")
        pack = _pack([item_a, item_b])
        selected = pack.budgeted_items(max_chars=500, dedup_threshold=0.6)
        self.assertEqual(len(selected), 2)

    def test_rejects_out_of_range_dedup_threshold(self):
        item_a, item_b = self._items()
        pack = _pack([item_a, item_b])
        with self.assertRaises(ValueError):
            pack.budgeted_items(max_chars=500, dedup_threshold=1.5)
        with self.assertRaises(ValueError):
            pack.budgeted_items(max_chars=500, dedup_threshold=-0.1)


if __name__ == "__main__":
    unittest.main()
