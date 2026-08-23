# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""HorizonAPI: POST-then-GET round trip, include_sources toggling, 404, health."""
from __future__ import annotations

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

    def test_create_answer_resolves_without_sources_by_default(self):
        response = self.client.post("/v1/answers", json={
            "question": QUESTION, "documents": DOCUMENTS})
        self.assertEqual(response.status_code, 201)
        body = response.get_json()
        self.assertEqual(body["object"], "answer")
        self.assertTrue(body["id"].startswith("ans_"))
        self.assertEqual(body["state"], "resolved")
        self.assertIn("42", body["answer"])
        self.assertEqual(body["evidence"], body["answer"])
        self.assertIsNone(body["direct_answer"])
        self.assertEqual(body["direct_answer_state"], "not_attempted")
        self.assertFalse(body["direct_answer_proof_closed"])
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
