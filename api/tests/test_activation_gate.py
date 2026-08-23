# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Coverage for the two activation modes: default "direct" (today's only behavior, unchanged)
and opt-in "keyword" (gates ENGINE.answer() behind a small, closed, server-configured
trigger-phrase list -- HORIZON_ACTIVATION_MODE / HORIZON_ACTIVATION_KEYWORDS). Both HTTP
(`server.py`) and MCP (`mcp_server.py`) share the same choke point (`_engine_bridge.maybe_answer`),
so both are exercised here to confirm neither transport drifts from the other."""
from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _engine_bridge  # noqa: E402
from mcp_server import mcp  # noqa: E402
from server import STORE, app  # noqa: E402

DOCUMENTS = [
    "The Meridian project reduced compute cost by exactly 42 percent compared to the "
    "previous baseline architecture across every workload.",
]
QUESTION_NO_TRIGGER = "What percent did the Meridian project reduce cost by?"
QUESTION_WITH_TRIGGER_EN = "Do you remember what percent the Meridian project reduced cost by?"
QUESTION_WITH_TRIGGER_PT = "Você lembra qual foi a redução de custo do projeto Meridian?"


class _KeywordModeTestCase(unittest.TestCase):
    def setUp(self):
        STORE.clear()
        app.testing = True
        self.client = app.test_client()
        self.addCleanup(setattr, _engine_bridge, "ACTIVATION_MODE", _engine_bridge.ACTIVATION_MODE)
        self.addCleanup(
            setattr, _engine_bridge, "ACTIVATION_KEYWORDS", _engine_bridge.ACTIVATION_KEYWORDS)

    def _post(self, question: str) -> dict:
        response = self.client.post("/v1/answers", json={
            "question": question, "documents": DOCUMENTS})
        return response.get_json()


class DefaultDirectModeTests(_KeywordModeTestCase):
    """Default mode must reproduce today's exact behavior regardless of question content."""

    def test_default_mode_is_direct(self):
        self.assertEqual(_engine_bridge.ACTIVATION_MODE, "direct")

    def test_direct_mode_runs_the_engine_even_with_no_trigger_word_present(self):
        body = self._post(QUESTION_NO_TRIGGER)
        self.assertEqual(body["state"], "resolved")
        self.assertIn("42", body["answer"])


class KeywordModeHttpTests(_KeywordModeTestCase):
    def setUp(self):
        super().setUp()
        _engine_bridge.ACTIVATION_MODE = "keyword"

    def test_no_trigger_word_returns_not_activated_without_running_the_engine(self):
        call_count = {"n": 0}
        original_answer = _engine_bridge.ENGINE.answer

        def _spy(*args, **kwargs):
            call_count["n"] += 1
            return original_answer(*args, **kwargs)

        _engine_bridge.ENGINE.answer = _spy
        self.addCleanup(setattr, _engine_bridge.ENGINE, "answer", original_answer)

        body = self._post(QUESTION_NO_TRIGGER)

        self.assertEqual(body["state"], "not_activated")
        self.assertIsNone(body["answer"])
        self.assertIsNone(body["evidence"])
        self.assertEqual(body["documents_considered"], 0)
        self.assertEqual(call_count["n"], 0)  # the engine must never have been invoked

    def test_trigger_word_en_runs_the_engine_normally(self):
        body = self._post(QUESTION_WITH_TRIGGER_EN)
        self.assertEqual(body["state"], "resolved")
        self.assertIn("42", body["answer"])

    def test_trigger_word_pt_runs_the_engine_normally(self):
        body = self._post(QUESTION_WITH_TRIGGER_PT)
        self.assertEqual(body["state"], "resolved")

    def test_trigger_match_is_case_insensitive(self):
        body = self._post(QUESTION_WITH_TRIGGER_EN.upper())
        self.assertEqual(body["state"], "resolved")

    def test_not_activated_result_replays_identically_via_get(self):
        post_body = self._post(QUESTION_NO_TRIGGER)
        get_response = self.client.get(f"/v1/answers/{post_body['id']}")
        get_body = get_response.get_json()
        self.assertEqual(get_body["state"], "not_activated")
        self.assertIsNone(get_body["answer"])


class KeywordModeMcpTests(_KeywordModeTestCase):
    """The MCP transport must share identical gating behavior with HTTP -- same choke point."""

    def setUp(self):
        super().setUp()
        _engine_bridge.ACTIVATION_MODE = "keyword"

    def _call(self, question: str) -> dict:
        async def run():
            return await mcp.call_tool(
                "horizon_ask", {"question": question, "documents": DOCUMENTS})
        result = asyncio.run(run())
        self.assertFalse(result.is_error)
        return json.loads(result.content[0].text)

    def test_no_trigger_word_returns_not_activated(self):
        body = self._call(QUESTION_NO_TRIGGER)
        self.assertEqual(body["state"], "not_activated")
        self.assertIsNone(body["answer"])

    def test_trigger_word_runs_normally(self):
        body = self._call(QUESTION_WITH_TRIGGER_EN)
        self.assertEqual(body["state"], "resolved")


class ActivationKeywordParsingTests(unittest.TestCase):
    """`HORIZON_ACTIVATION_KEYWORDS` parsing, tested as a pure function -- the module-level
    `ACTIVATION_KEYWORDS` itself is read once at import time (matching `POLISH_BASE_URL`'s own
    established pattern), so overriding the *environment variable* after import has no effect;
    this is the actual parsing logic a fresh process would apply."""

    def test_none_or_empty_falls_back_to_the_default_set(self):
        self.assertEqual(
            _engine_bridge._parse_activation_keywords(None), _engine_bridge.DEFAULT_ACTIVATION_KEYWORDS)
        self.assertEqual(
            _engine_bridge._parse_activation_keywords(""), _engine_bridge.DEFAULT_ACTIVATION_KEYWORDS)

    def test_comma_separated_override_is_lowercased_and_stripped(self):
        keywords = _engine_bridge._parse_activation_keywords(" Onde Foi , O Que Aconteceu ")
        self.assertEqual(keywords, frozenset({"onde foi", "o que aconteceu"}))

    def test_overriding_the_module_attribute_directly_changes_gate_behavior(self):
        # Matches this project's own established test convention (addCleanup(setattr, ...)) for
        # temporarily overriding deploy-time config within a single test process.
        original = _engine_bridge.ACTIVATION_KEYWORDS
        try:
            _engine_bridge.ACTIVATION_KEYWORDS = frozenset({"custom trigger"})
            self.assertTrue(_engine_bridge.keyword_gate_matches(
                "this has a CUSTOM TRIGGER in it", _engine_bridge.ACTIVATION_KEYWORDS))
            self.assertFalse(_engine_bridge.keyword_gate_matches(
                "do you remember anything", _engine_bridge.ACTIVATION_KEYWORDS))
        finally:
            _engine_bridge.ACTIVATION_KEYWORDS = original


if __name__ == "__main__":
    unittest.main()
