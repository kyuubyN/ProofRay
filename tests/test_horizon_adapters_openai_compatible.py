# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""OpenAICompatiblePolishAdapter: generic rewrite-only step over any chat/completions endpoint."""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from horizon_memory.adapters import OpenAICompatiblePolishAdapter, PolishConfig
from horizon_memory.adapters.openai_compatible import TransportResponse

QUESTION = "What framework does BARM adopt for its estimation task?"


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, url, headers, body, timeout):
        self.calls.append((url, headers, json.loads(body) if body is not None else None, timeout))
        return self.responses.pop(0)


def _success_response(text="Rewritten fluent answer.", input_tokens=42, output_tokens=9):
    return TransportResponse(200, {}, json.dumps({
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens},
    }).encode())


class ConfigValidationTests(unittest.TestCase):
    def test_rejects_empty_model(self):
        with self.assertRaises(ValueError):
            PolishConfig(model="")

    def test_rejects_out_of_range_temperature(self):
        with self.assertRaises(ValueError):
            PolishConfig(model="m", temperature=3.0)

    def test_rejects_non_positive_max_tokens(self):
        with self.assertRaises(ValueError):
            PolishConfig(model="m", max_output_tokens=0)


class QuestionRequiredTests(unittest.TestCase):
    """The 2026-08-19 fix: `polish()` was shipped without a `question` parameter at all -- the
    model was told "the facts below answer the user's question" but never actually shown it."""

    def test_empty_question_raises_without_a_network_call(self):
        transport = FakeTransport([_success_response()])
        adapter = OpenAICompatiblePolishAdapter(transport=transport, allow_network=True)
        with self.assertRaises(ValueError):
            adapter.polish("", "fact one.", PolishConfig(model="qwen/qwen3.6-27b"))
        self.assertEqual(transport.calls, [])

    def test_question_is_present_in_the_request_payload(self):
        transport = FakeTransport([_success_response()])
        adapter = OpenAICompatiblePolishAdapter(transport=transport, allow_network=True)
        adapter.polish(QUESTION, "fact one.", PolishConfig(model="m"))
        _, _, body, _ = transport.calls[0]
        self.assertIn(QUESTION, body["messages"][1]["content"])


class DryRunSafetyTests(unittest.TestCase):
    def test_dry_run_by_default_makes_no_transport_call(self):
        transport = FakeTransport([_success_response()])
        adapter = OpenAICompatiblePolishAdapter(transport=transport)  # allow_network=False default
        result = adapter.polish(QUESTION, "BARM adopts a Bayesian framework.",
                                PolishConfig(model="qwen/qwen3.6-27b"))
        self.assertEqual(result.state, "dry_run")
        self.assertEqual(transport.calls, [])

    def test_empty_answer_text_is_returned_unchanged_without_a_network_call(self):
        transport = FakeTransport([_success_response()])
        adapter = OpenAICompatiblePolishAdapter(transport=transport, allow_network=True)
        result = adapter.polish(QUESTION, "   ", PolishConfig(model="qwen/qwen3.6-27b"))
        self.assertEqual(result.state, "polished")
        self.assertEqual(result.text, "   ")
        self.assertEqual(transport.calls, [])


