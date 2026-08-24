# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""HorizonAnswerEngine: the model-shaped facade over route -> verify -> compose."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import unittest

from horizon_memory import (
    AnswerContextIntent, DEFAULT_PROFILE, DirectAnswer, DirectAnswerProposal,
    DirectAnswerResolution, EngineProfile,
    HorizonAnswerEngine, RouteDocument,
)
from horizon_memory.answer_engine import _fit_rendered_answer_budget

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

    def test_physical_render_budget_counts_newline_separators(self):
        lines = (
            self._line("alpha"), self._line("bravo"), self._line("c"),
        )
        fitted = _fit_rendered_answer_budget(lines, 11)
        self.assertEqual(tuple(line.text for line in fitted), ("alpha", "bravo"))
        self.assertEqual(len("\n".join(line.text for line in fitted).encode()), 11)

    def test_direct_answer_contract_rejects_unproven_resolution(self):
        with self.assertRaises(ValueError):
            DirectAnswer("resolved", "42", "extractive", ("doc:1",), False)
        candidate = DirectAnswer("candidate", "42", "extractive", ("doc:1",), False)
        self.assertEqual(candidate.text, "42")
        with self.assertRaises(ValueError):
            DirectAnswer("resolved", "42", "exact", ("doc:1",), True)
        resolved = DirectAnswer("resolved", "42", "exact", ("doc:1",), True,
                                certificate=b"proof")
        self.assertTrue(resolved.proof_closed)

    def test_relevant_claim_outranks_unrelated_claim_in_answer(self):
        result = self.engine.answer("What percent did the Meridian project reduce cost by?",
                                    self.documents)
        answer_text = result.answer_text
        self.assertIn("42", answer_text)
        self.assertNotIn("pascals", answer_text)

    @staticmethod
    def _line(text: str):
        from horizon_memory import AnsweredClaim
        return AnsweredClaim(text, 1, f"source:{text}", 0.0)

    def test_zero_score_ranked_fallback_abstains_instead_of_dumping_arbitrary_source(self):
        result = self.engine.answer(
            "What is the answer?",
            (_doc(99, "Completely unrelated content about migratory bird patterns."),))
        self.assertFalse(result.resolved)
        self.assertEqual(result.answer_text, "")


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


