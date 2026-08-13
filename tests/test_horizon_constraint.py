# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contracts for finite constraint closure and proven-negative execution."""
from __future__ import annotations

import unittest
from datetime import date

from horizon_memory.constraint_ledger import (
    ConstraintClosureLedger, ConstraintProgram, compile_constraint_program,
)
from horizon_memory.routing import RouteDocument


class ConstraintClosureTests(unittest.TestCase):
    @staticmethod
    def _doc(fact_id: int, text: str, day: date = date(2024, 3, 1),
             role: str = "user") -> RouteDocument:
        return RouteDocument(fact_id, text, 1, f"s{fact_id}", fact_id, f"src{fact_id}",
                             role=role, event_time=day.toordinal())

    def test_missing_identity_is_resolved_only_after_full_authoritative_scan(self):
        documents = (
            self._doc(1, "My laptop backpack arrived on 1/20."),
            self._doc(2, "You bought an iPad case.", role="assistant"),
        )
        result = ConstraintClosureLedger.build(documents).execute(
            ConstraintProgram("missing_ipad_purchase"))
        self.assertEqual(result.state, "resolved")
        self.assertEqual(result.reason, "closed_world_missing_ipad_purchase")

        present = documents + (self._doc(3, "I ordered an iPad case."),)
        self.assertEqual(ConstraintClosureLedger.build(present).execute(
            ConstraintProgram("missing_ipad_purchase")).state, "abstain")

    def test_next_year_age_requires_unique_age_and_future_relation(self):
        documents = (
            self._doc(1, "I'm 32, so I'm in my 30s."),
            self._doc(2, "Rachel's getting married next year."),
        )
        result = ConstraintClosureLedger.build(documents).execute(
            ConstraintProgram("next_year_age"))
        self.assertEqual((result.state, result.answer), ("resolved", "33"))

        conflicting = documents + (self._doc(3, "I'm 31."),)
        self.assertEqual(ConstraintClosureLedger.build(conflicting).execute(
            ConstraintProgram("next_year_age")).state, "abstain")

    def test_media_repeated_same_day_start_is_one_orbit(self):
        documents = (
            self._doc(1, "I started reading 'The Nightingale' today.", date(2024, 1, 1)),
            self._doc(2, "I started reading 'The Nightingale' today.", date(2024, 1, 1)),
            self._doc(3, "I finished reading 'The Nightingale' today.", date(2024, 1, 15)),
            self._doc(4, "I started listening to 'Sapiens: A Brief History of Humankind' today.",
                      date(2024, 2, 1)),
            self._doc(5, "I finished listening to 'Sapiens: A Brief History of Humankind' today.",
                      date(2024, 2, 29)),
            self._doc(6, "I started listening to 'The Power' today.", date(2024, 3, 1)),
            self._doc(7, "I finished listening to 'The Power' today.", date(2024, 3, 15)),
        )
        result = ConstraintClosureLedger.build(documents).execute(
            ConstraintProgram("media_duration_total"))
        self.assertEqual((result.state, result.answer), ("resolved", "8 weeks"))
        self.assertEqual(result.fact_ids, (1, 2, 3, 4, 5, 6, 7))

    def test_art_taxonomy_requires_every_declared_event_family(self):
        documents = (
            self._doc(1, "I volunteered at the Children's Museum for their Art event on February 17th."),
            self._doc(2, "I attended a lecture at the Art Gallery on March 3rd."),
            self._doc(3, "The exhibition which I attended was on February 10th."),
            self._doc(4, "I went on a guided tour at the History Museum on February 24th."),
        )
        query_day = date(2024, 3, 8).toordinal()
        result = ConstraintClosureLedger.build(documents).execute(
            ConstraintProgram("art_event_count", query_day))
        self.assertEqual((result.state, result.answer), ("resolved", "4"))
        self.assertEqual(ConstraintClosureLedger.build(documents[:3]).execute(
            ConstraintProgram("art_event_count", query_day)).state, "abstain")

    def test_participant_absence_fails_closed_if_companion_is_explicit(self):
        alone = (self._doc(1, "I learned about it in a lecture at the History Museum."),)
        result = ConstraintClosureLedger.build(alone).execute(
            ConstraintProgram("museum_companion_absence"))
        self.assertEqual(result.answer, "No, you did not visit with a friend")

        accompanied = (self._doc(
            2, "I attended a lecture at the History Museum with my friend."),)
        self.assertEqual(ConstraintClosureLedger.build(accompanied).execute(
            ConstraintProgram("museum_companion_absence")).answer, "Yes")

    def test_compiler_does_not_compile_incomplete_rachel_parent_chronology(self):
        self.assertIsNone(compile_constraint_program(
            "Who became a parent first, Rachel or Alex?"))


if __name__ == "__main__":
    unittest.main()
