# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""SemanticRouter.route()'s `limit` was relaxed 2026-08-17 from the V25 experiment's own
`{1,2,4,8,16,32}` enum to any positive integer -- a claim-level generator filling a large byte
budget needs a candidate count well above 32 so the candidate cap doesn't bind before the byte
budget does. See routing.py's route() docstring and ConformalClaimGenerator's docstring for the
full rationale."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from horizon_memory import (
    BM25Generator, ClaimGenerator, HorizonConfig, HorizonMemory, HorizonVerifier, QueryEnvelope,
    RouteDocument, RouteState, RoutingIndex, SemanticRouter,
)

KEY = b"routing-limit-contract-key-32by!"
SCOPE = 21


def _memory():
    root = Path(tempfile.mkdtemp()) / "hm"
    memory = HorizonMemory.create(HorizonConfig(str(root), SCOPE, KEY))
    memory.put(SCOPE, 1, 1, 10)
    return memory


class RouteLimitValidationTests(unittest.TestCase):
    def test_rejects_zero_and_negative_limits(self):
        memory = _memory()
        generation = memory.get(SCOPE, 1).generation_id
        index = RoutingIndex((
            RouteDocument(1, "some content here", SCOPE, "s1", 1, "note-a", generation),))
        router = SemanticRouter(index, BM25Generator(), HorizonVerifier(memory, index))
        query = QueryEnvelope("q1", "some content", SCOPE, "s1", 10)
        with self.assertRaises(ValueError):
            router.route(query, 0)
        with self.assertRaises(ValueError):
            router.route(query, -1)
        memory.close()

    def test_rejects_non_integer_limit(self):
        memory = _memory()
        index = RoutingIndex((RouteDocument(1, "content", SCOPE, "s1", 1, "note-a"),))
        router = SemanticRouter(index, BM25Generator(), HorizonVerifier(memory, index))
        query = QueryEnvelope("q1", "content", SCOPE, "s1", 10)
        with self.assertRaises(ValueError):
            router.route(query, 8.0)
        memory.close()

    def test_accepts_legacy_enum_values(self):
        memory = _memory()
        index = RoutingIndex((RouteDocument(1, "content here", SCOPE, "s1", 1, "note-a"),))
        router = SemanticRouter(index, BM25Generator(), HorizonVerifier(memory, index))
        query = QueryEnvelope("q1", "content", SCOPE, "s1", 10)
        for legacy_limit in (1, 2, 4, 8, 16, 32):
            router.route(query, legacy_limit)  # must not raise
        memory.close()

    def test_accepts_limit_above_the_historical_32_ceiling(self):
        memory = _memory()
        generation = memory.get(SCOPE, 1).generation_id
        doc = RouteDocument(
            1, ". ".join(f"Sentence number {i} about topic alpha" for i in range(80)) + ".",
            SCOPE, "s1", 1, "note-a", generation)
        index = RoutingIndex((doc,))
        query = QueryEnvelope("q1", "topic alpha", SCOPE, "s1", 10)
        result = SemanticRouter(index, ClaimGenerator(), HorizonVerifier(memory, index)).route(
            query, 64, allow_scope_fallback=False)
        self.assertEqual(result.state, RouteState.EVIDENCE)
        self.assertGreater(len(result.evidence.items), 32)
        memory.close()


if __name__ == "__main__":
    unittest.main()
