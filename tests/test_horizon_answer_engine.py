# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""HorizonAnswerEngine: the model-shaped facade over route -> verify -> compose."""
from __future__ import annotations

import unittest

from horizon_memory import (
    AnswerContextIntent, DEFAULT_PROFILE, DirectAnswer, DirectAnswerProposal, EngineProfile,
    HorizonAnswerEngine, RouteDocument,
)

SCOPE = 1


def _doc(fact_id: int, text: str) -> RouteDocument:
    return RouteDocument(fact_id, text, SCOPE, "s1", 1, f"doc:{fact_id}")


class ResolvedAnswerTests(unittest.TestCase):
    def setUp(self):
        self.engine = HorizonAnswerEngine(profile=DEFAULT_PROFILE, scope_id=SCOPE)
        self.documents = (
            _doc(1, "The Meridian project reduced compute cost by exactly 42 percent "
                    "compared to the previous baseline architecture across every workload."),
            _doc(2, "Standard atmospheric pressure at sea level is approximately "
                    "one hundred and one thousand three hundred and twenty five pascals."),
            _doc(3, "Meridian's cost reduction came from a redesigned caching layer that "
                    "eliminated redundant recomputation across adjacent pipeline stages."),
        )

    def test_resolves_and_is_verified(self):
        result = self.engine.answer("What percent did the Meridian project reduce cost by?",
                                    self.documents)
        self.assertEqual(result.state, "RESOLVED")
        self.assertTrue(result.resolved)
        self.assertGreater(len(result.claims), 0)
        self.assertGreater(len(result.answer_lines), 0)
        self.assertGreater(len(result.sources), 0)

    def test_answer_lines_are_a_subset_of_claims(self):
        result = self.engine.answer("What percent did the Meridian project reduce cost by?",
                                    self.documents)
        claim_texts = {c.text for c in result.claims}
        for line in result.answer_lines:
            self.assertIn(line.text, claim_texts)

    def test_dossiers_independently_verify_against_sources(self):
        result = self.engine.answer("What percent did the Meridian project reduce cost by?",
                                    self.documents)
        self.assertIsNotNone(result.core_dossier)
        self.assertIsNotNone(result.ranked_dossier)
        self.assertTrue(result.core_dossier.verify(result.sources, DEFAULT_PROFILE.answer_bytes))
        self.assertTrue(
            result.ranked_dossier.verify(result.sources, DEFAULT_PROFILE.acquisition_bytes))

    def test_answer_text_property_joins_answer_lines(self):
        result = self.engine.answer("What percent did the Meridian project reduce cost by?",
                                    self.documents)
        expected = "\n".join(line.text for line in result.answer_lines)
        self.assertEqual(result.answer_text, expected)
        self.assertEqual(result.evidence_text, expected)
        self.assertEqual(result.direct_answer.state, "not_attempted")
        self.assertEqual(result.direct_answer.text, "")

    def test_direct_answer_contract_rejects_unproven_resolution(self):
        with self.assertRaises(ValueError):
            DirectAnswer("resolved", "42", "extractive", ("doc:1",), False)
        candidate = DirectAnswer("candidate", "42", "extractive", ("doc:1",), False)
        self.assertEqual(candidate.text, "42")
        resolved = DirectAnswer("resolved", "42", "exact", ("doc:1",), True)
        self.assertTrue(resolved.proof_closed)

    def test_relevant_claim_outranks_unrelated_claim_in_answer(self):
        result = self.engine.answer("What percent did the Meridian project reduce cost by?",
                                    self.documents)
        answer_text = result.answer_text
        self.assertIn("42", answer_text)
        self.assertNotIn("pascals", answer_text)


class VersionedDocumentTests(unittest.TestCase):
    def test_resolves_when_document_version_is_not_one(self):
        # `answer()` used to write every document into its ephemeral store with a hardcoded
        # version=1 regardless of `RouteDocument.version`, so `HorizonVerifier.verify()`'s strict
        # `read.version != document.version` check always failed for any document whose stated
        # version wasn't 1, silently dropping it from the evidence pool (2026-08-19, found via
        # code review).
        engine = HorizonAnswerEngine(profile=DEFAULT_PROFILE, scope_id=SCOPE)
        documents = (RouteDocument(
            1, "The Meridian project reduced compute cost by exactly 42 percent compared to "
               "the previous baseline architecture across every workload.",
            SCOPE, "s1", 7, "doc:1"),)
        result = engine.answer("What percent did the Meridian project reduce cost by?", documents)
        self.assertEqual(result.state, "RESOLVED")
        self.assertIn("42", result.answer_text)


