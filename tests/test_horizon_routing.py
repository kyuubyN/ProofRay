# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""FH-06 / V25 — contratos do roteamento real e do verificador."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from horizon_memory import (
    CandidateList, CausalWeaveGenerator, DenseGenerator, HorizonConfig, HorizonMemory, HorizonVerifier, HybridGenerator,
    BM25Generator, LexicalGenerator, QueryEnvelope, RouteDocument, RouteState, RoutingIndex,
    SemanticRouter, TemporalCausalWeaveGenerator,
)


KEY = b"routing-contract-key-32-bytes!!!"
SCOPE = 7


def _memory():
    root = Path(tempfile.mkdtemp()) / "hm"
    memory = HorizonMemory.create(HorizonConfig(str(root), SCOPE, KEY))
    memory.put(SCOPE, 1, 1, 10)
    memory.put(SCOPE, 2, 1, 20)
    return memory


def _index(memory, stale_version=False):
    generation = memory.get(SCOPE, 1).generation_id
    return RoutingIndex((
        RouteDocument(1, "project horizon release memory", SCOPE, "s1",
                      2 if stale_version else 1, "note-a", generation),
        RouteDocument(2, "granite model evaluation benchmark", SCOPE, "s2", 1, "note-b", generation),
        RouteDocument(99, "foreign scope secret", 8, "s1", 1, "foreign", generation),
    ))


def _query(text="horizon memory", scope=SCOPE, session="s1"):
    return QueryEnvelope("q1", text, scope, session, 10)


class RoutingContractTests(unittest.TestCase):
    def test_candidate_lists_are_deterministic_and_deduplicated(self):
        memory = _memory()
        index = _index(memory)
        for generator in (LexicalGenerator(), BM25Generator(), DenseGenerator(), HybridGenerator(),
                          CausalWeaveGenerator()):
            first = generator.generate(_query(), index, 8)
            second = generator.generate(_query(), index, 8)
            self.assertEqual(first, second)
            self.assertIsInstance(first, CandidateList)
            self.assertEqual(len({c.fact_id for c in first.candidates}), len(first.candidates))
        memory.close()

    def test_causal_weave_covers_session_boundaries_without_labels(self):
        documents = []
        for session in range(4):
            for turn in range(4):
                documents.append(RouteDocument(
                    session * 4 + turn + 1,
                    ("target project" if session == 2 else "unrelated") + f" event {turn}",
                    SCOPE, f"s{session}", 1, f"s{session}", None, session * 4 + turn,
                    (turn, turn + 1), "user" if turn % 2 == 0 else "assistant",
                ))
        index = RoutingIndex(tuple(documents))
        result = CausalWeaveGenerator().generate(_query("target project"), index, 8,
                                                  same_session=False)
        selected = {candidate.fact_id for candidate in result.candidates}
        self.assertIn(9, selected)
        self.assertIn(11, selected)
        self.assertEqual(len(selected), 8)

    def test_bm25_prefers_specific_short_turn_and_preserves_sequence(self):
        memory = _memory()
        generation = memory.get(SCOPE, 1).generation_id
        index = RoutingIndex((
            RouteDocument(1, "horizon " * 50, SCOPE, "s1", 1, "long", generation, 20, (4, 5)),
            RouteDocument(2, "horizon memory release", SCOPE, "s1", 1, "specific", generation,
                          10, (2, 3)),
        ))
        result = SemanticRouter(index, BM25Generator(), HorizonVerifier(memory, index)).route(
            _query(), 2, allow_scope_fallback=False)
        self.assertEqual(result.evidence.fact_ids, (2, 1))
        self.assertEqual(result.evidence.items[0].span, (2, 3))
        memory.close()

    def test_temporal_weave_projects_explicit_relative_day_without_lexical_match(self):
        index = RoutingIndex((
            RouteDocument(1, "ordinary note", SCOPE, "s1", 1, "a", event_time=90),
            RouteDocument(2, "I bought a smoker today", SCOPE, "s2", 1, "b", event_time=100),
            RouteDocument(3, "appliance discussion", SCOPE, "s3", 1, "c", event_time=109),
        ))
        result = TemporalCausalWeaveGenerator().generate(
            QueryEnvelope("q", "What kitchen appliance did I buy 10 days ago?", SCOPE, "q", 3,
                          event_time=110),
            index, 8, same_session=False,
        )
        self.assertEqual(tuple(candidate.fact_id for candidate in result.candidates), (2,))
        self.assertEqual(result.candidates[0].namespace, "scope_time")

    def test_router_returns_only_horizon_verified_identity(self):
        memory = _memory()
        index = _index(memory)
        result = SemanticRouter(index, HybridGenerator(), HorizonVerifier(memory, index)).route(_query(), 4)
        self.assertEqual(result.state, RouteState.EVIDENCE)
        self.assertIn(1, result.evidence.fact_ids)
        self.assertTrue(all(item.verifier_state == "verified" for item in result.evidence.items))
        memory.close()

    def test_version_mismatch_is_rejected_fail_closed(self):
        memory = _memory()
        index = _index(memory, stale_version=True)
        result = SemanticRouter(index, LexicalGenerator(), HorizonVerifier(memory, index)).route(_query(), 1)
        self.assertEqual(result.state, RouteState.ABSTENTION)
        self.assertEqual(result.trace.verifier_rejections, 1)
        memory.close()

    def test_deleted_candidate_is_rejected(self):
        memory = _memory()
        index = _index(memory)
        memory.delete(SCOPE, 1, 2)
        result = SemanticRouter(index, LexicalGenerator(), HorizonVerifier(memory, index)).route(_query(), 1)
        self.assertEqual(result.state, RouteState.ABSTENTION)
        memory.close()

    def test_scope_mismatch_abstains_before_lookup(self):
        memory = _memory()
        index = _index(memory)
        result = SemanticRouter(index, HybridGenerator(), HorizonVerifier(memory, index)).route(
            _query(scope=8), 4)
        self.assertEqual(result.state, RouteState.ABSTAIN_SCOPE)
        self.assertEqual(result.trace.horizon_lookups, 0)
        memory.close()

    def test_session_fallback_is_explicit(self):
        memory = _memory()
        index = _index(memory)
        result = SemanticRouter(index, LexicalGenerator(), HorizonVerifier(memory, index)).route(
            _query(text="granite benchmark", session="new-session"), 2)
        self.assertEqual(result.state, RouteState.EVIDENCE)
        self.assertIn(2, result.evidence.fact_ids)
        self.assertTrue(result.trace.session_fallback_used)
        memory.close()

    def test_negative_query_abstains(self):
        memory = _memory()
        index = _index(memory)
        result = SemanticRouter(index, HybridGenerator(), HorizonVerifier(memory, index)).route(
            _query(text="unseenwordwithoutmatch"), 4)
        self.assertEqual(result.state, RouteState.ABSTENTION)
        memory.close()


if __name__ == "__main__":
    unittest.main()