class SecretHandlingTests(unittest.TestCase):
    def test_api_key_env_none_sends_no_authorization_header(self):
        transport = FakeTransport([_success_response()])
        adapter = OpenAICompatiblePolishAdapter(transport=transport, allow_network=True)
        adapter.polish(QUESTION, "fact one.", PolishConfig(model="local-model", api_key_env=None))
        _, headers, _, _ = transport.calls[0]
        self.assertNotIn("Authorization", headers)

    @patch.dict("os.environ", {"FAKE_POLISH_KEY": "sk-real-secret-value"})
    def test_api_key_env_reads_env_var_and_never_logs_it_elsewhere(self):
        transport = FakeTransport([_success_response()])
        adapter = OpenAICompatiblePolishAdapter(transport=transport, allow_network=True)
        result = adapter.polish(QUESTION, "fact one.",
                                PolishConfig(model="m", api_key_env="FAKE_POLISH_KEY"))
        url, headers, _, _ = transport.calls[0]
        self.assertEqual(headers["Authorization"], "Bearer sk-real-secret-value")
        self.assertNotIn("sk-real-secret-value", url)
        self.assertNotIn("sk-real-secret-value", repr(result))
        self.assertNotIn("sk-real-secret-value", str(result))

    def test_missing_env_var_is_a_clean_error_not_a_crash(self):
        transport = FakeTransport([])
        adapter = OpenAICompatiblePolishAdapter(transport=transport, allow_network=True)
        result = adapter.polish(QUESTION, "fact one.",
                                PolishConfig(model="m", api_key_env="DOES_NOT_EXIST_9182"))
        self.assertEqual(result.state, "error")
        self.assertTrue(result.error_code.startswith("missing_env_var"))
        self.assertEqual(transport.calls, [])


class RequestShapeTests(unittest.TestCase):
    def test_request_payload_has_expected_keys(self):
        transport = FakeTransport([_success_response()])
        adapter = OpenAICompatiblePolishAdapter(transport=transport, allow_network=True)
        adapter.polish(QUESTION, "fact one.",
                       PolishConfig(model="qwen/qwen3.6-27b", temperature=0.2))
        _, _, body, _ = transport.calls[0]
        self.assertEqual(body["model"], "qwen/qwen3.6-27b")
        self.assertEqual(body["temperature"], 0.2)
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertEqual(len(body["messages"]), 2)
        self.assertEqual(body["messages"][0]["role"], "system")
        self.assertIn("fact one.", body["messages"][1]["content"])
        self.assertIn(QUESTION, body["messages"][1]["content"])

    def test_extra_body_is_merged_into_payload(self):
        transport = FakeTransport([_success_response()])
        adapter = OpenAICompatiblePolishAdapter(transport=transport, allow_network=True)
        adapter.polish(QUESTION, "fact one.", PolishConfig(
            model="m", extra_body={"chat_template_kwargs": {"enable_thinking": False}}))
        _, _, body, _ = transport.calls[0]
        self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})

    def test_reasoning_effort_none_is_omitted_from_payload(self):
        transport = FakeTransport([_success_response()])
        adapter = OpenAICompatiblePolishAdapter(transport=transport, allow_network=True)
        adapter.polish(QUESTION, "fact one.", PolishConfig(model="m", reasoning_effort=None))
        _, _, body, _ = transport.calls[0]
        self.assertNotIn("reasoning_effort", body)