class ChineseQuestionTests(unittest.TestCase):
    # A trivial Chinese question with an unambiguous, verified answer in the same document used
    # to abstain completely through this real facade -- traced to `_WORD`'s regex having no
    # concept of CJK word boundaries (an entire clause matched as one opaque token, so the
    # compiled question and the extracted claim shared zero lexical overlap) plus the
    # answer-selection length tiers and sentence-terminator check both being calibrated for
    # English prose (2026-08-19, found via code review, fixed in raw_causal_channels.py and
    # answer_engine.py).
    def test_resolves_a_short_chinese_question_with_the_correct_answer(self):
        engine = HorizonAnswerEngine(profile=DEFAULT_PROFILE, scope_id=SCOPE)
        documents = (
            _doc(1, "北京的地铁系统在2023年运送了超过一百万名乘客。"),
            _doc(2, "上海的天气今天很好，适合出去散步和购物。"),
        )
        result = engine.answer("北京的地铁系统在2023年运送了多少名乘客？", documents)
        self.assertEqual(result.state, "RESOLVED")
        self.assertIn("一百万", result.answer_text)
        self.assertNotIn("天气", result.answer_text)

    def test_resolves_longer_chinese_prose_and_excludes_the_irrelevant_document(self):
        engine = HorizonAnswerEngine(profile=DEFAULT_PROFILE, scope_id=SCOPE)
        documents = (
            _doc(1, "根据国家统计局在2023年底发布的最新数据，北京市地铁系统全年累计运送乘客超过"
                    "一百二十万人次，创下了自该系统建成以来的历史最高纪录，这一显著增长主要归因于"
                    "三条新线路的正式开通运营，以及智能调度系统在全网范围内的全面升级和部署。"),
            _doc(2, "根据气象部门的观测记录，上海市在同一时期的平均气温维持在二十二摄氏度左右，"
                    "降水量明显低于历史同期水平，市民普遍反映这样的天气非常适合户外散步和购物活动，"
                    "许多公园和商业街区的客流量因此出现了显著增长。"),
        )
        result = engine.answer("北京市地铁系统在2023年累计运送了多少人次的乘客？", documents)
        self.assertEqual(result.state, "RESOLVED")
        self.assertIn("一百二十万", result.answer_text)
        self.assertNotIn("摄氏度", result.answer_text)


class AbstentionTests(unittest.TestCase):
    def test_abstains_when_scope_mismatches(self):
        # A document scoped differently from the engine/query can never be verified by
        # `HorizonVerifier` -- `route()` then finds zero verified items and returns ABSTENTION.
        # This is the cleanest reproducible way to exercise the facade's abstain path without
        # depending on ClaimGenerator's own relevance scoring (which returns *some* candidate
        # even for near-zero lexical overlap, so a "weak evidence" document is not guaranteed
        # to abstain at the routing stage).
        engine = HorizonAnswerEngine(profile=DEFAULT_PROFILE, scope_id=SCOPE)
        documents = (RouteDocument(
            1, "Some content the query will never be able to verify against.",
            SCOPE + 1, "s1", 1, "doc:1"),)
        result = engine.answer("What is the answer?", documents)
        self.assertNotEqual(result.state, "RESOLVED")
        self.assertFalse(result.resolved)
        self.assertEqual(result.claims, ())
        self.assertEqual(result.answer_lines, ())
        self.assertEqual(result.sources, ())
        self.assertIsNone(result.core_dossier)
        self.assertIsNone(result.ranked_dossier)
        self.assertEqual(result.answer_bytes, 0)


