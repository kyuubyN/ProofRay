# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Closed-world measurement ledger contracts."""
from __future__ import annotations

import unittest
from decimal import Decimal

from horizon_memory.measurement_ledger import (
    AggregateProgram, CountProgram, EventLedger, NumericLedger, compile_aggregate_program,
    compile_count_program,
)
from horizon_memory.routing import RouteDocument


class NumericLedgerTests(unittest.TestCase):
    def _documents(self):
        return (
            RouteDocument(1, "I hiked 3 miles at Pine Trail.", 1, "s1", 1, "src1", role="user"),
            RouteDocument(2, "I hiked 5 miles at Lake Trail.", 1, "s2", 1, "src2", role="user"),
            RouteDocument(3, "Maybe the next hike will be 9 miles.", 1, "s3", 1, "src3",
                          role="user"),
            RouteDocument(4, "You hiked 99 miles.", 1, "s4", 1, "src4", role="assistant"),
        )

    def test_extracts_exact_authoritative_numeric_spans(self):
        ledger = NumericLedger.build(self._documents())
        self.assertEqual([atom.value for atom in ledger.atoms],
                         [Decimal("3"), Decimal("5"), Decimal("9")])
        for atom, document in zip(ledger.atoms, self._documents()[:3]):
            self.assertEqual(document.text[atom.span[0]:atom.span[1]], atom.surface)

    def test_lexical_slice_proves_enumeration_not_semantic_completeness(self):
        result = NumericLedger.build(self._documents()).slice(
            unit="mile", required_terms=frozenset(("hiked",)))
        self.assertTrue(result.certificate.enumeration_complete)
        self.assertFalse(result.certificate.semantic_closed_world)
        self.assertFalse(result.executable)

    def test_closed_predicate_still_rejects_uncertain_atom(self):
        result = NumericLedger.build(self._documents()).slice(
            unit="mile", semantic_closed_world=True)
        self.assertFalse(result.executable)
        self.assertEqual(result.certificate.eligible_atoms, 3)

    def test_fundraising_schema_sums_only_asserted_raised_amounts(self):
        documents = (
            RouteDocument(1, "I raised $250 for a food bank.", 1, "s1", 1, "a", role="user"),
            RouteDocument(2, "Our event raised $600 for a shelter.", 1, "s2", 1, "b", role="user"),
            RouteDocument(3, "My fundraising goal is $5,000.", 1, "s3", 1, "c", role="user"),
        )
        result = EventLedger.build(documents).aggregate(AggregateProgram("fundraising", "USD"))
        self.assertEqual(result.state, "resolved")
        self.assertEqual(result.value, Decimal("850"))
        self.assertEqual(result.fact_ids, (1, 2))

    def test_relevant_event_without_measure_blocks_closed_world(self):
        documents = (
            RouteDocument(1, "I raised $250 for a food bank.", 1, "s1", 1, "a", role="user"),
            RouteDocument(2, "I helped raise money for a shelter.", 1, "s2", 1, "b", role="user"),
        )
        result = EventLedger.build(documents).aggregate(AggregateProgram("fundraising", "USD"))
        self.assertEqual(result.state, "abstain")
        self.assertEqual(result.reason, "incomplete_event_measurements")

    def test_query_compiles_to_operation_conditioned_schema(self):
        self.assertEqual(compile_aggregate_program(
            "How much money did I raise through all charity events?").schema, "fundraising")
        self.assertEqual(compile_aggregate_program(
            "How many days did I spend on camping trips this year?").schema,
            "camping_duration")

    def test_count_deduplicates_entity_and_can_prove_zero_in_another_month(self):
        documents = (
            RouteDocument(1, "I visited The Art Cube on 2/15.", 1, "s1", 1, "a", role="user"),
            RouteDocument(2, "I visited The Art Cube again on February 15th.",
                          1, "s2", 1, "b", role="user"),
            RouteDocument(3, "I took my niece to the Natural History Museum on 2/8.",
                          1, "s3", 1, "c", role="user"),
        )
        ledger = EventLedger.build(documents)
        february = ledger.count(CountProgram("museum_visit", "february"))
        december = ledger.count(CountProgram("museum_visit", "december"))
        self.assertEqual(february.value, 2)
        self.assertEqual(december.value, 0)
        self.assertEqual(december.state, "resolved")

    def test_count_query_compiles_month_and_schema(self):
        program = compile_count_program(
            "How many different museums or galleries did I visit in February?")
        self.assertEqual(program, CountProgram("museum_visit", "february"))

    def test_undated_repeat_collapses_only_onto_same_dated_identity(self):
        documents = (
            RouteDocument(1, "I visited The Art Cube on 2/15.", 1, "s1", 1, "a", role="user"),
            RouteDocument(2, "I recently visited The Art Cube.", 1, "s2", 1, "b", role="user"),
        )
        result = EventLedger.build(documents).count(CountProgram("museum_visit", "february"))
        self.assertEqual(result.state, "resolved")
        self.assertEqual(result.value, 1)

        distinct = documents + (
            RouteDocument(3, "I recently visited the History Museum.",
                          1, "s3", 1, "c", role="user"),
        )
        self.assertEqual(EventLedger.build(distinct).count(
            CountProgram("museum_visit", "february")).state, "abstain")

    def test_adjacent_sentence_transport_binds_bike_entity_to_service_action(self):
        documents = (RouteDocument(
            1, "I need a tire for my commuter bike. It is time to replace it this month, "
               "before April comes.", 1, "s1", 1, "a", role="user"),)
        result = EventLedger.build(documents).count(CountProgram("bike_service", "march"))
        self.assertEqual(result.state, "resolved")
        self.assertEqual(result.value, 1)

    def test_planned_doctor_appointment_is_not_counted_as_completed(self):
        documents = (
            RouteDocument(1, "I had an appointment with my primary care physician, Dr. Smith, "
                               "on March 3rd.", 1, "s1", 1, "a", role="user"),
            RouteDocument(2, "I'll schedule an appointment with Dr. Jones.",
                          1, "s2", 1, "b", role="user"),
        )
        result = EventLedger.build(documents).count(CountProgram("doctor_appointment", "march"))
        self.assertEqual(result.state, "resolved")
        self.assertEqual(result.value, 1)


if __name__ == "__main__":
    unittest.main()