class PreparedRuntimeCacheTests(unittest.TestCase):
    def test_default_engine_keeps_request_runtime_ephemeral(self):
        engine = HorizonAnswerEngine(scope_id=SCOPE)
        engine.answer("What reduction did Meridian record?", (
            _doc(1, "Meridian recorded a verified reduction of 42 percent."),))
        self.assertIsNone(engine._prepared_runtime)
        with self.assertRaises(TypeError):
            HorizonAnswerEngine(reuse_prepared_runtime=1)  # type: ignore[arg-type]

    def test_exact_document_snapshot_reuses_runtime_and_changed_snapshot_closes_old_store(self):
        engine = HorizonAnswerEngine(scope_id=SCOPE, reuse_prepared_runtime=True)
        first = (_doc(1, "Meridian recorded a verified reduction of 42 percent."),)
        engine.answer("What reduction did Meridian record?", first)
        initial = engine._prepared_runtime
        self.assertIsNotNone(initial)
        initial_path = Path(initial.workdir)
        engine.answer("What did Meridian record?", first)
        self.assertIs(engine._prepared_runtime, initial)
        second = (_doc(2, "Orion recorded a verified reduction of 17 percent."),)
        engine.answer("What reduction did Orion record?", second)
        self.assertIsNot(engine._prepared_runtime, initial)
        self.assertFalse(initial_path.exists())
        engine.close()
        self.assertIsNone(engine._prepared_runtime)

    def test_shared_engine_serializes_concurrent_snapshot_replacement(self):
        engine = HorizonAnswerEngine(scope_id=SCOPE, reuse_prepared_runtime=True)
        meridian = (_doc(1, "Meridian recorded a verified reduction of 42 percent."),)
        orion = (_doc(2, "Orion recorded a verified reduction of 17 percent."),)

        def answer(index):
            if index % 2:
                result = engine.answer("What reduction did Meridian record?", meridian)
                return "42" in result.final_answer_text
            result = engine.answer("What reduction did Orion record?", orion)
            return "17" in result.final_answer_text

        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertTrue(all(pool.map(answer, range(24))))
        engine.close()

    def test_opt_in_reuse_is_byte_and_proof_equivalent_to_ephemeral_engine(self):
        documents = (
            RouteDocument(1, "My camping trip lasted 3 days.", SCOPE, "trip:a", 1,
                          "chat:1", role="user"),
            RouteDocument(2, "My second camping trip lasted 5 days.", SCOPE, "trip:b", 1,
                          "chat:2", role="user"),
        )
        ephemeral = HorizonAnswerEngine(scope_id=SCOPE, allow_scope_fallback=True)
        reused = HorizonAnswerEngine(
            scope_id=SCOPE, allow_scope_fallback=True, reuse_prepared_runtime=True)
        for question in (
                "How many days did I spend camping in total?",
                "How long were my camping trips altogether?"):
            expected = ephemeral.answer(question, documents)
            actual = reused.answer(question, documents)
            self.assertEqual(actual, expected)
        self.assertIsNotNone(reused._prepared_runtime)
        reused.close()

    def test_failed_answer_never_reuses_prepared_runtime(self):
        engine = HorizonAnswerEngine(scope_id=SCOPE, reuse_prepared_runtime=True)
        documents = (_doc(1, "Meridian recorded a verified value of 42."),)
        with self.assertRaises(ValueError):
            engine.answer("What value?", documents, context_intents=(
                AnswerContextIntent("bad", "Unknown fact", (99,)),))
        self.assertIsNone(engine._prepared_runtime)


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

    def test_duplicate_document_fact_ids_fail_before_routing_or_resolution(self):
        engine = HorizonAnswerEngine(scope_id=SCOPE)
        documents = (
            _doc(1, "The first source records a verified value of 40."),
            _doc(1, "A conflicting source reuses the identity with a value of 99."),
        )
        with self.assertRaisesRegex(ValueError, "unique FactIds"):
            engine.answer("What value was recorded?", documents)

    def test_duplicate_context_intent_ids_fail_in_python_core(self):
        engine = HorizonAnswerEngine(scope_id=SCOPE)
        documents = (_doc(1, "The source records a verified value of 42."),)
        with self.assertRaisesRegex(ValueError, "unique intent IDs"):
            engine.answer("What value was recorded?", documents, context_intents=(
                AnswerContextIntent("turn:0", "First observation", (1,)),
                AnswerContextIntent("turn:0", "Second observation", (1,)),
            ))

    def test_context_intent_rejects_boolean_identity_and_clock(self):
        with self.assertRaises(ValueError):
            AnswerContextIntent("turn:0", "Observed", (True,))
        with self.assertRaises(ValueError):
            AnswerContextIntent("turn:0", "Observed", (1,), turn_index=True)

    def test_route_document_version_is_bounded_by_storage_u32_domain(self):
        accepted = RouteDocument(
            1, "The maximum version remains representable.", SCOPE, "s1",
            (1 << 32) - 1, "doc:max-version")
        self.assertEqual(accepted.version, (1 << 32) - 1)
        with self.assertRaisesRegex(ValueError, "invalid fact identity"):
            RouteDocument(
                2, "This version cannot be persisted.", SCOPE, "s1", 1 << 32,
                "doc:overflow-version")
        with self.assertRaisesRegex(ValueError, "invalid fact identity"):
            RouteDocument(
                1 << 62, "This FactId cannot be persisted.", SCOPE, "s1", 1,
                "doc:overflow-fact")
        with self.assertRaisesRegex(ValueError, "invalid fact identity"):
            RouteDocument(
                3, "This scope cannot be persisted.", 1 << 32, "s1", 1,
                "doc:overflow-scope")
        for field_values in ((True, SCOPE, 1), (1, True, 1), (1, SCOPE, True)):
            with self.subTest(field_values=field_values), self.assertRaisesRegex(
                    ValueError, "invalid fact identity"):
                RouteDocument(
                    field_values[0], "Boolean identity is invalid.", field_values[1],
                    "s1", field_values[2], "doc:boolean")
        with self.assertRaisesRegex(ValueError, "span"):
            RouteDocument(
                4, "An invalid source interval.", SCOPE, "s1", 1, "doc:span",
                span=(7, 7))

    def test_context_intent_cannot_cross_sessions(self):
        engine = HorizonAnswerEngine(scope_id=SCOPE, allow_scope_fallback=True)
        documents = (
            RouteDocument(1, "Alice recorded one observation.", SCOPE, "session:1", 1,
                          "chat:1"),
            RouteDocument(2, "Bob recorded another observation.", SCOPE, "session:2", 1,
                          "chat:2"),
        )
        with self.assertRaisesRegex(ValueError, "crosses document sessions"):
            engine.answer("What was observed?", documents, context_intents=(
                AnswerContextIntent("mixed", "Observed facts", (1, 2)),))

    def test_context_intent_session_must_match_its_documents(self):
        engine = HorizonAnswerEngine(scope_id=SCOPE, allow_scope_fallback=True)
        documents = (RouteDocument(
            1, "Alice recorded one observation.", SCOPE, "session:1", 1, "chat:1"),)
        with self.assertRaisesRegex(ValueError, "session does not match"):
            engine.answer("What was observed?", documents, context_intents=(
                AnswerContextIntent(
                    "wrong-session", "Observed fact", (1,), session_id="session:2"),))


