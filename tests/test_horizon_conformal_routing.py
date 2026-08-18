# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""FH-06.2 -- split conformal document routing (D137 port): ConformalCalibrator,
collect_calibration_scores, ConformalDocumentGenerator, ConformalClaimGenerator,
document_priority_by_source."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from horizon_memory import (
    ConformalCalibrator, ConformalClaimGenerator, ConformalDocumentGenerator, HorizonConfig,
    HorizonMemory, HorizonVerifier, QueryEnvelope, RouteDocument, RouteState, RoutingIndex,
    SemanticRouter, collect_conformal_calibration_scores, conformal_score_documents,
    document_priority_by_source,
)

KEY = b"conformal-routing-contract-key32"
SCOPE = 13


class ConformalCalibratorTests(unittest.TestCase):
    def test_rejects_empty_calibration_scores(self):
        with self.assertRaises(ValueError):
            ConformalCalibrator(())

    def test_rejects_unsorted_calibration_scores(self):
        with self.assertRaises(ValueError):
            ConformalCalibrator((0.5, 0.1, 0.9))

    def test_p_value_monotonically_increases_with_score(self):
        # Higher score -> more of the calibration set falls below it -> higher p-value -> more
        # confidently a true match, per split conformal prediction's own definition.
        calibrator = ConformalCalibrator((0.1, 0.3, 0.5, 0.7, 0.9))
        low = calibrator.p_value(0.0)
        mid = calibrator.p_value(0.5)
        high = calibrator.p_value(1.0)
        self.assertLess(low, mid)
        self.assertLess(mid, high)
        self.assertGreater(low, 0.0)

    def test_p_value_of_top_score_is_smallest_but_nonzero(self):
        calibrator = ConformalCalibrator((0.1, 0.2, 0.3))
        self.assertGreater(calibrator.p_value(100.0), 0.0)


class CollectCalibrationScoresTests(unittest.TestCase):
    def test_collects_one_score_per_resolved_true_pair(self):
        documents = (
            RouteDocument(1, "Aldren activates Zephyra directly.", SCOPE, "s1", 1, "note-a"),
            RouteDocument(2, "Meridian reduces errors by 18 percent.", SCOPE, "s1", 1, "note-b"),
        )
        episodes = [(documents, (("Aldren Zephyra", 1), ("Meridian errors", 2)))]
        scores = collect_conformal_calibration_scores(episodes)
        self.assertEqual(len(scores), 2)
        self.assertEqual(list(scores), sorted(scores))

    def test_skips_episodes_with_no_documents_or_no_true_pairs(self):
        self.assertEqual(collect_conformal_calibration_scores([((), (("q", 1),))]), ())
        documents = (RouteDocument(1, "some text here", SCOPE, "s1", 1, "note-a"),)
        self.assertEqual(collect_conformal_calibration_scores([(documents, ())]), ())

    def test_unresolved_true_fact_id_contributes_no_score(self):
        documents = (RouteDocument(1, "some text here", SCOPE, "s1", 1, "note-a"),)
        scores = collect_conformal_calibration_scores([(documents, (("query", 999),))])
        self.assertEqual(scores, ())


class ScoreDocumentsTests(unittest.TestCase):
    def test_empty_documents_yields_empty_map(self):
        self.assertEqual(conformal_score_documents("q", ()), {})

    def test_scores_keyed_by_fact_id(self):
        documents = (
            RouteDocument(1, "Aldren activates Zephyra directly.", SCOPE, "s1", 1, "note-a"),
            RouteDocument(2, "unrelated content about something else entirely", SCOPE, "s1", 1,
                          "note-b"),
        )
        scores = conformal_score_documents("Aldren Zephyra", documents)
        self.assertEqual(set(scores), {1, 2})
        self.assertGreater(scores[1], scores[2])


