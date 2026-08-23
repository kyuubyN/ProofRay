# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared glue between the two HorizonAPI transports (`server.py`'s HTTP surface and
`mcp_server.py`'s MCP surface) so both call one implementation instead of two drifting copies.

This is transport-adjacent plumbing, not a model adapter -- it stays AGPL like `server.py` itself,
not the `Apache-2.0 OR AGPL-3.0-or-later` carve-out reserved for `src/horizon_memory/adapters/`
(see `LICENSE_POLICY.md`: that carve-out is scoped to model-reader integration boundaries).
"""
from __future__ import annotations

import os
import secrets
import sys
import time
from collections import OrderedDict
from pathlib import Path
from threading import RLock
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

# id -> (result, created_unix_ts, polished_answer, polish_state) -- the polish fields are
# persisted alongside the result so a later GET doesn't silently lose the polish work a POST
# already paid for (2026-08-19, found via code review). Bounded by TTL + LRU eviction below --
# an unauthenticated POST loop used to grow this without limit until the process restarted
# (2026-08-2x, found via security review): every resolved answer retains its full claims/
# sources, so an attacker didn't even need to guess ids to exhaust memory, just POST repeatedly.
STORE_TTL_SECONDS = 3600
STORE_MAX_ENTRIES = 1000
STORE: OrderedDict[str, tuple[AnsweredResult, int, str | None, str | None]] = OrderedDict()
_STORE_LOCK = RLock()


def _prune_store(now: int) -> None:
    expired = [answer_id for answer_id, entry in STORE.items()
               if now - entry[1] >= STORE_TTL_SECONDS]
    for answer_id in expired:
        STORE.pop(answer_id, None)
    while len(STORE) > STORE_MAX_ENTRIES:
        STORE.popitem(last=False)


def store_answer(answer_id: str, entry: tuple[AnsweredResult, int, str | None, str | None]) -> None:
    with _STORE_LOCK:
        _prune_store(int(time.time()))
        STORE[answer_id] = entry
        STORE.move_to_end(answer_id)


def load_answer(answer_id: str):
    with _STORE_LOCK:
        _prune_store(int(time.time()))
        entry = STORE.get(answer_id)
        if entry is not None:
            STORE.move_to_end(answer_id)
        return entry


MAX_DOCUMENTS = 2000  # a defensive ceiling, not a tuned limit -- see api/README.md
MAX_QUESTION_BYTES = 16 * 1024
MAX_DOCUMENT_BYTES = 64 * 1024

# Deploy-time config for the optional `polish` step -- never caller input (see
# build_polish_config's docstring for why).
POLISH_BASE_URL = os.environ.get(
    "HORIZON_POLISH_BASE_URL", "https://api.groq.com/openai/v1/chat/completions")
POLISH_API_KEY_ENV = os.environ.get("HORIZON_POLISH_API_KEY_ENV")

# Deploy-time config for which "activation mode" gates ENGINE.answer() (see maybe_answer()):
# "direct" (default -- every request runs the pipeline, today's only behavior, unchanged) or
# "keyword" (only run the pipeline when the question matches one of ACTIVATION_KEYWORDS). Never a
# per-request caller field -- same reasoning as POLISH_BASE_URL/POLISH_API_KEY_ENV above: a
# setting that changes whether/how much server-side work a request triggers must come from this
# process's own environment, not an unauthenticated caller's request body.
ACTIVATION_MODE = os.environ.get("HORIZON_ACTIVATION_MODE", "direct").strip().lower()

# A small, closed, server-configurable trigger-phrase set -- not a growing dictionary. Overridable
# via HORIZON_ACTIVATION_KEYWORDS (comma-separated), server-side only, mirroring the same
# closed-list discipline already used for `_RETRACTION_MARKER`/`_ZH_CORRECTION_MARKER` elsewhere
# in this project: a fixed, small set of trigger phrases, not an attempt to enumerate every way a
# caller might ask Horizon to recall something.
DEFAULT_ACTIVATION_KEYWORDS = frozenset({
    "remember", "recall", "what did", "when did", "do you remember",
    "lembra", "lembrar", "lembra-se", "lembras", "você lembra", "se lembra",
})


def _parse_activation_keywords(raw: str | None) -> frozenset[str]:
    if not raw:
        return DEFAULT_ACTIVATION_KEYWORDS
    return frozenset(word.strip().lower() for word in raw.split(",") if word.strip())


ACTIVATION_KEYWORDS = _parse_activation_keywords(os.environ.get("HORIZON_ACTIVATION_KEYWORDS"))

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


def _utf8_size(value: str) -> int:
    return len(value.encode("utf-8"))


def validate_question_length(question: str) -> None:
    if _utf8_size(question) > MAX_QUESTION_BYTES:
        raise ValueError(f"`question` exceeds the {MAX_QUESTION_BYTES}-byte limit")


def build_documents(raw_documents: list) -> tuple[RouteDocument, ...]:
    documents = []
    for i, text in enumerate(raw_documents, start=1):
        if not isinstance(text, str) or not text.strip():
            raise ValueError(f"documents[{i - 1}] must be a non-empty string")
        if _utf8_size(text) > MAX_DOCUMENT_BYTES:
            raise ValueError(f"documents[{i - 1}] exceeds the {MAX_DOCUMENT_BYTES}-byte limit")
        documents.append(RouteDocument(i, text.strip(), SCOPE_ID, SESSION_ID, 1, f"doc:{i}"))
    return tuple(documents)


def keyword_gate_matches(text: str, keywords: frozenset[str]) -> bool:
    """Case-insensitive substring match against a small, closed trigger-phrase set -- supports
    multi-word phrases ("what did", "você lembra") without needing tokenization, since these are
    meant as simple trigger phrases, not a word-class grammar."""
    lowered = text.casefold()
    return any(keyword in lowered for keyword in keywords)


def maybe_answer(question: str, documents: tuple[RouteDocument, ...]) -> AnsweredResult | None:
    """The single choke point both transports (`server.py`, `mcp_server.py`) call instead of
    `ENGINE.answer()` directly.

    In the default "direct" activation mode, this is exactly `ENGINE.answer(question, documents)`
    -- zero behavior change from before this function existed. In "keyword" mode (see
    ACTIVATION_MODE above), returns `None` -- the engine never runs, zero pipeline cost -- when
    `question` matches none of ACTIVATION_KEYWORDS, instead of an `AnsweredResult`. A caller-facing
    "tool" activation mode needs no logic here at all: an orchestrating agent deciding for itself
    whether to invoke `horizon_ask` already IS the activation decision, made entirely outside this
    process, before this function is ever called -- this only implements the one mode ("keyword")
    that has no external decision-maker of its own.
    """
    if ACTIVATION_MODE == "keyword" and not keyword_gate_matches(question, ACTIVATION_KEYWORDS):
        return None
    return ENGINE.answer(question, documents)


def serialize(answer_id: str, created: int, result: AnsweredResult | None, include_sources: bool,
              polished_answer: str | None = None, polish_state: str | None = None) -> dict:
    if result is None:
        # The activation gate declined to run the engine at all (see maybe_answer) -- same
        # envelope shape as a real answer so callers never need a special-case parser, just a new
        # `state` value to branch on, exactly like they already branch on "resolved"/"abstention".
        return {
            "id": answer_id,
            "object": "answer",
            "created": created,
            "state": "not_activated",
            "answer": None,
            "evidence": None,
            "direct_answer": None,
            "direct_answer_state": None,
            "direct_answer_method": None,
            "direct_answer_sources": [],
            "direct_answer_proof_closed": None,
            "direct_answer_residual": [],
            "answer_lines": [],
            "documents_considered": 0,
            "verified_candidates": 0,
            "answer_bytes": 0,
            "sources": None,
            "polished_answer": None,
            "polish_state": None,
            "selector": None,
            "selector_proof_closed": None,
            "selector_residual": [],
        }
    payload = {
        "id": answer_id,
        "object": "answer",
        "created": created,
        "state": result.state.lower(),
        "answer": result.answer_text,
        "evidence": result.evidence_text,
        "direct_answer": result.direct_answer.text or None,
        "direct_answer_state": result.direct_answer.state,
        "direct_answer_method": result.direct_answer.method,
        "direct_answer_sources": list(result.direct_answer.source_ids),
        "direct_answer_proof_closed": result.direct_answer.proof_closed,
        "direct_answer_residual": list(result.direct_answer.residual),
        "answer_lines": [
            {"text": line.text, "source": line.source_id, "relevance_score": line.relevance_score}
            for line in result.answer_lines],
        "documents_considered": result.documents_considered,
        "verified_candidates": result.verified_candidates,
        "answer_bytes": result.answer_bytes,
        "sources": None,
        "polished_answer": polished_answer,
        "polish_state": polish_state,
        "selector": result.selector,
        "selector_proof_closed": result.selector_proof_closed,
        "selector_residual": list(result.selector_residual),
    }
    if include_sources:
        payload["sources"] = [
            {"text": c.text, "source": c.source_id, "relevance_score": c.relevance_score}
            for c in result.claims]
    return payload


def build_polish_config(body: dict) -> PolishConfig | None:
    """Returns None when polish was not requested; raises ValueError (-> 400 in server.py /
    a clean tool error in mcp_server.py) when `polish: true` but `polish_model` is missing.

    `base_url` and `api_key_env` come only from this process's own environment
    (HORIZON_POLISH_BASE_URL / HORIZON_POLISH_API_KEY_ENV), never from the request body. An
    earlier version let an unauthenticated caller set both directly: pointing `polish_base_url`
    at an attacker-controlled host while naming a real secret in `polish_api_key_env` made this
    process read that secret from its own environment and hand it to the attacker as a Bearer
    token; even without a key name, an arbitrary `base_url` is an open SSRF proxy into internal
    network/metadata endpoints (2026-08-2x, found via security review). `timeout_seconds`/
    `max_retries` are also tightened here rather than left at the adapter's own defaults: with no
    request auth on this API (see api/README.md's "Deferred" section), a caller could otherwise
    chain up to ~5 network attempts per polish call to pin a worker thread for minutes."""
    if not json_bool(body.get("polish")):
        return None
    model = body.get("polish_model")
    if not model:
        raise ValueError("`polish_model` is required when `polish` is true")
    return PolishConfig(
        model=model,
        base_url=POLISH_BASE_URL,
        api_key_env=POLISH_API_KEY_ENV,
        timeout_seconds=10.0,
        max_retries=0,
    )


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
