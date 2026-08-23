# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression coverage for the security-review fixes to api/_engine_bridge.py + server.py:
caller-controlled polish destination/secret (SSRF + credential exfiltration), unbounded STORE
growth, and unbounded request-body size. See the Maestri audit notes (2026-08-2x) for the
original findings this locks in."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


class PolishDestinationAndSecretTests(unittest.TestCase):
    """A caller must never be able to choose where `polish` sends the answer, or which
    environment variable gets read and forwarded as a credential."""

    def setUp(self):
        STORE.clear()
        RATE_LIMITER.reset()
        app.testing = True
        self.client = app.test_client()
        self.client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {CREDENTIALS['token']}"
        self.addCleanup(setattr, _engine_bridge, "POLISH_TRANSPORT_FACTORY", None)

    def test_build_polish_config_ignores_caller_destination_and_secret(self):
        config = _engine_bridge.build_polish_config({
            "polish": True, "polish_model": "qwen/qwen3.6-27b",
            "polish_base_url": "https://attacker.invalid/collect",
            "polish_api_key_env": "SOME_ENV_VAR_NAME"})
        self.assertEqual(config.base_url, _engine_bridge.POLISH_BASE_URL)
        self.assertNotEqual(config.base_url, "https://attacker.invalid/collect")
        self.assertEqual(config.api_key_env, _engine_bridge.POLISH_API_KEY_ENV)
        self.assertEqual(config.max_retries, 0)
        self.assertEqual(config.timeout_seconds, 10.0)

    def test_http_endpoint_ignores_caller_destination_and_never_leaks_the_named_secret(self):
        transport = FakeTransport([_polish_success_response()])
        _engine_bridge.POLISH_TRANSPORT_FACTORY = lambda: transport
        import os
        os.environ["HORIZON_TEST_SECRET"] = "sekrit-value-should-never-leave-process"
        self.addCleanup(os.environ.pop, "HORIZON_TEST_SECRET", None)

        response = self.client.post("/v1/answers", json={
            "question": QUESTION, "documents": DOCUMENTS,
            "polish": True, "polish_model": "qwen/qwen3.6-27b",
            "polish_base_url": "https://attacker.invalid/collect",
            "polish_api_key_env": "HORIZON_TEST_SECRET"})

        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(transport.calls), 1)
        url, headers, _body, _timeout = transport.calls[0]
        self.assertEqual(url, _engine_bridge.POLISH_BASE_URL)
        self.assertNotIn("attacker.invalid", url)
        self.assertNotIn("sekrit-value-should-never-leave-process", json.dumps(headers))


class StoreBoundsTests(unittest.TestCase):
    """The in-memory answer STORE must stay bounded (LRU) and time-limited (TTL), not grow
    forever off unauthenticated POSTs."""

    def setUp(self):
        STORE.clear()
        RATE_LIMITER.reset()
        app.testing = True
        self.client = app.test_client()
        self.client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {CREDENTIALS['token']}"
        self.addCleanup(setattr, _engine_bridge, "STORE_MAX_ENTRIES", _engine_bridge.STORE_MAX_ENTRIES)
        self.addCleanup(setattr, _engine_bridge, "STORE_TTL_SECONDS", _engine_bridge.STORE_TTL_SECONDS)

    def _post(self):
        return self.client.post(
            "/v1/answers", json={"question": QUESTION, "documents": DOCUMENTS}).get_json()["id"]

    def test_store_evicts_oldest_entry_beyond_max_entries(self):
        _engine_bridge.STORE_MAX_ENTRIES = 2
        ids = [self._post() for _ in range(3)]
        self.assertEqual(self.client.get(f"/v1/answers/{ids[0]}").status_code, 404)
        self.assertEqual(self.client.get(f"/v1/answers/{ids[-1]}").status_code, 200)

    def test_store_prunes_entries_past_ttl(self):
        _engine_bridge.STORE_TTL_SECONDS = 1
        answer_id = self._post()
        with _engine_bridge._STORE_LOCK:
            result, _created, polished, state = _engine_bridge.STORE[answer_id]
            _engine_bridge.STORE[answer_id] = (result, int(time.time()) - 2, polished, state)
        self.assertEqual(self.client.get(f"/v1/answers/{answer_id}").status_code, 404)


class RequestSizeLimitTests(unittest.TestCase):
    """Neither the whole request body nor an individual field should be unbounded."""

    def setUp(self):
        STORE.clear()
        RATE_LIMITER.reset()
        app.testing = True
        self.client = app.test_client()
        self.client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {CREDENTIALS['token']}"

    def test_oversized_body_returns_413(self):
        huge_document = "x" * (2 * 1024 * 1024)  # 2 MiB, over the 1 MiB body cap
        response = self.client.post(
            "/v1/answers", json={"question": QUESTION, "documents": [huge_document]})
        self.assertEqual(response.status_code, 413)

    def test_oversized_single_document_returns_400(self):
        huge_document = "x" * (_engine_bridge.MAX_DOCUMENT_BYTES + 1)
        response = self.client.post(
            "/v1/answers", json={"question": QUESTION, "documents": [huge_document]})
        self.assertEqual(response.status_code, 400)

    def test_oversized_question_returns_400(self):
        huge_question = "x" * (_engine_bridge.MAX_QUESTION_BYTES + 1)
        response = self.client.post(
            "/v1/answers", json={"question": huge_question, "documents": DOCUMENTS})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
