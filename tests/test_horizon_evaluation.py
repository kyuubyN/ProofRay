# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import unittest

from horizon_memory import (
    EvaluationArm, TrialSignals, assert_paired_query_ids, classify_trial,
)
from horizon_memory.adapters import FixtureModelAdapter, GenerationConfig


class EvaluationContracts(unittest.TestCase):
    def test_reader_error_is_not_route_error(self):
        run = FixtureModelAdapter(responder=lambda q, p: "wrong").generate("q", None, GenerationConfig())
        row = classify_trial(TrialSignals(
            "q1", EvaluationArm.HORIZON_REAL, True, True, False, False, False,
        ), run)
        self.assertTrue(row.reader_error)
        self.assertTrue(row.supported_wrong)
        self.assertFalse(row.route_error)

    def test_all_five_arms_must_be_exactly_paired(self):
        rows = {arm: ("q1", "q2") for arm in EvaluationArm}
        self.assertEqual(assert_paired_query_ids(rows), ("q1", "q2"))
        rows[EvaluationArm.RAG] = ("q2", "q1")
        with self.assertRaises(ValueError):
            assert_paired_query_ids(rows)


if __name__ == "__main__":
    unittest.main()
