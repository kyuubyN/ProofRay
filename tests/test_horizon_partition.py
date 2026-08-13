# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""FH-05 / V24 — contratos da particao causal experimental."""
from __future__ import annotations

import unittest

from horizon_memory import (
    CausalPartitioner, PartitionContext, PartitionIndex, PartitionStrategy,
)


def _ctx(**overrides):
    values = dict(scope_id=7, session_id="session-a", active_goals=("ship-paper",),
                  entities=("horizon",), sources=("user",))
    values.update(overrides)
    return PartitionContext(**values)


class PartitionContractTests(unittest.TestCase):
    def test_context_is_canonical_and_immutable(self):
        context = _ctx(active_goals=(" z ", "a", "a"), entities=("e2", "e1"))
        self.assertEqual(context.active_goals, ("a", "z"))
        self.assertEqual(context.entities, ("e1", "e2"))
        with self.assertRaises(Exception):
            context.scope_id = 8

    def test_future_and_gold_signals_are_not_in_interface(self):
        with self.assertRaises(TypeError):
            PartitionContext(scope_id=7, session_id="s", future_query="leak")
        with self.assertRaises(TypeError):
            PartitionContext(scope_id=7, session_id="s", gold_fact_id=1)

    def test_partition_is_deterministic_and_scope_bound(self):
        partitioner = CausalPartitioner()
        a = partitioner.partition(_ctx(), PartitionStrategy.SCOPE_GOAL)
        b = partitioner.partition(_ctx(), PartitionStrategy.SCOPE_GOAL)
        other = partitioner.partition(_ctx(scope_id=8), PartitionStrategy.SCOPE_GOAL)
        self.assertEqual(a, b)
        self.assertNotEqual(a.partition_ids, other.partition_ids)
        self.assertEqual(a.provenance, ("scope_id", "active_goals"))

    def test_each_arm_uses_only_its_preregistered_signals(self):
        partitioner = CausalPartitioner()
        base = _ctx()
        changed = _ctx(session_id="other", active_goals=("other",), entities=("other",),
                       sources=("other",))
        none_a = partitioner.partition(base, PartitionStrategy.NONE)
        none_b = partitioner.partition(changed, PartitionStrategy.NONE)
        self.assertEqual(none_a.partition_ids, none_b.partition_ids)
        self.assertNotEqual(
            partitioner.partition(base, PartitionStrategy.SCOPE_SESSION).partition_ids,
            partitioner.partition(changed, PartitionStrategy.SCOPE_SESSION).partition_ids)
        self.assertNotEqual(
            partitioner.partition(base, PartitionStrategy.SCOPE_GOAL).partition_ids,
            partitioner.partition(changed, PartitionStrategy.SCOPE_GOAL).partition_ids)

    def test_index_returns_deterministic_deduplicated_candidates(self):
        index = PartitionIndex(PartitionStrategy.SCOPE_GOAL)
        index.add(3, _ctx())
        index.add(1, _ctx())
        index.add(1, _ctx())
        self.assertEqual(index.candidates(_ctx()), (1, 3))
        self.assertEqual(index.candidates(_ctx(active_goals=("other",))), ())
        self.assertGreater(index.byte_size, 0)

    def test_fact_cannot_silently_move_partition(self):
        index = PartitionIndex(PartitionStrategy.SCOPE_SESSION)
        index.add(1, _ctx())
        with self.assertRaises(ValueError):
            index.add(1, _ctx(session_id="session-b"))

    def test_limits_and_types_fail_closed(self):
        with self.assertRaises(ValueError):
            _ctx(scope_id=-1)
        with self.assertRaises(TypeError):
            _ctx(active_goals="scalar")
        with self.assertRaises(ValueError):
            _ctx(entities=tuple(str(i) for i in range(33)))


if __name__ == "__main__":
    unittest.main()
