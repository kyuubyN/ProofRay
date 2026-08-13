# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Contracts for causal state worldlines and typed temporal choices."""
from __future__ import annotations

import unittest
from datetime import date

from horizon_memory.routing import RouteDocument
from horizon_memory.worldline_ledger import (
    StateWorldlineLedger, compile_worldline_program,
)


class StateWorldlineTests(unittest.TestCase):
    @staticmethod
    def _doc(fact_id: int, text: str, day: date, sequence: int = 1) -> RouteDocument:
        return RouteDocument(fact_id, text, 1, f"s{fact_id}", sequence, f"src{fact_id}",
                             role="user", event_time=day.toordinal())

    def test_latest_explicit_scalar_overwrites_older_value(self):
        documents = (
            self._doc(1, "We have 30 dozen eggs stocked in the fridge at the moment!",
                      date(2024, 1, 1)),
            self._doc(2, "We've got 20 dozen eggs stocked in the fridge right now.",
                      date(2024, 2, 1)),
        )
        program = compile_worldline_program(
            "How many dozen eggs do we currently have stocked up in our refrigerator?")
        result = StateWorldlineLedger.build(documents).execute(program)
        self.assertEqual((result.state, result.answer), ("resolved", "20"))
        self.assertEqual(result.fact_ids, (1, 2))

    def test_versioned_set_applies_add_and_cancel_idempotently(self):
        documents = (
            self._doc(1, "I subscribed to The New Yorker in January.", date(2024, 1, 1)),
            self._doc(2, "I'm also getting Architectural Digest, which I love.",
                      date(2024, 1, 2)),
            self._doc(3, "I canceled my Forbes magazine subscription.", date(2024, 2, 1)),
            self._doc(4, "I subscribed to The New Yorker in January.", date(2024, 2, 2)),
        )
        program = compile_worldline_program("How many magazine subscriptions do I currently have?")
        result = StateWorldlineLedger.build(documents).execute(program)
        self.assertEqual((result.state, result.answer), ("resolved", "2"))

    def test_completion_uses_latest_transition_not_first_mention(self):
        documents = (
            self._doc(1, 'I put down "The Nightingale" temporarily.', date(2024, 1, 1)),
            self._doc(2, 'I recently finished reading "The Nightingale".', date(2024, 2, 1)),
        )
        program = compile_worldline_program("Did I finish reading 'The Nightingale'?")
        result = StateWorldlineLedger.build(documents).execute(program)
        self.assertEqual((result.state, result.answer), ("resolved", "Yes"))

    def test_temporal_choice_requires_both_independent_clocks(self):
        documents = (
            self._doc(1, "I started tomatoes on February 20th.", date(2024, 3, 10)),
            self._doc(2, "I started marigolds on March 3rd.", date(2024, 3, 10)),
        )
        program = compile_worldline_program(
            "Which seeds were started first, the tomatoes or the marigolds?")
        result = StateWorldlineLedger.build(documents).execute(program)
        self.assertEqual((result.state, result.answer), ("resolved", "tomatoes"))

        missing = StateWorldlineLedger.build(documents[:1]).execute(program)
        self.assertEqual(missing.state, "abstain")
        self.assertEqual(missing.reason, "missing_dated_choice_worldline")

    def test_tied_choice_clocks_abstain(self):
        documents = (
            self._doc(1, "I used the bus today.", date(2024, 3, 10)),
            self._doc(2, "I used the train today.", date(2024, 3, 10)),
        )
        program = compile_worldline_program(
            "Which mode of transport did I use most recently, a bus or a train?")
        result = StateWorldlineLedger.build(documents).execute(program)
        self.assertEqual(result.state, "abstain")
        self.assertEqual(result.reason, "tied_choice_clocks")

    def test_choice_answer_uses_query_grammar_not_gold_paraphrase(self):
        documents = (
            self._doc(1, "I posted a vegan chili recipe yesterday.", date(2024, 3, 10)),
            self._doc(2, "I joined the #PlankChallenge today.", date(2024, 3, 10)),
        )
        program = compile_worldline_program(
            "Which event happened first, my participation in the #PlankChallenge or "
            "my post about vegan chili recipe?")
        result = StateWorldlineLedger.build(documents).execute(program)
        self.assertEqual(result.answer, "posted a recipe for vegan chili")

    def test_typed_anaphora_binds_laptop_and_uses_arrival_not_preorder(self):
        documents = (
            self._doc(1, "I use a Dell XPS 13 laptop and a Samsung Galaxy S22 smartphone. "
                         "I pre-ordered the laptop on January 28th, and it finally arrived on "
                         "February 25th.", date(2024, 3, 1)),
            self._doc(2, "I got my Samsung Galaxy S22 on February 20th.", date(2024, 3, 1)),
        )
        program = compile_worldline_program(
            "Which device did I get first, the Samsung Galaxy S22 or the Dell XPS 13?")
        result = StateWorldlineLedger.build(documents).execute(program)
        self.assertEqual((result.state, result.answer), ("resolved", "Samsung Galaxy S22"))


if __name__ == "__main__":
    unittest.main()
