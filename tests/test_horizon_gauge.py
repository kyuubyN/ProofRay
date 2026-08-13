# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""HGSA contracts: aliases and pragmatics propose IDs but never grant authority."""
from __future__ import annotations

import unittest

from horizon_memory.gauge import (
    AliasEdge, CausalCapsuleExtractor, GaugeSyndromeGenerator, GaugeSyndromeIndex,
    GaugeWeaveGenerator, SemanticCapsule,
)
from horizon_memory.routing import QueryEnvelope, RouteDocument, RoutingIndex


class GaugeSyndromeTests(unittest.TestCase):
    def test_scoped_versioned_jargon_recovers_lexically_disconnected_fact(self):
        capsules = (SemanticCapsule(1, 7, 1, entities=("risky prototype",),
                                    relations=("experimental project",)),)
        index = GaugeSyndromeIndex(capsules, (
            AliasEdge(7, "moonshot", "risky prototype", 10, 20, "user-definition"),
        ))
        active = index.address(QueryEnvelope("q", "How is the moonshot?", 7, "s", 15))
        expired = index.address(QueryEnvelope("q", "How is the moonshot?", 7, "s", 21))
        foreign = index.address(QueryEnvelope("q", "How is the moonshot?", 8, "s", 15))
        self.assertEqual(active.addresses[0].fact_id, 1)
        self.assertGreater(active.addresses[0].alias_hops, 0)
        self.assertEqual(expired.addresses, ())
        self.assertEqual(foreign.addresses, ())

    def test_idiom_alias_adds_candidate_before_any_reranker(self):
        index = GaugeSyndromeIndex((
            SemanticCapsule(4, 7, 1, relations=("die",), pragmatic=("idiomatic",)),
        ), (AliasEdge(7, "kick the bucket", "die", 0, None, "approved-lexicon"),))
        result = index.address(QueryEnvelope("q", "Did they kick the bucket?", 7, "s", 1))
        self.assertEqual(result.addresses[0].fact_id, 4)

    def test_irony_keeps_competing_interpretations_and_recommends_planner(self):
        index = GaugeSyndromeIndex((
            SemanticCapsule(1, 7, 1, relations=("great outcome",), polarity="positive",
                            pragmatic=("literal",)),
            SemanticCapsule(2, 7, 1, relations=("bad outcome",), polarity="negative",
                            pragmatic=("ironic",)),
        ), (AliasEdge(7, "yeah right great", "bad outcome", 0, None, "session-context"),))
        result = index.address(QueryEnvelope("q", "Yeah right, great outcome /s", 7, "s", 1))
        self.assertTrue(result.ambiguous)
        self.assertTrue(result.planner_recommended)
        self.assertIn("ironic", result.interpretations)

    def test_polarity_conflict_is_not_canonicalized_away(self):
        index = GaugeSyndromeIndex((
            SemanticCapsule(1, 7, 1, relations=("approve release",), polarity="positive"),
            SemanticCapsule(2, 7, 2, relations=("approve release",), polarity="negative"),
        ))
        result = index.address(QueryEnvelope("q", "Do not approve release", 7, "s", 1))
        states = {address.fact_id: address.polarity_state for address in result.addresses}
        self.assertEqual(states[1], "competing")
        self.assertEqual(states[2], "compatible")

    def test_generator_respects_operational_scope_namespace(self):
        syndrome = GaugeSyndromeIndex((SemanticCapsule(1, 7, 1, entities=("moonshot",)),
                                      SemanticCapsule(2, 7, 1, entities=("moonshot",))))
        route = RoutingIndex((RouteDocument(1, "a", 7, "current", 1, "a"),
                              RouteDocument(2, "b", 7, "other", 1, "b")))
        candidates = GaugeSyndromeGenerator(syndrome).generate(
            QueryEnvelope("q", "moonshot", 7, "current", 1), route, 4, same_session=True)
        self.assertEqual(tuple(candidate.fact_id for candidate in candidates.candidates), (1,))

    def test_write_time_extractor_creates_only_explicit_quoted_aliases(self):
        documents = (
            RouteDocument(1, '`moonshot` means "risky prototype".', 7, "s", 1, "user", sequence=4),
            RouteDocument(2, "What does moonshot mean?", 7, "s", 1, "user", sequence=5),
        )
        extraction = CausalCapsuleExtractor().extract(documents)
        self.assertEqual(len(extraction.aliases), 1)
        self.assertEqual(extraction.aliases[0].left, "moonshot")
        self.assertIn("definition", extraction.capsules[0].pragmatic)

    def test_alias_redefinition_closes_old_meaning_causally(self):
        documents = (
            RouteDocument(1, '`red` means "blocked".', 7, "s", 1, "user", sequence=4),
            RouteDocument(2, '`red` means "urgent".', 7, "s", 1, "user", sequence=9),
        )
        extraction = CausalCapsuleExtractor().extract(documents)
        old, new = extraction.aliases
        self.assertEqual(old.valid_until, 8)
        self.assertTrue(old.active(8))
        self.assertFalse(old.active(9))
        self.assertTrue(new.active(9))

    def test_extractor_preserves_negation_modality_and_irony(self):
        documents = (RouteDocument(
            1, "Maybe we should not ship it. Great idea /s", 7, "s", 1, "user", sequence=2),)
        capsule = CausalCapsuleExtractor().extract(documents).capsules[0]
        self.assertEqual(capsule.polarity, "negative")
        self.assertEqual(capsule.modality, "uncertain")
        self.assertIn("ironic", capsule.pragmatic)

    def test_gauge_weave_recovers_alias_target_absent_from_lexical_query(self):
        documents = (
            RouteDocument(1, '`moonshot` means "risky prototype".', 7, "s", 1, "user", sequence=1),
            RouteDocument(2, "The risky prototype failed testing.", 7, "s", 1, "user", sequence=2),
            RouteDocument(3, "The moonshot budget is ready.", 7, "s", 1, "user", sequence=3),
        )
        extraction = CausalCapsuleExtractor().extract(documents)
        generator = GaugeWeaveGenerator(GaugeSyndromeIndex(
            extraction.capsules, extraction.aliases))
        candidates = generator.generate(
            QueryEnvelope("q", "Did the moonshot fail?", 7, "new", 4),
            RoutingIndex(documents), 3, same_session=False,
        )
        ids = tuple(candidate.fact_id for candidate in candidates.candidates)
        self.assertIn(2, ids)


if __name__ == "__main__":
    unittest.main()
