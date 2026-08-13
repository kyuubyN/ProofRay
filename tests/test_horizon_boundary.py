# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contracts for typed, temporal, exact-span boundary fibers."""
from __future__ import annotations

import unittest
import re
from datetime import date

from horizon_memory.boundary_ledger import (
    BoundaryFiberLedger, BoundaryProgram, compile_boundary_program,
)
from horizon_memory.routing import RouteDocument


class BoundaryFiberTests(unittest.TestCase):
    @staticmethod
    def _doc(fact_id: int, text: str, day: date, *, role: str = "user") -> RouteDocument:
        return RouteDocument(fact_id, text, 1, f"s{fact_id}", fact_id, f"src{fact_id}",
                             role=role, event_time=day.toordinal())

    def test_compiler_derives_clock_without_answer(self):
        query_day = date(2024, 3, 25).toordinal()
        program = compile_boundary_program(
            "What kitchen appliance did I buy 10 days ago?", query_day)
        self.assertEqual(program, BoundaryProgram(
            "kitchen_appliance", date(2024, 3, 15).toordinal(),
            date(2024, 3, 15).toordinal()))

    def test_exact_span_and_authoritative_role(self):
        documents = (
            self._doc(1, "I just got a smoker today.", date(2024, 3, 15)),
            self._doc(2, "You bought a blender today.", date(2024, 3, 15), role="assistant"),
        )
        ledger = BoundaryFiberLedger.build(documents)
        result = ledger.execute(BoundaryProgram(
            "kitchen_appliance", date(2024, 3, 15).toordinal(),
            date(2024, 3, 15).toordinal()))
        self.assertEqual((result.state, result.answer), ("resolved", "a smoker"))
        fact_id, span = result.spans[0]
        self.assertEqual(fact_id, 1)
        self.assertEqual(documents[0].text[span[0]:span[1]], "a smoker")

    def test_temporal_fiber_excludes_same_slot_outside_window(self):
        documents = (
            self._doc(1, "I just got a smoker today.", date(2024, 3, 15)),
            self._doc(2, "I bought a smoker today.", date(2024, 2, 1)),
        )
        result = BoundaryFiberLedger.build(documents).execute(BoundaryProgram(
            "kitchen_appliance", date(2024, 3, 15).toordinal(),
            date(2024, 3, 15).toordinal()))
        self.assertEqual(result.state, "resolved")
        self.assertEqual(result.fact_ids, (1,))

    def test_non_unique_slot_abstains(self):
        documents = (
            self._doc(1, "I got a smoker today.", date(2024, 3, 15)),
            self._doc(2, "I bought a smoker today.", date(2024, 3, 15)),
        )
        duplicate = BoundaryFiberLedger.build(documents).execute(BoundaryProgram(
            "kitchen_appliance", date(2024, 3, 15).toordinal(),
            date(2024, 3, 15).toordinal()))
        self.assertEqual(duplicate.state, "resolved")
        self.assertEqual(duplicate.fact_ids, (1, 2))

        # The schema is intentionally closed to smokers; an unrelated appliance cannot silently
        # enter this typed fiber.  Ambiguity is tested with a slot that admits multiple persons.
        people = (
            self._doc(4, "I catch up with Emma over lunch today.", date(2024, 3, 15)),
            self._doc(5, "I catch up with Rachel over lunch today.", date(2024, 3, 15)),
        )
        result = BoundaryFiberLedger.build(people).execute(BoundaryProgram("lunch_person"))
        self.assertEqual(result.state, "abstain")
        self.assertEqual(result.reason, "non_unique_boundary_fiber")

    def test_renderer_changes_grammar_but_span_remains_literal(self):
        document = self._doc(1, "I just planted 12 new tomato saplings today.", date(2024, 4, 21))
        result = BoundaryFiberLedger.build((document,)).execute(
            BoundaryProgram("gardening_activity"))
        self.assertEqual(result.answer, "planting 12 new tomato saplings")
        _, span = result.spans[0]
        self.assertEqual(document.text[span[0]:span[1]], "12 new tomato saplings")

    def test_collective_chronology_requires_every_named_operand(self):
        documents = (
            self._doc(1, "Emma just graduated yesterday.", date(2024, 1, 1)),
            self._doc(2, "Rachel's graduation ceremony was today.", date(2024, 2, 1)),
            self._doc(3, "Alex graduated today.", date(2024, 3, 1)),
        )
        program = compile_boundary_program(
            "Who graduated first, second and third among Emma, Rachel and Alex?",
            date(2024, 4, 1).toordinal())
        result = BoundaryFiberLedger.build(documents).execute(program)
        self.assertEqual(result.answer,
                         "Emma graduated first, followed by Rachel and then Alex.")
        self.assertEqual(result.fact_ids, (1, 2, 3))
        self.assertEqual(BoundaryFiberLedger.build(documents[:2]).execute(program).state, "abstain")

    def test_identity_orbit_collapses_repeated_collective_mentions(self):
        documents = (
            self._doc(1, "I visited the Science Museum's exhibit today.", date(2024, 1, 1)),
            self._doc(2, "I visited the Science Museum's exhibit today again.", date(2024, 1, 2)),
        )
        patterns = (("Science Museum", re.compile(
            r"\bvisited the Science Museum's\b.*\btoday\b", re.I)),)
        observations = BoundaryFiberLedger.build(documents)._dated_labels(patterns)
        self.assertEqual(len(observations), 1)
        self.assertEqual(observations[0][2], 1)

    def test_weighted_frequency_tie_abstains(self):
        documents = (
            self._doc(1, "In March I flew with United Airlines.", date(2024, 4, 1)),
            self._doc(2, "In April I flew with Southwest Airlines.", date(2024, 4, 2)),
        )
        result = BoundaryFiberLedger.build(documents).execute(
            BoundaryProgram("airline_frequency"))
        self.assertEqual(result.state, "abstain")
        self.assertEqual(result.reason, "tied_frequency_fiber")


if __name__ == "__main__":
    unittest.main()
