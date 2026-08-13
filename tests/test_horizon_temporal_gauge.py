# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contracts for query-selected temporal reference gauges."""
from __future__ import annotations

import unittest
from datetime import date

from horizon_memory.routing import RouteDocument
from horizon_memory.temporal_gauge import (
    TemporalGaugeProgram, TemporalReferenceGauge, compile_temporal_gauge,
)


class TemporalReferenceGaugeTests(unittest.TestCase):
    @staticmethod
    def _doc(fact_id: int, text: str, day: date, role: str = "user") -> RouteDocument:
        return RouteDocument(fact_id, text, 1, f"s{fact_id}", fact_id, f"src{fact_id}",
                             role=role, event_time=day.toordinal())

    def test_intrassentence_yesterday_refines_session_clock(self):
        documents = (self._doc(
            1, "Yesterday, I attended a friends and family sale at Nordstrom.",
            date(2024, 11, 18)),)
        program = compile_temporal_gauge(
            "How many weeks ago did I attend the friends and family sale at Nordstrom?",
            date(2024, 12, 1).toordinal())
        result = TemporalReferenceGauge.build(documents).execute(program)
        self.assertEqual((result.state, result.value, result.unit), ("resolved", 2, "week"))
        self.assertEqual(result.event_days, (date(2024, 11, 17).toordinal(),))

    def test_repeated_same_day_reports_form_one_section(self):
        documents = (
            self._doc(1, "I got a smoker today.", date(2024, 3, 15)),
            self._doc(2, "I bought a smoker today.", date(2024, 3, 15)),
        )
        result = TemporalReferenceGauge.build(documents).execute(TemporalGaugeProgram(
            "smoker_purchase", "day", date(2024, 3, 25).toordinal()))
        self.assertEqual((result.state, result.value), ("resolved", 10))
        self.assertEqual(result.fact_ids, (1, 2))

    def test_different_event_days_do_not_collapse(self):
        documents = (
            self._doc(1, "I got a smoker today.", date(2024, 3, 14)),
            self._doc(2, "I bought a smoker today.", date(2024, 3, 15)),
        )
        result = TemporalReferenceGauge.build(documents).execute(TemporalGaugeProgram(
            "smoker_purchase", "day", date(2024, 3, 25).toordinal()))
        self.assertEqual(result.state, "abstain")
        self.assertEqual(result.reason, "non_unique_temporal_section")

    def test_consecutive_pair_requires_exactly_one_pair(self):
        documents = (
            self._doc(1, "I attended a charity gala today.", date(2024, 2, 14)),
            self._doc(2, "I volunteered at a charity event today.", date(2024, 2, 15)),
            self._doc(3, "I did another charity event today.", date(2024, 3, 19)),
        )
        program = TemporalGaugeProgram(
            "consecutive_charity_pair", "month", date(2024, 4, 18).toordinal())
        result = TemporalReferenceGauge.build(documents).execute(program)
        self.assertEqual((result.state, result.value), ("resolved", 2))
        self.assertEqual(result.fact_ids, (1, 2))

        two_pairs = documents + (
            self._doc(4, "I attended a charity auction today.", date(2024, 3, 20)),)
        self.assertEqual(TemporalReferenceGauge.build(two_pairs).execute(program).state, "abstain")

    def test_when_conflict_does_not_compile_to_single_reference_gauge(self):
        self.assertIsNone(compile_temporal_gauge(
            "How many days ago did I launch my website when I signed my first client?",
            date(2024, 3, 25).toordinal()))

    def test_future_reference_abstains(self):
        documents = (self._doc(1, "I got a smoker today.", date(2024, 3, 26)),)
        result = TemporalReferenceGauge.build(documents).execute(TemporalGaugeProgram(
            "smoker_purchase", "day", date(2024, 3, 25).toordinal()))
        self.assertEqual(result.state, "abstain")
        self.assertEqual(result.reason, "inexact_temporal_projection")


if __name__ == "__main__":
    unittest.main()