class ProfileIsRespectedTests(unittest.TestCase):
    def test_tighter_answer_bytes_budget_is_honored(self):
        documents = (
            _doc(1, "The Solstice engine achieves ninety nine percent accuracy on the "
                    "benchmark suite according to the independently reproduced evaluation."),
            _doc(2, "The Solstice engine's accuracy comes from a three-stage verification "
                    "pipeline that cross-checks every candidate answer against its source."),
        )
        tight = EngineProfile(name="tight", answer_bytes=1024, acquisition_bytes=8192)
        engine = HorizonAnswerEngine(profile=tight, scope_id=SCOPE)
        result = engine.answer("What accuracy does the Solstice engine achieve?", documents)
        self.assertEqual(result.state, "RESOLVED")
        self.assertLessEqual(result.answer_bytes, tight.answer_bytes)

    def test_hpps_selector_is_opt_in_and_exposes_incomplete_closure(self):
        profile = EngineProfile(name="hpps", answer_selector="hpps", hpps_max_results=1)
        engine = HorizonAnswerEngine(profile=profile, scope_id=SCOPE)
        documents = (
            _doc(1, "北京地铁在二零二三年运送了一百二十万名乘客。"),
            _doc(2, "上海今天气温二十二度，适合户外活动。"),
        )
        result = engine.answer("北京地铁运送了多少名乘客？", documents)
        self.assertTrue(result.resolved)
        self.assertEqual(result.selector, "hpps")
        self.assertIn("一百二十万", result.answer_text)
        self.assertIsNotNone(result.selector_proof_closed)
        self.assertTrue(all(line.text in {doc.text for doc in documents}
                            for line in result.answer_lines))

    def test_hpps_exploration_reserve_is_bounded_and_never_fakes_closure(self):
        profile = EngineProfile(
            name="hpps-exploration", answer_selector="hpps",
            hpps_max_results=2, hpps_exploration_reserve=2)
        engine = HorizonAnswerEngine(profile=profile, scope_id=SCOPE)
        documents = (
            _doc(1, "北京地铁在二零二三年运送了一百二十万名乘客。"),
            _doc(2, "这项统计由城市交通部门在年度报告中发布。"),
            _doc(3, "上海今天气温二十二度，适合户外活动。"),
        )
        result = engine.answer("北京地铁运送了多少名乘客？", documents)
        self.assertTrue(result.resolved)
        self.assertEqual(result.selector, "hpps")
        self.assertLessEqual(len(result.answer_lines), 2)
        self.assertFalse(result.selector_proof_closed)
        self.assertTrue(all(line.text in {doc.text for doc in documents}
                            for line in result.answer_lines))

    def test_full_dossier_render_is_explicit_and_preserves_verified_composition(self):
        profile = EngineProfile(name="full", answer_render_mode="full_dossier")
        engine = HorizonAnswerEngine(profile=profile, scope_id=SCOPE)
        documents = (
            _doc(1, "The Meridian project reduced compute cost by exactly 42 percent compared "
                    "with the previous baseline architecture across every workload."),
            _doc(2, "The reduction followed a cache redesign that removed duplicate work "
                    "between adjacent processing stages in the complete pipeline."),
        )
        result = engine.answer("How did Meridian reduce compute cost?", documents)
        self.assertTrue(result.resolved)
        self.assertEqual(result.answer_lines, result.claims)

    def test_turn_intents_are_typed_and_unknown_fact_ids_fail_closed(self):
        engine = HorizonAnswerEngine(scope_id=SCOPE)
        documents = (_doc(1, "The first observed turn established that Meridian saved 42 percent."),)
        intent = AnswerContextIntent("turn:0", "What did Meridian save?", (1,))
        self.assertTrue(engine.answer("Summarize the result", documents,
                                      context_intents=(intent,)).resolved)
        with self.assertRaises(ValueError):
            engine.answer("Summarize the result", documents, context_intents=(
                AnswerContextIntent("bad", "Unknown", (99,)),))


class DirectAnswerReaderBoundaryTests(unittest.TestCase):
    class Extract42:
        def propose(self, question, evidence):
            line = next(item for item in evidence if "42" in item.text)
            return DirectAnswerProposal("42", "test_extractive", (line.source_id,))

    class Hallucinate:
        def propose(self, question, evidence):
            return DirectAnswerProposal("99", "test_extractive", (evidence[0].source_id,))

    class UnknownSource:
        def propose(self, question, evidence):
            return DirectAnswerProposal("42", "test_extractive", ("unknown",))

    def setUp(self):
        self.documents = (_doc(
            1, "The Meridian project reduced compute cost by exactly 42 percent across all runs."),)

    def test_source_exact_proposal_is_candidate_and_evidence_is_preserved(self):
        result = HorizonAnswerEngine(
            scope_id=SCOPE, direct_answer_reader=self.Extract42()).answer(
                "What percent did Meridian reduce cost by?", self.documents)
        self.assertEqual(result.direct_answer.state, "candidate")
        self.assertEqual(result.direct_answer.text, "42")
        self.assertFalse(result.direct_answer.proof_closed)
        self.assertIn("42", result.evidence_text)

    def test_invented_span_fails_closed_without_erasing_evidence(self):
        result = HorizonAnswerEngine(
            scope_id=SCOPE, direct_answer_reader=self.Hallucinate()).answer(
                "What percent did Meridian reduce cost by?", self.documents)
        self.assertEqual(result.direct_answer.state, "abstain")
        self.assertEqual(result.direct_answer.residual, ("text_does_not_reopen",))
        self.assertIn("42", result.evidence_text)

    def test_unknown_source_fails_closed(self):
        result = HorizonAnswerEngine(
            scope_id=SCOPE, direct_answer_reader=self.UnknownSource()).answer(
                "What percent did Meridian reduce cost by?", self.documents)
        self.assertEqual(result.direct_answer.state, "abstain")
        self.assertEqual(result.direct_answer.residual, ("unknown_source_id",))


if __name__ == "__main__":
    unittest.main()