class DirectAnswerReaderBoundaryTests(unittest.TestCase):
    class SumCertificate:
        def compact(self):
            return b"test-sum:40+2=42"

        def reopen(self, blob, question, evidence):
            return (blob == b"test-sum:40+2=42" and "sum" in question.casefold()
                    and any("40 plus 2" in item.text for item in evidence))

        def reopen_resolution(self, blob, question, evidence, *, text, method, source_ids):
            return (self.reopen(blob, question, evidence) and text == "42"
                    and method == "test_certified_sum"
                    and source_ids == (evidence[0].source_id,))

    class ResolveSum:
        def resolve(self, question, evidence):
            return DirectAnswerResolution(
                "42", "test_certified_sum", (evidence[0].source_id,),
                DirectAnswerReaderBoundaryTests.SumCertificate())

    class BadCertificate(SumCertificate):
        def reopen(self, blob, question, evidence):
            return False

    class RejectBadSum:
        def resolve(self, question, evidence):
            return DirectAnswerResolution(
                "42", "test_bad_sum", (evidence[0].source_id,),
                DirectAnswerReaderBoundaryTests.BadCertificate())

    class LegacyCertificate:
        def compact(self):
            return b"legacy-proof"

        def reopen(self, blob, question, evidence):
            return True

    class ResolveWithLegacyCertificate:
        def resolve(self, question, evidence):
            return DirectAnswerResolution(
                "42", "legacy", (evidence[0].source_id,),
                DirectAnswerReaderBoundaryTests.LegacyCertificate())

    class MutateResolution:
        def __init__(self, *, text="42", method="test_certified_sum"):
            self.text = text
            self.method = method

        def resolve(self, question, evidence):
            return DirectAnswerResolution(
                self.text, self.method, (evidence[0].source_id,),
                DirectAnswerReaderBoundaryTests.SumCertificate())

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

    class CaptureResolverPool:
        def __init__(self):
            self.evidence = ()

        def resolve(self, question, evidence):
            self.evidence = evidence
            return None

    class ContextCertificate:
        def compact(self):
            return b"context-proof-v1"

        def reopen(self, blob, question, evidence):
            return False

        def reopen_contextual(self, blob, question, evidence, context_intents):
            return (blob == b"context-proof-v1" and question == "Summarize the observed turn"
                    and tuple(item.intent_id for item in context_intents) == ("turn:0",)
                    and tuple(item.fact_ids for item in context_intents) == ((1,),)
                    and len(evidence) == 1 and evidence[0].fact_id == 1
                    and evidence[0].speaker == "Alice" and evidence[0].sequence == 7
                    and evidence[0].event_time == 739000 and evidence[0].scope_id == SCOPE
                    and evidence[0].version == 1 and evidence[0].source_span == (0, 52)
                    and evidence[0].parent_sha256 is not None
                    and context_intents[0].turn_index == 0
                    and context_intents[0].session_id == "session:7")

        def reopen_contextual_resolution(
                self, blob, question, evidence, context_intents, *, text, method, source_ids):
            return (self.reopen_contextual(
                blob, question, evidence, context_intents)
                    and text == "The observed turn is certified."
                    and method == "test_contextual"
                    and source_ids == (evidence[0].source_id,))

    class ContextOnlyResolver:
        def __init__(self):
            self.context_intents = ()

        def resolve_contextual(self, question, evidence, context_intents):
            self.context_intents = context_intents
            return DirectAnswerResolution(
                "The observed turn is certified.", "test_contextual",
                (evidence[0].source_id,),
                DirectAnswerReaderBoundaryTests.ContextCertificate())

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

    def test_certified_derived_answer_can_resolve_without_text_containment(self):
        documents = (_doc(1, "The two independently witnessed operands are 40 plus 2."),)
        result = HorizonAnswerEngine(
            scope_id=SCOPE, direct_answer_resolver=self.ResolveSum()).answer(
                "What is the sum of 40 plus 2?", documents)
        self.assertEqual(result.direct_answer.state, "resolved")
        self.assertEqual(result.direct_answer.text, "42")
        self.assertTrue(result.direct_answer.proof_closed)
        self.assertEqual(result.direct_answer.certificate, b"test-sum:40+2=42")
        self.assertNotIn("42", result.evidence_text)

    def test_unreopenable_derived_certificate_fails_closed(self):
        documents = (_doc(1, "The two independently witnessed operands are 40 plus 2."),)
        result = HorizonAnswerEngine(
            scope_id=SCOPE, direct_answer_resolver=self.RejectBadSum()).answer(
                "What is the sum of 40 plus 2?", documents)
        self.assertEqual(result.direct_answer.state, "abstain")
        self.assertEqual(result.direct_answer.residual, ("certificate_does_not_reopen",))
        self.assertIn("40 plus 2", result.evidence_text)

    def test_legacy_certificate_cannot_authorize_a_resolution(self):
        result = HorizonAnswerEngine(
            scope_id=SCOPE,
            direct_answer_resolver=self.ResolveWithLegacyCertificate()).answer(
                "What is the sum of 40 plus 2?", (
                    _doc(1, "The two independently witnessed operands are 40 plus 2."),))
        self.assertEqual(result.direct_answer.state, "abstain")
        self.assertEqual(
            result.direct_answer.residual, ("certificate_does_not_bind_resolution",))

    def test_certificate_rejects_changed_answer_or_method(self):
        for resolver in (
                self.MutateResolution(text="43"),
                self.MutateResolution(method="changed_method")):
            with self.subTest(resolver=resolver):
                result = HorizonAnswerEngine(
                    scope_id=SCOPE, direct_answer_resolver=resolver).answer(
                        "What is the sum of 40 plus 2?", (
                            _doc(1, "The two independently witnessed operands are 40 plus 2."),))
                self.assertEqual(result.direct_answer.state, "abstain")
                self.assertEqual(
                    result.direct_answer.residual, ("certificate_does_not_reopen",))

    def test_resolver_sees_verified_acquisition_pool_before_render_budget(self):
        probe = self.CaptureResolverPool()
        documents = tuple(_doc(index, (
            f"Meridian observation {index} records a separately verified percentage fact "
            "with enough explanatory material to exceed the deliberately tiny answer budget."
        )) for index in range(1, 5))
        result = HorizonAnswerEngine(
            scope_id=SCOPE,
            profile=EngineProfile(acquisition_bytes=4096, answer_bytes=256),
            direct_answer_resolver=probe).answer(
                "What percentage facts did Meridian record?", documents)
        self.assertGreater(len(result.sources), len(result.claims))
        self.assertEqual(len(probe.evidence), len(result.sources))
        self.assertEqual({item.source_id for item in probe.evidence},
                         {item.source_id for item in result.sources})

    def test_explicit_context_intent_does_not_hide_other_authorized_evidence(self):
        probe = self.CaptureResolverPool()
        documents = (
            _doc(1, "The authorized Meridian game had a verified 48-yard field goal."),
            _doc(2, "A different Meridian game had a verified 54-yard field goal."),
        )
        result = HorizonAnswerEngine(
            scope_id=SCOPE, direct_answer_resolver=probe).answer(
                "How long was the Meridian field goal?", documents,
                context_intents=(AnswerContextIntent("authorized-game", "Meridian field goal", (1,)),))
        self.assertTrue(probe.evidence)
        self.assertEqual({item.fact_id for item in probe.evidence}, {1, 2})
        self.assertTrue(any("48-yard" in item.text for item in probe.evidence))
        self.assertTrue(any("54-yard" in item.text for item in probe.evidence))

    def test_context_intent_abstains_if_any_declared_fact_fails_verification(self):
        probe = self.CaptureResolverPool()
        documents = (
            _doc(1, "The valid source records the value 42."),
            RouteDocument(
                2, "The foreign-scope source claims the value 99.", SCOPE + 1, "s1", 1,
                "foreign:2"),
        )
        result = HorizonAnswerEngine(
            scope_id=SCOPE, direct_answer_resolver=probe).answer(
                "What value was recorded?", documents,
                context_intents=(AnswerContextIntent(
                    "declared-authority", "What value was recorded?", (1, 2)),))
        self.assertEqual(result.state, "ABSTAIN_CONTEXT_AUTHORITY")
        self.assertEqual(probe.evidence, ())
        self.assertEqual(result.direct_answer.state, "not_attempted")

    def test_contextual_resolver_receives_intents_and_typed_document_coordinates(self):
        resolver = self.ContextOnlyResolver()
        documents = (RouteDocument(
            1, "Alice recorded a certified observation in this turn.", SCOPE, "session:7", 1,
            "chat:7", sequence=7, event_time=739000, role="user", speaker="Alice"),)
        intent = AnswerContextIntent(
            "turn:0", "What did Alice observe?", (1,),
            turn_index=0, session_id="session:7")
        result = HorizonAnswerEngine(
            scope_id=SCOPE, session_id="session:7", direct_answer_resolver=resolver).answer(
                "Summarize the observed turn", documents, context_intents=(intent,))
        self.assertEqual(result.direct_answer.state, "resolved")
        self.assertEqual(result.direct_answer.method, "test_contextual")
        self.assertEqual(resolver.context_intents, (intent,))

    def test_contextual_certificate_cannot_reopen_under_changed_intent(self):
        resolver = self.ContextOnlyResolver()
        documents = (RouteDocument(
            1, "Alice recorded a certified observation in this turn.", SCOPE, "session:7", 1,
            "chat:7", sequence=7, event_time=739000, role="user", speaker="Alice"),)
        result = HorizonAnswerEngine(
            scope_id=SCOPE, session_id="session:7", direct_answer_resolver=resolver).answer(
                "Summarize the observed turn", documents,
                context_intents=(AnswerContextIntent("turn:changed", "Different", (1,)),))
        self.assertEqual(result.direct_answer.state, "abstain")
        self.assertEqual(result.direct_answer.residual, ("certificate_does_not_reopen",))


if __name__ == "__main__":
    unittest.main()
