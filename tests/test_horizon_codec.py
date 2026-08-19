# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""HPC contracts: compression is exact, dated, bounded, and independently verifiable."""
from __future__ import annotations

import hashlib
import unittest
from datetime import date

from horizon_memory.codec import (
    ProofCarryingCodec, compile_query_equation, execute_exact, semantic_charges,
)
from horizon_memory.codec import _term_key
from horizon_memory.boundary_ledger import BoundaryFiberLedger
from horizon_memory.evidence import EvidenceItem, EvidencePack
from horizon_memory.measurement_ledger import EventLedger
from horizon_memory.routing import RouteDocument
from horizon_memory.relational_ledger import RelationalTensorLedger
from horizon_memory.temporal_gauge import TemporalReferenceGauge
from horizon_memory.constraint_ledger import ConstraintClosureLedger
from horizon_memory.worldline_ledger import StateWorldlineLedger


class ProofCarryingCodecTests(unittest.TestCase):
    def _pack(self):
        text = "We discussed lunch. I bought a smoker today. It was not expensive."
        item = EvidenceItem(1, "session-a", 1, None, text, verifier_state="verified",
                            sequence=3, retrieval_rank=1, event_time=738900)
        return text, EvidencePack.build("q", (item,), generation_id=2, recovery_reason="bulk")

    def test_excerpt_is_exact_parent_span_with_digest(self):
        text, parent = self._pack()
        compressed, report = ProofCarryingCodec().compress("What appliance did I buy?", parent)
        item = compressed.items[0]
        self.assertEqual(text[item.content_span[0]:item.content_span[1]], item.content)
        self.assertEqual(item.parent_sha256, hashlib.sha256(text.encode()).hexdigest())
        self.assertTrue(ProofCarryingCodec.verify(compressed, parent))
        self.assertTrue(report.exact_spans)

    def test_render_exposes_date_without_putting_it_inside_the_quote(self):
        _, parent = self._pack()
        compressed, _ = ProofCarryingCodec().compress("smoker", parent)
        rendered = compressed.render_untrusted()
        self.assertIn("date=2024-01-15", rendered)
        self.assertIn("[E1 date=2024-01-15]", rendered)
        self.assertNotIn("session-a#fact-1", rendered)
        self.assertEqual(compressed.items[0].content, "I bought a smoker today.")

    def test_tampered_parent_fails_proof(self):
        _, parent = self._pack()
        compressed, _ = ProofCarryingCodec().compress("smoker", parent)
        tampered = EvidencePack.build("q", (
            EvidenceItem(1, "session-a", 1, None, "different", verifier_state="verified"),
        ), generation_id=2, recovery_reason="bulk")
        self.assertFalse(ProofCarryingCodec.verify(compressed, tampered))

    def test_term_key_strips_only_a_literal_possessive_suffix(self):
        # `_term_key` used to call `.rstrip("'s")`, which strips a *character set* (any
        # trailing run of "'" or "s"), not the literal suffix -- corrupting ordinary words that
        # simply end in "s" ("boss" -> "bo", "process" -> "proce") instead of only normalizing a
        # genuine possessive (2026-08-19, found via code review).
        self.assertEqual(_term_key("boss"), "boss")
        self.assertEqual(_term_key("process"), "process")
        self.assertEqual(_term_key("world's"), "world")

    def test_semantic_charges_track_number_negation_quote_and_tag(self):
        charges = semantic_charges('Not "Project Red": $720 in 4 days #Launch')
        self.assertIn("neg:not", charges)
        self.assertTrue(any(charge.startswith("num:") for charge in charges))
        self.assertIn("quote:project red", charges)
        self.assertIn("tag:#launch", charges)

    def test_budget_never_slices_a_measurement(self):
        _, parent = self._pack()
        compressed, _ = ProofCarryingCodec().compress("smoker", parent, max_chars=1)
        self.assertEqual(compressed.items, ())

    def test_query_equation_is_typed_but_contains_no_answer(self):
        plan = compile_query_equation("How many days passed between both events?", 738900)
        self.assertIn("op=interval", plan)
        self.assertIn("query_date=2024-01-15", plan)
        self.assertNotIn("answer", plan)

    def test_query_equation_distinguishes_event_date_and_sum(self):
        self.assertIn("op=event_date", compile_query_equation("When did I volunteer?"))
        self.assertIn("op=sum", compile_query_equation("How many years in total did I study?"))

    def test_query_equation_separates_duration_count_state_and_age(self):
        self.assertIn("op=interval", compile_query_equation(
            "How many days had passed since I bought shoes when I noticed a broken lace?"))
        self.assertIn("op=relative_time", compile_query_equation(
            "How many months have passed since I last visited a museum?"))
        self.assertIn("op=sum", compile_query_equation(
            "How many days did I spend attending workshops in April?"))
        self.assertIn("op=latest_state", compile_query_equation(
            "How many magazine subscriptions do I currently have?"))
        self.assertIn("op=age_at_event", compile_query_equation(
            "How many years will I be when Rachel gets married?"))

    def test_exact_executor_resolves_relative_months_from_proven_dates(self):
        item = EvidenceItem(1, "s", 1, None, "I attended the festival today.",
                            verifier_state="verified", retrieval_rank=1,
                            event_time=date(2021, 6, 1).toordinal())
        parent = EvidencePack.build("q", (item,), generation_id=1, recovery_reason="bulk")
        compressed, _ = ProofCarryingCodec().compress(
            "How many months ago did I attend the festival?", parent,
            query_time=date(2021, 10, 2).toordinal())
        result = execute_exact(compressed)
        self.assertEqual(result.state, "resolved")
        self.assertEqual(result.answer, "4 months ago")

    def test_relative_time_uses_when_clause_as_contextual_observer_clock(self):
        items = (
            EvidenceItem(1, "contract", 1, None,
                         "user: I signed a contract with my first client today.",
                         verifier_state="verified", sequence=1, retrieval_rank=1,
                         event_time=date(2023, 3, 6).toordinal()),
            EvidenceItem(2, "launch", 1, None,
                         "user: I launched my website today and signed my first client.",
                         verifier_state="verified", sequence=2, retrieval_rank=2,
                         event_time=date(2023, 3, 1).toordinal()),
        )
        parent = EvidencePack.build("q", items, generation_id=1, recovery_reason="bulk")
        compressed, _ = ProofCarryingCodec().compress(
            "How many days ago did I launch my website when I signed a contract with my first client?",
            parent, query_time=date(2023, 3, 25).toordinal())
        result = execute_exact(compressed)
        self.assertEqual(result.state, "resolved")
        self.assertEqual(result.answer, "5 days ago")
        self.assertEqual(result.reason, "contextual_observer_difference")
        self.assertEqual(set(result.citation_labels), {"E1", "E2"})

    def test_contextual_observer_cannot_precede_observed_event(self):
        items = (
            EvidenceItem(1, "launch", 1, None, "user: I launched my website today.",
                         verifier_state="verified", sequence=1, retrieval_rank=1,
                         event_time=date(2023, 3, 6).toordinal()),
            EvidenceItem(2, "contract", 1, None, "user: I signed my first client today.",
                         verifier_state="verified", sequence=2, retrieval_rank=2,
                         event_time=date(2023, 3, 1).toordinal()),
        )
        parent = EvidencePack.build("q", items, generation_id=1, recovery_reason="bulk")
        compressed, _ = ProofCarryingCodec().compress(
            "How many days ago did I launch my website when I signed my first client?",
            parent, query_time=date(2023, 3, 25).toordinal())
        result = execute_exact(compressed)
        self.assertEqual(result.state, "abstain")
        self.assertEqual(result.reason, "context_precedes_observed_event")

    def test_contextual_observer_refines_yesterday_inside_evidence_span(self):
        items = (
            EvidenceItem(1, "class", 1, None,
                         "user: I attended the baking class yesterday.",
                         verifier_state="verified", sequence=1, retrieval_rank=1,
                         event_time=date(2023, 3, 21).toordinal()),
            EvidenceItem(2, "cake", 1, None,
                         "user: I made my friend's birthday cake today.",
                         verifier_state="verified", sequence=2, retrieval_rank=2,
                         event_time=date(2023, 4, 10).toordinal()),
        )
        parent = EvidencePack.build("q", items, generation_id=1, recovery_reason="bulk")
        compressed, _ = ProofCarryingCodec().compress(
            "How many days ago did I attend the baking class when I made my friend's birthday cake?",
            parent, query_time=date(2023, 4, 15).toordinal())
        result = execute_exact(compressed)
        self.assertEqual((result.state, result.answer), ("resolved", "21 days ago"))

    def test_object_calendar_charge_does_not_replace_event_clock(self):
        item = EvidenceItem(
            1, "magazine", 1, None, "user: I read the March 15th issue today.",
            verifier_state="verified", sequence=1, retrieval_rank=1,
            event_time=date(2023, 3, 27).toordinal())
        parent = EvidencePack.build("q", (item,), generation_id=1, recovery_reason="bulk")
        compressed, _ = ProofCarryingCodec().compress(
            "How many days ago did I read the March 15th issue?", parent,
            query_time=date(2023, 4, 8).toordinal())
        result = execute_exact(compressed)
        self.assertEqual((result.state, result.answer), ("resolved", "12 days ago"))

    def test_exact_executor_prefers_explicit_holiday_over_session_header(self):
        item = EvidenceItem(1, "s", 1, None, "I volunteer on Valentine's Day.",
                            verifier_state="verified", retrieval_rank=1,
                            event_time=date(2023, 4, 2).toordinal())
        parent = EvidencePack.build("q", (item,), generation_id=1, recovery_reason="bulk")
        compressed, _ = ProofCarryingCodec().compress(
            "When did I volunteer?", parent, query_time=date(2023, 4, 2).toordinal())
        result = execute_exact(compressed)
        self.assertEqual(result.answer, "February 14th")

    def test_exact_executor_does_not_guess_unimplemented_sum(self):
        item = EvidenceItem(1, "s", 1, None, "I studied for four years.",
                            verifier_state="verified", retrieval_rank=1)
        parent = EvidencePack.build("q", (item,), generation_id=1, recovery_reason="bulk")
        compressed, _ = ProofCarryingCodec().compress("How many years in total?", parent)
        self.assertEqual(execute_exact(compressed).state, "unsupported")

    def test_morphology_connects_volunteered_to_volunteer(self):
        item = EvidenceItem(1, "s", 1, None, "user: I volunteered at the shelter today.",
                            verifier_state="verified", retrieval_rank=1,
                            event_time=date(2024, 2, 14).toordinal())
        parent = EvidencePack.build("q", (item,), generation_id=1, recovery_reason="bulk")
        compressed, _ = ProofCarryingCodec().compress("When did I volunteer at the shelter?", parent)
        self.assertIn("focus=E1", compressed.query_plan)
        self.assertEqual(execute_exact(compressed).state, "resolved")

    def test_exact_interval_uses_two_separately_bound_events(self):
        items = (
            EvidenceItem(1, "s1", 1, None, "user: I finished reading The Nightingale today.",
                         verifier_state="verified", sequence=1, retrieval_rank=1,
                         event_time=date(2024, 1, 10).toordinal()),
            EvidenceItem(2, "s2", 1, None,
                         "user: I started reading The Hitchhiker's Guide to the Galaxy today.",
                         verifier_state="verified", sequence=2, retrieval_rank=2,
                         event_time=date(2024, 1, 11).toordinal()),
        )
        parent = EvidencePack.build("q", items, generation_id=1, recovery_reason="bulk")
        compressed, _ = ProofCarryingCodec().compress(
            "How many days passed between the day I finished reading The Nightingale and the day "
            "I started reading The Hitchhiker's Guide to the Galaxy?", parent)
        result = execute_exact(compressed)
        self.assertEqual(result.state, "resolved")
        self.assertEqual(result.answer, "1 day")
        self.assertEqual(set(result.citation_labels), {"E1", "E2"})

    def test_exact_interval_abstains_when_operand_binding_is_tied(self):
        items = tuple(EvidenceItem(
            fact_id, f"s{fact_id}", 1, None, "user: I attended the same event today.",
            verifier_state="verified", sequence=fact_id, retrieval_rank=fact_id,
            event_time=date(2024, 1, 10 + fact_id).toordinal()) for fact_id in (1, 2, 3))
        parent = EvidencePack.build("q", items, generation_id=1, recovery_reason="bulk")
        compressed, _ = ProofCarryingCodec().compress(
            "How many days passed between attending the same event and attending the same event?",
            parent)
        self.assertEqual(execute_exact(compressed).state, "abstain")

    def test_exact_sum_executes_against_full_write_time_event_ledger(self):
        documents = (
            RouteDocument(1, "I raised $250 for a food bank.", 1, "s1", 1, "a", role="user"),
            RouteDocument(2, "Our charity event raised $600 for a shelter.",
                          1, "s2", 1, "b", role="user"),
        )
        parent = EvidencePack.build("q", (
            EvidenceItem(1, "s1", 1, None, documents[0].text,
                         verifier_state="verified", retrieval_rank=1),
        ), generation_id=1, recovery_reason="bulk")
        compressed, _ = ProofCarryingCodec().compress(
            "How much money did I raise through all charity events?", parent)
        result = execute_exact(compressed, EventLedger.build(documents))
        self.assertEqual(result.state, "resolved")
        self.assertEqual(result.answer, "$850")
        self.assertEqual(result.fact_ids, (1, 2))

    def test_exact_distinct_count_executes_with_orbit_deduplication(self):
        documents = (
            RouteDocument(1, "I visited The Art Cube on 2/15.", 1, "s1", 1, "a", role="user"),
            RouteDocument(2, "I visited The Art Cube again in February.",
                          1, "s2", 1, "b", role="user"),
            RouteDocument(3, "I went to the History Museum in February.",
                          1, "s3", 1, "c", role="user"),
        )
        parent = EvidencePack.build("q", (
            EvidenceItem(1, "s1", 1, None, documents[0].text,
                         verifier_state="verified", retrieval_rank=1),
        ), generation_id=1, recovery_reason="bulk")
        compressed, _ = ProofCarryingCodec().compress(
            "How many different museums or galleries did I visit in February?", parent)
        result = execute_exact(compressed, EventLedger.build(documents))
        self.assertEqual(result.state, "resolved")
        self.assertEqual(result.answer, "2")
        self.assertEqual(set(result.fact_ids), {1, 2, 3})

    def test_exact_executor_dispatches_versioned_worldline_state(self):
        documents = (
            RouteDocument(1, "We have 30 dozen eggs stocked in the fridge at the moment!",
                          1, "s1", 1, "a", role="user",
                          event_time=date(2024, 1, 1).toordinal()),
            RouteDocument(2, "We've got 20 dozen eggs stocked in the fridge right now.",
                          1, "s2", 2, "b", role="user",
                          event_time=date(2024, 2, 1).toordinal()),
        )
        parent = EvidencePack.build("q", (
            EvidenceItem(2, "s2", 1, None, documents[1].text,
                         verifier_state="verified", retrieval_rank=1),
        ), generation_id=1, recovery_reason="bulk")
        compressed, _ = ProofCarryingCodec().compress(
            "How many dozen eggs do we currently have stocked up in our refrigerator?", parent)
        result = execute_exact(
            compressed, worldline_ledger=StateWorldlineLedger.build(documents))
        self.assertEqual((result.state, result.answer), ("resolved", "20"))
        self.assertEqual(result.fact_ids, (1, 2))

    def test_exact_executor_dispatches_unique_boundary_fiber(self):
        day = date(2024, 3, 15).toordinal()
        documents = (RouteDocument(
            1, "I just got a smoker today.", 1, "s1", 1, "a", role="user",
            event_time=day),)
        parent = EvidencePack.build("q", (
            EvidenceItem(1, "s1", 1, None, documents[0].text,
                         verifier_state="verified", retrieval_rank=1, event_time=day),
        ), generation_id=1, recovery_reason="bulk")
        compressed, _ = ProofCarryingCodec().compress(
            "What kitchen appliance did I buy 10 days ago?", parent,
            query_time=date(2024, 3, 25).toordinal())
        result = execute_exact(
            compressed, boundary_ledger=BoundaryFiberLedger.build(documents))
        self.assertEqual((result.state, result.answer), ("resolved", "a smoker"))
        self.assertEqual(result.fact_ids, (1,))

    def test_exact_executor_dispatches_relational_tensor_before_lexical_binding(self):
        day = date(2024, 1, 24).toordinal()
        documents = (
            RouteDocument(1, "I bought my laptop backpack on 1/15.",
                          1, "s1", 1, "a", role="user", event_time=day),
            RouteDocument(2, "My laptop backpack arrived on 1/20.",
                          1, "s2", 2, "b", role="user", event_time=day),
        )
        parent = EvidencePack.build("q", tuple(EvidenceItem(
            document.fact_id, document.session_id, 1, None, document.text,
            verifier_state="verified", retrieval_rank=document.fact_id, event_time=day)
            for document in documents), generation_id=1, recovery_reason="bulk")
        compressed, _ = ProofCarryingCodec().compress(
            "How many days did it take for my laptop backpack to arrive after I bought it?", parent)
        result = execute_exact(
            compressed, relational_ledger=RelationalTensorLedger.build(documents))
        self.assertEqual((result.state, result.answer), ("resolved", "5 days"))
        self.assertEqual(result.fact_ids, (1, 2))

    def test_exact_executor_dispatches_temporal_reference_gauge(self):
        event_day = date(2024, 3, 15).toordinal()
        documents = (RouteDocument(
            1, "I just got a smoker today.", 1, "s1", 1, "a", role="user",
            event_time=event_day),)
        parent = EvidencePack.build("q", (
            EvidenceItem(1, "s1", 1, None, documents[0].text,
                         verifier_state="verified", retrieval_rank=1, event_time=event_day),
        ), generation_id=1, recovery_reason="bulk")
        compressed, _ = ProofCarryingCodec().compress(
            "How many days ago did I buy a smoker?", parent,
            query_time=date(2024, 3, 25).toordinal())
        result = execute_exact(
            compressed, temporal_gauge=TemporalReferenceGauge.build(documents))
        self.assertEqual((result.state, result.answer), ("resolved", "10 days ago"))
        self.assertEqual(result.fact_ids, (1,))

    def test_exact_executor_dispatches_closed_world_negative(self):
        documents = (RouteDocument(
            1, "My laptop backpack arrived on 1/20.", 1, "s1", 1, "a", role="user",
            event_time=date(2024, 1, 24).toordinal()),)
        parent = EvidencePack.build("q", (
            EvidenceItem(1, "s1", 1, None, documents[0].text,
                         verifier_state="verified", retrieval_rank=1,
                         event_time=documents[0].event_time),
        ), generation_id=1, recovery_reason="bulk")
        compressed, _ = ProofCarryingCodec().compress(
            "How many days did it take for my iPad case to arrive after I bought it?", parent)
        result = execute_exact(
            compressed, constraint_ledger=ConstraintClosureLedger.build(documents))
        self.assertEqual(result.state, "resolved")
        self.assertEqual(result.answer, "The information provided is not enough")


if __name__ == "__main__":
    unittest.main()
