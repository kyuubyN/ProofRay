# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""HorizonAPI: POST-then-GET round trip, include_sources toggling, 404, health."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# A fixed, shared temp path so every api/tests/ file agrees on the same throwaway credentials
# file regardless of import order, instead of writing to the real per-user config directory
# `machine_auth.credentials_path()` would otherwise resolve to.
os.environ.setdefault(
    "HORIZON_API_CREDENTIALS_PATH",
    str(Path(tempfile.gettempdir()) / "horizon-memory-test-credentials.json"))

import _engine_bridge  # noqa: E402
from horizon_memory import (  # noqa: E402
    CONVERSATIONAL_HIGH_RECALL_PROFILE, DEFAULT_PROFILE, RouteDocument,
)
from horizon_memory.adapters.openai_compatible import TransportResponse  # noqa: E402
from rate_limit import RATE_LIMITER  # noqa: E402
from server import CREDENTIALS, STORE, app  # noqa: E402

DOCUMENTS = [
    "The Meridian project reduced compute cost by exactly 42 percent compared to the "
    "previous baseline architecture across every workload.",
    "Standard atmospheric pressure at sea level is approximately one hundred and one "
    "thousand three hundred and twenty five pascals.",
    "Meridian's cost reduction came from a redesigned caching layer that eliminated "
    "redundant recomputation across adjacent pipeline stages.",
]
QUESTION = "What percent did the Meridian project reduce cost by?"

STRUCTURED_DOCUMENTS = [
    {
        "fact_id": 41,
        "text": "My camping trip to Big Sur lasted 3 days.",
        "source": "chat:41",
        "scope": 1,
        "session": "history-2024-05",
        "version": 2,
        "sequence": 7,
        "event_time": 739019,
        "role": "user",
        "speaker": "Ana",
        "span": [100, 143],
        "text_sha256": hashlib.sha256(
            "My camping trip to Big Sur lasted 3 days.".encode()).hexdigest(),
    },
    {
        "fact_id": 99,
        "text": "The camping trip in Yosemite lasted 5 days.",
        "source": "chat:99",
        "scope": 1,
        "session": "history-2024-06",
        "version": 1,
        "sequence": 11,
        "event_time": 739050,
        "role": "user",
        "speaker": "Ana",
        "span": [200, 246],
        "text_sha256": hashlib.sha256(
            "The camping trip in Yosemite lasted 5 days.".encode()).hexdigest(),
    },
]
STRUCTURED_QUESTION = "How many days did I spend camping in total?"


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, url, headers, body, timeout):
        self.calls.append((url, headers, json.loads(body) if body is not None else None, timeout))
        return self.responses.pop(0)


def _polish_success_response(text="Meridian's compute cost dropped by 42 percent."):
    return TransportResponse(200, {}, json.dumps({
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 30, "completion_tokens": 12},
    }).encode())


