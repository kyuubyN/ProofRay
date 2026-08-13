# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contracts for orbit-aware relational tensor interval bindings."""
from __future__ import annotations

import unittest
from datetime import date

from horizon_memory.relational_ledger import (
    RelationalProgram, RelationalTensorLedger, compile_relational_program,
)
from horizon_memory.routing import RouteDocument


class RelationalTensorTests(unittest.TestCase):
    @staticmethod
    def _doc(fact_id: int, text: str, day: date, sequence: int = 1,
             role: str = "user") -> RouteDocument:
        return RouteDocument(fact_id, text, 1, f"s{fact_id}", sequence, f"src{fact_id}",
                             role=role, event_time=day.toordinal())

    def test_compiler_transports_pronoun_through_shared_object(self):
        program = compile_relational_program(
            "How many days did it take for my laptop backpack to arrive after I bought it?")
        self.assertEqual(program.anchor_a, "bought laptop backpack")
        self.assertEqual(program.anchor_b, "arrive laptop backpack")

    def test_joint_binding_resolves_two_independent_clocks(self):
        documents = (
            self._doc(1, "I bought my laptop backpack on 1/15.", date(2024, 1, 24)),
            self._doc(2, "My laptop backpack arrived on 1/20.", date(2024, 1, 24)),
        )
        program = compile_relational_program(
            "How many days did it take for my laptop backpack to arrive after I bought it?")
        result = RelationalTensorLedger.build(documents).execute(program)
        self.assertEqual((result.state, result.value, result.unit), ("resolved", 5, "day"))
        self.assertEqual(result.fact_ids, (1, 2))

    def test_missing_shared_identity_abstains_instead_of_substituting_backpack(self):
        documents = (
            self._doc(1, "I bought my laptop backpack on 1/15.", date(2024, 1, 24)),
            self._doc(2, "My laptop backpack arrived on 1/20.", date(2024, 1, 24)),
        )
        program = compile_relational_program(
            "How many days did it take for my iPad case to arrive after I bought it?")
        result = RelationalTensorLedger.build(documents).execute(program)
        self.assertEqual(result.state, "abstain")
        self.assertEqual(result.reason, "missing_relational_operand")

    def test_same_date_repeated_reports_form_one_orbit(self):
        documents = (
            self._doc(1, "I bought my laptop backpack on 1/15.", date(2024, 1, 24)),
            self._doc(2, "I bought my laptop backpack on January 15th.", date(2024, 1, 24)),
            self._doc(3, "My laptop backpack arrived on 1/20.", date(2024, 1, 24)),
        )
        result = RelationalTensorLedger.build(documents).execute(RelationalProgram(
            "day", "bought laptop backpack", "arrive laptop backpack"))
        self.assertEqual(result.state, "resolved")
        self.assertEqual(result.value, 5)
        self.assertEqual(result.fact_ids, (1, 2, 3))

    def test_equal_scoring_different_date_orbits_remain_ambiguous(self):
        documents = (
            self._doc(1, "I bought my laptop backpack on 1/14.", date(2024, 1, 24)),
            self._doc(2, "I bought my laptop backpack on 1/15.", date(2024, 1, 24)),
            self._doc(3, "My laptop backpack arrived on 1/20.", date(2024, 1, 24)),
        )
        result = RelationalTensorLedger.build(documents).execute(RelationalProgram(
            "day", "bought laptop backpack", "arrive laptop backpack"))
        self.assertEqual(result.state, "abstain")
        self.assertEqual(result.reason, "ambiguous_relational_tensor")

    def test_abbreviation_does_not_sever_entity_from_date(self):
        documents = (
            self._doc(1, "I attended Sunday mass at St. Mary's Church on January 2nd.",
                      date(2024, 2, 20)),
            self._doc(2, "I attended the Ash Wednesday service on February 1st.",
                      date(2024, 2, 20)),
        )
        result = RelationalTensorLedger.build(documents).execute(RelationalProgram(
            "day", "Sunday mass at St. Mary's Church", "Ash Wednesday service"))
        self.assertEqual((result.state, result.value), ("resolved", 30))

    def test_week_projection_allows_only_documented_one_day_calendar_slack(self):
        documents = (
            self._doc(1, "I sold baked goods at the Farmers' Market on February 26th.",
                      date(2024, 3, 21)),
            self._doc(2, "I participated in the Spring Fling Market on March 19th.",
                      date(2024, 3, 21)),
        )
        exact = RelationalTensorLedger.build(documents).execute(RelationalProgram(
            "week", "sold baked goods Farmers Market", "participated Spring Fling Market"))
        self.assertEqual((exact.state, exact.value), ("resolved", 3))

        too_slack = documents[:1] + (
            self._doc(3, "I participated in the Spring Fling Market on March 20th.",
                      date(2024, 3, 21)),)
        self.assertEqual(RelationalTensorLedger.build(too_slack).execute(RelationalProgram(
            "week", "sold baked goods Farmers Market",
            "participated Spring Fling Market")).state, "abstain")


if __name__ == "__main__":
    unittest.main()