class ResponseHandlingTests(unittest.TestCase):
    def test_successful_response_strips_think_blocks(self):
        transport = FakeTransport([_success_response("<think>internal reasoning</think>Final answer.")])
        adapter = OpenAICompatiblePolishAdapter(transport=transport, allow_network=True)
        result = adapter.polish(QUESTION, "fact one.", PolishConfig(model="m"))
        self.assertEqual(result.state, "polished")
        self.assertEqual(result.text, "Final answer.")
        self.assertNotIn("internal reasoning", result.text)

    def test_successful_response_strips_unterminated_think_prefix(self):
        transport = FakeTransport([TransportResponse(200, {}, json.dumps({
            "choices": [{"message": {"content": "reasoning trace</think>Final answer."}}],
        }).encode())])
        adapter = OpenAICompatiblePolishAdapter(transport=transport, allow_network=True)
        result = adapter.polish(QUESTION, "fact one.", PolishConfig(model="m"))
        self.assertEqual(result.text, "Final answer.")

    def test_non_200_response_is_a_clean_error(self):
        transport = FakeTransport([TransportResponse(404, {}, b"not found")])
        adapter = OpenAICompatiblePolishAdapter(transport=transport, allow_network=True)
        result = adapter.polish(QUESTION, "fact one.", PolishConfig(model="m"))
        self.assertEqual(result.state, "error")
        self.assertEqual(result.error_code, "http_404")
        self.assertEqual(result.text, "")
        self.assertEqual(len(transport.calls), 1, "a non-transient status must not be retried")

    def test_malformed_response_body_is_a_clean_error(self):
        transport = FakeTransport([TransportResponse(200, {}, b"not json")])
        adapter = OpenAICompatiblePolishAdapter(transport=transport, allow_network=True)
        result = adapter.polish(QUESTION, "fact one.", PolishConfig(model="m"))
        self.assertEqual(result.state, "error")
        self.assertEqual(result.error_code, "invalid_response_schema")

    def test_transport_exception_is_a_clean_error_after_retries_exhausted(self):
        class ExplodingTransport:
            def __init__(self):
                self.calls = 0

            def request(self, *args, **kwargs):
                self.calls += 1
                raise ConnectionError("network unreachable")

        transport = ExplodingTransport()
        adapter = OpenAICompatiblePolishAdapter(transport=transport, allow_network=True)
        result = adapter.polish(QUESTION, "fact one.", PolishConfig(
            model="m", max_retries=2, retry_base_seconds=0.001))
        self.assertEqual(result.state, "error")
        self.assertEqual(result.error_code, "ConnectionError")
        self.assertEqual(transport.calls, 3, "1 initial attempt + 2 retries")


class RetryBehaviorTests(unittest.TestCase):
    """2026-08-19: both Groq and Gemini's free tiers rate-limit real batch runs (confirmed this
    session -- Qwen/Groq lost 61/120 episodes to HTTP 429 in one run). Retries transient failures
    with backoff, matching lab/judge_client.py's already-validated shape for the same providers."""

    def test_429_is_retried_and_succeeds_on_a_later_attempt(self):
        transport = FakeTransport([
            TransportResponse(429, {}, b"rate limited"),
            TransportResponse(429, {}, b"rate limited"),
            _success_response("Recovered after retry."),
        ])
        adapter = OpenAICompatiblePolishAdapter(transport=transport, allow_network=True)
        result = adapter.polish(QUESTION, "fact one.", PolishConfig(
            model="m", max_retries=4, retry_base_seconds=0.001))
        self.assertEqual(result.state, "polished")
        self.assertEqual(result.text, "Recovered after retry.")
        self.assertEqual(len(transport.calls), 3)

    def test_exhausting_retries_on_persistent_429_is_a_clean_error(self):
        transport = FakeTransport([TransportResponse(429, {}, b"rate limited")] * 3)
        adapter = OpenAICompatiblePolishAdapter(transport=transport, allow_network=True)
        result = adapter.polish(QUESTION, "fact one.", PolishConfig(
            model="m", max_retries=2, retry_base_seconds=0.001))
        self.assertEqual(result.state, "error")
        self.assertEqual(result.error_code, "http_429")
        self.assertEqual(len(transport.calls), 3, "1 initial attempt + 2 retries")

    def test_max_retries_zero_disables_retrying(self):
        transport = FakeTransport([TransportResponse(429, {}, b"rate limited")])
        adapter = OpenAICompatiblePolishAdapter(transport=transport, allow_network=True)
        result = adapter.polish(QUESTION, "fact one.",
                                PolishConfig(model="m", max_retries=0))
        self.assertEqual(result.state, "error")
        self.assertEqual(len(transport.calls), 1)

    def test_config_rejects_negative_max_retries(self):
        with self.assertRaises(ValueError):
            PolishConfig(model="m", max_retries=-1)

    def test_config_rejects_non_positive_retry_base_seconds(self):
        with self.assertRaises(ValueError):
            PolishConfig(model="m", retry_base_seconds=0)


if __name__ == "__main__":
    unittest.main()
