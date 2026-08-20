# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared glue between the two HorizonAPI transports (`server.py`'s HTTP surface and
`mcp_server.py`'s MCP surface) so both call one implementation instead of two drifting copies.

This is transport-adjacent plumbing, not a model adapter -- it stays AGPL like `server.py` itself,
not the `Apache-2.0 OR AGPL-3.0-or-later` carve-out reserved for `src/horizon_memory/adapters/`
(see `LICENSE_POLICY.md`: that carve-out is scoped to model-reader integration boundaries).
"""
from __future__ import annotations

import secrets
import sys
import time
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from horizon_memory import (  # noqa: E402
    AnsweredResult, DEFAULT_PROFILE, HorizonAnswerEngine, RouteDocument,
)
from horizon_memory.adapters import (  # noqa: E402
    OpenAICompatiblePolishAdapter, PolishConfig,
)
from horizon_memory.adapters.openai_compatible import Transport, RequestsTransport  # noqa: E402

SCOPE_ID = 1
SESSION_ID = "api"
ENGINE = HorizonAnswerEngine(profile=DEFAULT_PROFILE, scope_id=SCOPE_ID, session_id=SESSION_ID)
STORE: dict[str, tuple[AnsweredResult, int, str | None, str | None]] = {}
# id -> (result, created_unix_ts, polished_answer, polish_state) -- the polish fields are
# persisted alongside the result so a later GET doesn't silently lose the polish work a POST
# already paid for (2026-08-19, found via code review).

MAX_DOCUMENTS = 2000  # a defensive ceiling, not a tuned limit -- see api/README.md

# Overridable by tests, matching how tests already reach into STORE directly. None (the default)
# means "construct a real network-capable RequestsTransport" -- see build_polish_adapter().
POLISH_TRANSPORT_FACTORY: Callable[[], Transport] | None = None


def new_id() -> str:
    return f"ans_{secrets.token_hex(12)}"


def json_bool(value, default: bool = False) -> bool:
    """A JSON request body's boolean-shaped field, read defensively: `bool()` on a non-empty
    string is always True regardless of its content (`bool("false")` is True), so naively
    `bool()`-casting a caller's `"polish": "false"` or `"include_sources": "0"` -- a stringified
    boolean, plausible from a client that serializes form values -- silently does the opposite
    of what was asked (2026-08-19, found via code review). Missing/None uses `default`; a real
    JSON boolean passes through as-is; a string is matched case-insensitively against the same
    truthy set `_include_sources_from_query()` already uses for the GET query-string path."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes")
    return bool(value)


def build_documents(raw_documents: list) -> tuple[RouteDocument, ...]:
    documents = []
    for i, text in enumerate(raw_documents, start=1):
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"documents[{i - 1}] must be a non-empty string")
        documents.append(RouteDocument(i, text.strip(), SCOPE_ID, SESSION_ID, 1, f"doc:{i}"))
    return tuple(documents)


def serialize(answer_id: str, created: int, result: AnsweredResult, include_sources: bool,
              polished_answer: str | None = None, polish_state: str | None = None) -> dict:
    payload = {
        "id": answer_id,
        "object": "answer",
        "created": created,
        "state": result.state.lower(),
        "answer": result.answer_text,
        "answer_lines": [
            {"text": line.text, "source": line.source_id, "relevance_score": line.relevance_score}
            for line in result.answer_lines],
        "documents_considered": result.documents_considered,
        "verified_candidates": result.verified_candidates,
        "answer_bytes": result.answer_bytes,
        "sources": None,
        "polished_answer": polished_answer,
        "polish_state": polish_state,
    }
    if include_sources:
        payload["sources"] = [
            {"text": c.text, "source": c.source_id, "relevance_score": c.relevance_score}
            for c in result.claims]
    return payload


def build_polish_config(body: dict) -> PolishConfig | None:
    """Returns None when polish was not requested; raises ValueError (-> 400 in server.py /
    a clean tool error in mcp_server.py) when `polish: true` but `polish_model` is missing."""
    if not json_bool(body.get("polish")):
        return None
    model = body.get("polish_model")
    if not model:
        raise ValueError("`polish_model` is required when `polish` is true")
    kwargs = {"model": model}
    if body.get("polish_base_url"):
        kwargs["base_url"] = body["polish_base_url"]
    if body.get("polish_api_key_env"):
        kwargs["api_key_env"] = body["polish_api_key_env"]
    return PolishConfig(**kwargs)


def build_polish_adapter() -> OpenAICompatiblePolishAdapter:
    transport = POLISH_TRANSPORT_FACTORY() if POLISH_TRANSPORT_FACTORY else RequestsTransport()
    return OpenAICompatiblePolishAdapter(transport=transport, allow_network=True)


def run_polish(question: str, result: AnsweredResult, config: PolishConfig) -> tuple[str | None, str]:
    """Never called by callers when `result.state != "RESOLVED"` -- nothing to polish. Returns
    (polished_text_or_None, polish_state). A broken/erroring polish call never raises here -- the
    caller's own primary `answer` must never be taken down by an optional cosmetic step."""
    adapter = build_polish_adapter()
    polish_result = adapter.polish(question, result.answer_text, config)
    if polish_result.state == "polished":
        return polish_result.text, "polished"
    return None, "error"


def new_answer_id_and_timestamp() -> tuple[str, int]:
    return new_id(), int(time.time())