class HorizonAPITests(unittest.TestCase):
    def setUp(self):
        STORE.clear()
        RATE_LIMITER.reset()
        app.testing = True
        self.client = app.test_client()
        self.client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {CREDENTIALS['token']}"
        self.addCleanup(setattr, _engine_bridge, "POLISH_TRANSPORT_FACTORY", None)

    def test_health(self):
        response = self.client.get("/v1/health")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["status"], "ok")
        self.assertIn("schema", body)
        self.assertIsInstance(body["conversational_recall"], bool)

    def test_conversational_activation_selects_the_measured_64_cut_profile(self):
        self.assertIs(_engine_bridge.conversational_engine_profile(False), DEFAULT_PROFILE)
        self.assertIs(
            _engine_bridge.conversational_engine_profile(True),
            CONVERSATIONAL_HIGH_RECALL_PROFILE)
        self.assertEqual(
            _engine_bridge.conversational_engine_profile(True).claim_limit, 64)
        with self.assertRaises(TypeError):
            _engine_bridge.conversational_engine_profile(1)

    def test_create_answer_resolves_without_sources_by_default(self):
        response = self.client.post("/v1/answers", json={
            "question": QUESTION, "documents": DOCUMENTS})
        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertEqual(body["object"], "answer")
        self.assertTrue(body["id"].startswith("ans_"))
        self.assertEqual(body["state"], "resolved")
        self.assertEqual(body["action"], "answer")
        self.assertIn("42", body["answer"])
        self.assertEqual(body["evidence"], body["answer"])
        self.assertIsNone(body["direct_answer"])
        self.assertEqual(body["direct_answer_state"], "not_attempted")
        self.assertFalse(body["direct_answer_proof_closed"])
        self.assertIsNone(body["direct_answer_certificate"])
        self.assertIsNone(body["direct_answer_certificate_encoding"])
        self.assertIsNone(body["sources"])
        self.assertGreater(len(body["answer_lines"]), 0)
        self.assertGreater(body["documents_considered"], 0)

    def test_create_answer_with_include_sources_populates_full_claim_list(self):
        response = self.client.post("/v1/answers", json={
            "question": QUESTION, "documents": DOCUMENTS, "include_sources": True})
        body = response.get_json()
        self.assertIsNotNone(body["sources"])
        self.assertGreater(len(body["sources"]), 0)
        joined = " ".join(s["text"] for s in body["sources"])
        self.assertIn("Meridian", joined)

    def test_get_answer_round_trip_defaults_to_compressed(self):
        created = self.client.post("/v1/answers", json={
            "question": QUESTION, "documents": DOCUMENTS, "include_sources": True}).get_json()
        fetched = self.client.get(f"/v1/answers/{created['id']}")
        self.assertEqual(fetched.status_code, 200)
        body = fetched.get_json()
        self.assertEqual(body["id"], created["id"])
        self.assertEqual(body["answer"], created["answer"])
        self.assertIsNone(body["sources"], "bare GET must stay compressed regardless of "
                                           "what the original POST requested")

    def test_get_answer_with_include_sources_query_param(self):
        created = self.client.post("/v1/answers", json={
            "question": QUESTION, "documents": DOCUMENTS}).get_json()
        fetched = self.client.get(f"/v1/answers/{created['id']}?include_sources=true")
        body = fetched.get_json()
        self.assertIsNotNone(body["sources"])
        self.assertGreater(len(body["sources"]), 0)

    def test_get_unknown_id_returns_404(self):
        response = self.client.get("/v1/answers/ans_doesnotexist")
        self.assertEqual(response.status_code, 404)
        self.assertIn("error", response.get_json())

    def test_missing_question_is_a_400(self):
        response = self.client.post("/v1/answers", json={"documents": DOCUMENTS})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.get_json())

    def test_missing_documents_is_a_400(self):
        response = self.client.post("/v1/answers", json={"question": QUESTION})
        self.assertEqual(response.status_code, 400)

    def test_empty_documents_is_a_400(self):
        response = self.client.post("/v1/answers", json={
            "question": QUESTION, "documents": []})
        self.assertEqual(response.status_code, 400)

    def test_non_string_document_entry_is_a_400(self):
        response = self.client.post("/v1/answers", json={
            "question": QUESTION, "documents": [123, "text"]})
        self.assertEqual(response.status_code, 400)

    def test_structured_documents_preserve_every_route_field_out_of_band(self):
        built = _engine_bridge.build_documents(STRUCTURED_DOCUMENTS)
        expected = (
            RouteDocument(41, STRUCTURED_DOCUMENTS[0]["text"], 1, "history-2024-05", 2,
                          "chat:41", sequence=7, span=(100, 143), event_time=739019,
                          role="user", speaker="Ana"),
            RouteDocument(99, STRUCTURED_DOCUMENTS[1]["text"], 1, "history-2024-06", 1,
                          "chat:99", sequence=11, span=(200, 246), event_time=739050,
                          role="user", speaker="Ana"),
        )
        self.assertEqual(built, expected)
        self.assertEqual(built[0].text, STRUCTURED_DOCUMENTS[0]["text"])
        self.assertNotIn("history-2024-05", built[0].text)
        self.assertNotIn("Ana", built[0].text)

    def test_structured_http_matches_direct_python_engine_and_serializes_certificate(self):
        documents = _engine_bridge.build_documents(STRUCTURED_DOCUMENTS)
        direct = _engine_bridge.ENGINE.answer(STRUCTURED_QUESTION, documents)
        response = self.client.post("/v1/answers", json={
            "question": STRUCTURED_QUESTION,
            "documents": STRUCTURED_DOCUMENTS,
            "include_sources": True,
        })
        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertEqual(body["state"], direct.state.lower())
        self.assertEqual(body["action"], "answer")
        self.assertEqual(body["answer"], direct.final_answer_text)
        self.assertEqual(body["evidence"], direct.evidence_text)
        self.assertEqual(body["direct_answer"], direct.direct_answer.text)
        self.assertEqual(body["direct_answer_state"], direct.direct_answer.state)
        self.assertTrue(body["direct_answer_proof_closed"])
        self.assertEqual(body["direct_answer_certificate"],
                         direct.direct_answer.certificate.hex())
        self.assertEqual(body["direct_answer_certificate_encoding"], "hex")
        self.assertEqual(body["answer"], "8 days")
        self.assertEqual(
            {item["fact_id"] for item in body["direct_answer_evidence"]}, {41, 99})
        self.assertEqual(
            {tuple(item["source_span"]) for item in body["direct_answer_evidence"]},
            {(100, 143), (200, 246)})
        self.assertTrue(all(item["parent_sha256"] for item in body["direct_answer_evidence"]))

    def test_parallel_requests_cannot_close_each_others_cached_runtime(self):
        meridian = [{
            "fact_id": 301, "text": "Meridian recorded exactly 42 percent reduction.",
            "source": "chat:301", "scope": 1, "session": "parallel:a", "version": 1,
        }]
        orion = [{
            "fact_id": 302, "text": "Orion recorded exactly 17 percent reduction.",
            "source": "chat:302", "scope": 1, "session": "parallel:b", "version": 1,
        }]

        def post(index):
            with app.test_client() as client:
                client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {CREDENTIALS['token']}"
                if index % 2:
                    response = client.post("/v1/answers", json={
                        "question": "What reduction did Meridian record?", "documents": meridian})
                    return response.status_code, "42" in response.get_json()["answer"]
                response = client.post("/v1/answers", json={
                    "question": "What reduction did Orion record?", "documents": orion})
                return response.status_code, "17" in response.get_json()["answer"]

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = tuple(pool.map(post, range(16)))
        self.assertTrue(all(status == 201 and correct for status, correct in results))

    def test_direct_answer_evidence_respects_include_sources_on_post_and_get(self):
        created = self.client.post("/v1/answers", json={
            "question": STRUCTURED_QUESTION,
            "documents": STRUCTURED_DOCUMENTS,
            "include_sources": False,
        }).get_json()
        self.assertEqual(created["direct_answer_state"], "resolved")
        self.assertEqual(created["direct_answer_evidence"], [])

        hidden = self.client.get(f"/v1/answers/{created['id']}").get_json()
        self.assertEqual(hidden["direct_answer_evidence"], [])
        exposed = self.client.get(
            f"/v1/answers/{created['id']}?include_sources=true").get_json()
        self.assertEqual(
            {item["fact_id"] for item in exposed["direct_answer_evidence"]}, {41, 99})

    def test_structured_context_intents_do_not_hide_complete_authority_pool(self):
        response = self.client.post("/v1/answers", json={
            "question": STRUCTURED_QUESTION,
            "documents": STRUCTURED_DOCUMENTS,
            "context_intents": [{
                "intent_id": "turn:0",
                "text": "How long was the Big Sur camping trip?",
                "fact_ids": [41],
                "turn_index": 0,
                "session_id": "history-2024-05",
            }],
        })
        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertEqual(body["direct_answer_state"], "resolved")
        self.assertEqual(body["answer"], "8 days")

    def test_context_intents_reject_unknown_facts_duplicates_and_scorer_fields(self):
        invalid = (
            [{"intent_id": "turn:0", "text": "Observed", "fact_ids": [404]}],
            [{"intent_id": "turn:0", "text": "Observed", "fact_ids": [41, 41]}],
            [{"intent_id": "turn:0", "text": "Observed", "fact_ids": [41],
              "is_supporting": True}],
            [{"intent_id": "turn:0", "text": "Observed", "fact_ids": [41],
              "session_id": "history-2024-06"}],
            [{"intent_id": "turn:0", "text": "Observed", "fact_ids": [41]},
             {"intent_id": "turn:0", "text": "Again", "fact_ids": [99]}],
        )
        for context_intents in invalid:
            with self.subTest(context_intents=context_intents):
                response = self.client.post("/v1/answers", json={
                    "question": STRUCTURED_QUESTION,
                    "documents": STRUCTURED_DOCUMENTS,
                    "context_intents": context_intents,
                })
                self.assertEqual(response.status_code, 400)

    def test_structured_version_is_bounded_before_storage(self):
        accepted = dict(STRUCTURED_DOCUMENTS[0], version=(1 << 32) - 1)
        self.assertEqual(_engine_bridge.build_documents([accepted])[0].version, (1 << 32) - 1)
        rejected = dict(accepted, version=1 << 32)
        response = self.client.post("/v1/answers", json={
            "question": STRUCTURED_QUESTION, "documents": [rejected]})
        self.assertEqual(response.status_code, 400)
        self.assertIn("storage domain", response.get_json()["error"]["message"])

    def test_legacy_document_projection_remains_exact(self):
        built = _engine_bridge.build_documents(["  Alpha remembers the blue bicycle.  "])
        self.assertEqual(built, (
            RouteDocument(1, "Alpha remembers the blue bicycle.", 1, "api", 1, "doc:1"),))

    def test_mixed_legacy_and_structured_documents_fail_closed(self):
        response = self.client.post("/v1/answers", json={
            "question": QUESTION,
            "documents": [DOCUMENTS[0], STRUCTURED_DOCUMENTS[0]],
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("only strings or only structured", response.get_json()["error"]["message"])

    def test_structured_document_metadata_fail_closed(self):
        invalid_rows = []
        missing = dict(STRUCTURED_DOCUMENTS[0])
        missing.pop("fact_id")
        invalid_rows.append(missing)
        invalid_rows.extend((
            {**STRUCTURED_DOCUMENTS[0], "fact_id": True},
            {**STRUCTURED_DOCUMENTS[0], "scope": 2},
            {**STRUCTURED_DOCUMENTS[0], "version": 0},
            {**STRUCTURED_DOCUMENTS[0], "sequence": -1},
            {**STRUCTURED_DOCUMENTS[0], "event_time": "yesterday"},
            {**STRUCTURED_DOCUMENTS[0], "role": "owner"},
            {**STRUCTURED_DOCUMENTS[0], "speaker": " "},
            {**STRUCTURED_DOCUMENTS[0], "source": "chat:41\nforged-citation"},
            {**STRUCTURED_DOCUMENTS[0], "fact_id": 1 << 62},
            {**STRUCTURED_DOCUMENTS[0], "span": [9, 9]},
            {**STRUCTURED_DOCUMENTS[0], "text_sha256": "0" * 64},
            {**STRUCTURED_DOCUMENTS[0], "gold_answer": "8 days"},
        ))
        for row in invalid_rows:
            with self.subTest(row=row):
                response = self.client.post("/v1/answers", json={
                    "question": STRUCTURED_QUESTION, "documents": [row]})
                self.assertEqual(response.status_code, 400)

        duplicate = [STRUCTURED_DOCUMENTS[0], {
            **STRUCTURED_DOCUMENTS[1], "fact_id": STRUCTURED_DOCUMENTS[0]["fact_id"]}]
        response = self.client.post("/v1/answers", json={
            "question": STRUCTURED_QUESTION, "documents": duplicate})
        self.assertEqual(response.status_code, 400)
        self.assertIn("unique fact_id", response.get_json()["error"]["message"])

    def test_polish_true_populates_polished_answer_without_changing_answer(self):
        transport = FakeTransport([_polish_success_response()])
        _engine_bridge.POLISH_TRANSPORT_FACTORY = lambda: transport
        response = self.client.post("/v1/answers", json={
            "question": QUESTION, "documents": DOCUMENTS,
            "polish": True, "polish_model": "qwen/qwen3.6-27b"})
        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertEqual(body["polish_state"], "polished")
        self.assertEqual(body["polished_answer"], "Meridian's compute cost dropped by 42 percent.")
        self.assertIn("42", body["answer"])
        self.assertNotEqual(body["answer"], body["polished_answer"])
        self.assertEqual(len(transport.calls), 1)

    def test_polish_true_without_polish_model_is_a_400(self):
        response = self.client.post("/v1/answers", json={
            "question": QUESTION, "documents": DOCUMENTS, "polish": True})
        self.assertEqual(response.status_code, 400)

    def test_polish_false_by_default_leaves_polish_fields_null(self):
        response = self.client.post("/v1/answers", json={
            "question": QUESTION, "documents": DOCUMENTS})
        body = response.get_json()
        self.assertIsNone(body["polished_answer"])
        self.assertIsNone(body["polish_state"])

    def test_polish_skips_transport_call_when_engine_abstains(self):
        transport = FakeTransport([_polish_success_response()])
        _engine_bridge.POLISH_TRANSPORT_FACTORY = lambda: transport
        response = self.client.post("/v1/answers", json={
            "question": "What is the answer?",
            "documents": ["Completely unrelated content about migratory bird patterns."],
            "polish": True, "polish_model": "qwen/qwen3.6-27b"})
        body = response.get_json()
        self.assertNotEqual(body["state"], "resolved")
        self.assertEqual(body["polish_state"], "skipped_abstained")
        self.assertIsNone(body["polished_answer"])
        self.assertEqual(transport.calls, [])

    def test_polish_transport_error_keeps_the_correct_answer(self):
        # 404, not 500/429 -- a non-transient status so this exercises the plain-error path
        # without triggering OpenAICompatiblePolishAdapter's own retry-with-backoff logic (real
        # sleeps at the default retry_base_seconds, exactly the mechanism validated separately in
        # tests/test_horizon_adapters_openai_compatible.py's own RetryBehaviorTests).
        transport = FakeTransport([TransportResponse(404, {}, b"not found")])
        _engine_bridge.POLISH_TRANSPORT_FACTORY = lambda: transport
        response = self.client.post("/v1/answers", json={
            "question": QUESTION, "documents": DOCUMENTS,
            "polish": True, "polish_model": "qwen/qwen3.6-27b"})
        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertEqual(body["polish_state"], "error")
        self.assertIsNone(body["polished_answer"])
        self.assertIn("42", body["answer"])


if __name__ == "__main__":
    unittest.main()
