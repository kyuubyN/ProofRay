# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""HorizonAPI over MCP: the horizon_ask tool, exercised both as a plain function and through
the real MCP server's list_tools()/call_tool() (no subprocess/stdio round-trip needed -- the
mcp SDK's MCPServer exposes both as directly-awaitable coroutines)."""
from __future__ import annotations

import json
import sys
import unittest
import anyio
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import _engine_bridge  # noqa: E402
from horizon_memory.adapters.openai_compatible import TransportResponse  # noqa: E402
from mcp_server import _horizon_ask_impl, mcp  # noqa: E402

DOCUMENTS = [
    "The Meridian project reduced compute cost by exactly 42 percent compared to the "
    "previous baseline architecture across every workload.",
    "Meridian's cost reduction came from a redesigned caching layer that eliminated "
    "redundant recomputation across adjacent pipeline stages.",
]
QUESTION = "What percent did the Meridian project reduce cost by?"
STRUCTURED = [{
    "fact_id": 41, "text": "My camping trip to Big Sur lasted 3 days.", "source": "chat:41",
    "scope": 1, "session": "history", "version": 1, "role": "user", "speaker": "Ana",
}]


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, url, headers, body, timeout):
        self.calls.append((url, headers, json.loads(body) if body is not None else None, timeout))
        return self.responses.pop(0)


class HorizonAskImplTests(unittest.TestCase):
    """Coverage on the plain-Python implementation -- no MCP transport involved."""

    def tearDown(self):
        _engine_bridge.POLISH_TRANSPORT_FACTORY = None

    def test_resolves_and_matches_serialize_shape(self):
        body = _horizon_ask_impl(QUESTION, DOCUMENTS)
        self.assertEqual(body["object"], "answer")
        self.assertEqual(body["state"], "resolved")
        self.assertIn("42", body["answer"])
        self.assertIsNone(body["sources"])
        self.assertIsNone(body["polished_answer"])
        self.assertIsNone(body["polish_state"])

    def test_include_sources_populates_full_claim_list(self):
        body = _horizon_ask_impl(QUESTION, DOCUMENTS, include_sources=True)
        self.assertIsNotNone(body["sources"])
        self.assertGreater(len(body["sources"]), 0)

    def test_empty_question_raises(self):
        with self.assertRaises(ValueError):
            _horizon_ask_impl("", DOCUMENTS)

    def test_empty_documents_raises(self):
        with self.assertRaises(ValueError):
            _horizon_ask_impl(QUESTION, [])

    def test_context_intents_are_supported_by_plain_mcp_implementation(self):
        body = _horizon_ask_impl(
            "How many days did I spend camping in total?", STRUCTURED,
            context_intents=[{
                "intent_id": "turn:0", "text": "How long was the Big Sur camping trip?",
                "fact_ids": [41]}])
        self.assertEqual(body["direct_answer_state"], "resolved")
        self.assertEqual(body["answer"], "3 days")

    def test_polish_true_populates_polished_answer(self):
        transport = FakeTransport([TransportResponse(200, {}, json.dumps({
            "choices": [{"message": {"content": "Meridian cut costs by 42 percent."}}],
        }).encode())])
        _engine_bridge.POLISH_TRANSPORT_FACTORY = lambda: transport
        body = _horizon_ask_impl(QUESTION, DOCUMENTS, polish=True,
                                 polish_model="qwen/qwen3.6-27b")
        self.assertEqual(body["polish_state"], "polished")
        self.assertEqual(body["polished_answer"], "Meridian cut costs by 42 percent.")
        self.assertIn("42", body["answer"])


class MCPToolTests(unittest.TestCase):
    """Exercises the real @mcp.tool()-wrapped function through the SDK's own list_tools()/
    call_tool(), confirming the tool is actually registered and reachable as an MCP client
    would see it -- not just that the underlying Python function works."""

    def test_horizon_ask_is_registered(self):
        async def run():
            return await mcp.list_tools()
        tools = anyio.run(run)
        names = [t.name for t in tools]
        self.assertIn("horizon_ask", names)

    def test_call_tool_returns_the_serialized_answer(self):
        async def run():
            return await mcp.call_tool(
                "horizon_ask", {"question": QUESTION, "documents": DOCUMENTS})
        result = anyio.run(run)
        self.assertFalse(result.is_error)
        body = json.loads(result.content[0].text)
        self.assertEqual(body["state"], "resolved")
        self.assertIn("42", body["answer"])


if __name__ == "__main__":
    unittest.main()