class ConformalDocumentGeneratorTests(unittest.TestCase):
    def _index(self):
        return RoutingIndex((
            RouteDocument(1, "Aldren activates Zephyra directly in the pipeline.", SCOPE, "s1",
                          1, "note-a"),
            RouteDocument(2, "Completely unrelated content about a totally different topic.",
                          SCOPE, "s1", 1, "note-b"),
        ))

    def test_rejects_epsilon_out_of_range(self):
        calibrator = ConformalCalibrator((0.1, 0.2))
        with self.assertRaises(ValueError):
            ConformalDocumentGenerator(calibrator, 0.0)
        with self.assertRaises(ValueError):
            ConformalDocumentGenerator(calibrator, 1.0)

    def test_rejects_invalid_weights(self):
        calibrator = ConformalCalibrator((0.1, 0.2))
        with self.assertRaises(ValueError):
            ConformalDocumentGenerator(calibrator, 0.2, weights=(1.0, 1.0))

    def test_low_calibration_scores_admit_the_relevant_document(self):
        # A calibration set of near-zero true-match scores makes even a modest real score land
        # in a high p-value percentile, clearing a lenient epsilon.
        calibrator = ConformalCalibrator((0.0001, 0.0002, 0.0003))
        generator = ConformalDocumentGenerator(calibrator, epsilon=0.2)
        index = self._index()
        query = QueryEnvelope("q1", "Aldren Zephyra pipeline", SCOPE, "s1", 10)
        result = generator.generate(query, index, 8)
        included = {candidate.fact_id for candidate in result.candidates}
        self.assertIn(1, included)

    def test_high_calibration_scores_can_exclude_everything(self):
        # A calibration set of very high true-match scores makes every real document's p-value
        # small; a strict epsilon then admits nothing -- an honest abstention, not a crash.
        calibrator = ConformalCalibrator((50.0, 60.0, 70.0))
        generator = ConformalDocumentGenerator(calibrator, epsilon=0.9)
        index = self._index()
        query = QueryEnvelope("q1", "Aldren Zephyra pipeline", SCOPE, "s1", 10)
        result = generator.generate(query, index, 8)
        self.assertEqual(result.candidates, ())

    def test_no_eligible_documents_yields_empty_candidate_list(self):
        calibrator = ConformalCalibrator((0.1, 0.2))
        generator = ConformalDocumentGenerator(calibrator, epsilon=0.2)
        index = RoutingIndex((RouteDocument(1, "irrelevant", SCOPE, "other-session", 1, "note"),))
        query = QueryEnvelope("q1", "query", SCOPE, "s1", 10)
        result = generator.generate(query, index, 8, same_session=True)
        self.assertEqual(result.candidates, ())


class ConformalClaimGeneratorTests(unittest.TestCase):
    def _memory(self):
        root = Path(tempfile.mkdtemp()) / "hm"
        memory = HorizonMemory.create(HorizonConfig(str(root), SCOPE, KEY))
        memory.put(SCOPE, 1, 1, 10)
        return memory

    def test_end_to_end_restricts_claims_to_conformally_included_documents(self):
        memory = self._memory()
        generation = memory.get(SCOPE, 1).generation_id
        index = RoutingIndex((
            RouteDocument(1, "Aldren activates Zephyra. Meridian reduces errors by 18 percent.",
                          SCOPE, "s1", 1, "note-a", generation),
            RouteDocument(2, "This document is about something totally unrelated to the query.",
                          SCOPE, "s1", 1, "note-b"),
        ))
        calibrator = ConformalCalibrator((0.0001, 0.0002, 0.0003))
        generator = ConformalClaimGenerator(calibrator, epsilon=0.2)
        query = QueryEnvelope("q1", "How does Aldren activate Zephyra?", SCOPE, "s1", 10)
        result = SemanticRouter(index, generator, HorizonVerifier(memory, index)).route(
            query, 2, allow_scope_fallback=False)
        self.assertEqual(result.state, RouteState.EVIDENCE)
        self.assertTrue(all(item.content_span is not None for item in result.evidence.items))
        self.assertTrue(all(item.fact_id == 1 for item in result.evidence.items))
        memory.close()

    def test_abstains_when_no_document_clears_epsilon(self):
        memory = self._memory()
        generation = memory.get(SCOPE, 1).generation_id
        index = RoutingIndex((
            RouteDocument(1, "Aldren activates Zephyra.", SCOPE, "s1", 1, "note-a", generation),
        ))
        calibrator = ConformalCalibrator((50.0, 60.0, 70.0))
        generator = ConformalClaimGenerator(calibrator, epsilon=0.9)
        query = QueryEnvelope("q1", "Aldren Zephyra", SCOPE, "s1", 10)
        result = SemanticRouter(index, generator, HorizonVerifier(memory, index)).route(
            query, 2, allow_scope_fallback=False)
        self.assertEqual(result.state, RouteState.ABSTENTION)
        memory.close()

    def test_document_router_attribute_reusable_for_source_priority(self):
        memory = self._memory()
        generation = memory.get(SCOPE, 1).generation_id
        index = RoutingIndex((
            RouteDocument(1, "Aldren activates Zephyra in the main pipeline stage.", SCOPE, "s1",
                          1, "note-a", generation),
        ))
        calibrator = ConformalCalibrator((0.0001, 0.0002, 0.0003))
        generator = ConformalClaimGenerator(calibrator, epsilon=0.2)
        query = QueryEnvelope("q1", "Aldren Zephyra pipeline", SCOPE, "s1", 10)
        generator.generate(query, index, 4)
        routed_documents = generator.document_router.generate(query, index, 32)
        priority = document_priority_by_source(routed_documents, index)
        self.assertIn("note-a", priority)
        memory.close()


if __name__ == "__main__":
    unittest.main()
