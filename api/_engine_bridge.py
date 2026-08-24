# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Shared glue between the two ProofRay transports (`server.py`'s HTTP surface and
`mcp_server.py`'s MCP surface) so both call one implementation instead of two drifting copies.

This is transport-adjacent plumbing, not a model adapter -- it stays AGPL like `server.py` itself,
not the `Apache-2.0 OR AGPL-3.0-or-later` carve-out reserved for `src/horizon_memory/adapters/`
(see `LICENSE_POLICY.md`: that carve-out is scoped to model-reader integration boundaries).
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sys
import time
from collections import OrderedDict
from pathlib import Path
from threading import RLock
from typing import Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from horizon_memory import (  # noqa: E402
    AnswerContextIntent, AnsweredResult, CONVERSATIONAL_HIGH_RECALL_PROFILE,
    ConversationalRecallGenerator, DEFAULT_PROFILE, HorizonAnswerEngine, RouteDocument,
)
from horizon_memory.adapters import (  # noqa: E402
    OpenAICompatiblePolishAdapter, PolishConfig,
)
from horizon_memory.adapters.openai_compatible import Transport, RequestsTransport  # noqa: E402

SCOPE_ID = 1
SESSION_ID = "api"


def _brand_env(name: str, default: str | None = None) -> str | None:
    """Read the canonical PROOFRAY variable, then its HORIZON compatibility alias."""
    return os.environ.get(f"PROOFRAY_{name}", os.environ.get(f"HORIZON_{name}", default))


CONVERSATIONAL_RECALL_ENABLED = (_brand_env(
    "CONVERSATIONAL_RECALL", "false") or "false").strip().casefold() in {
        "1", "true", "yes"}


def conversational_engine_profile(enabled: bool):
    """Bind the opt-in route to the exact measured 64-candidate consumer profile."""
    if not isinstance(enabled, bool):
        raise TypeError("conversational recall profile selector requires bool")
    return CONVERSATIONAL_HIGH_RECALL_PROFILE if enabled else DEFAULT_PROFILE


ENGINE = HorizonAnswerEngine(
    profile=conversational_engine_profile(CONVERSATIONAL_RECALL_ENABLED),
    scope_id=SCOPE_ID, session_id=SESSION_ID,
    candidate_generator=(ConversationalRecallGenerator()
                         if CONVERSATIONAL_RECALL_ENABLED else None),
    # Structured conversational documents normally belong to earlier sessions.  The fallback
    # remains inside the same server-owned scope; callers cannot use it to cross scope boundaries.
    allow_scope_fallback=True,
)

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
MAX_METADATA_BYTES = 4 * 1024
# The core registry is u64; the public JSON boundary deliberately stays inside a signed-safe
# transport domain so downstream stores/languages cannot silently reinterpret identities.
MAX_FACT_ID = 1 << 62
MAX_VERSION = (1 << 32) - 1  # on-disk group-commit record stores version as unsigned u32
MAX_CONTEXT_INTENTS = 256

_STRUCTURED_DOCUMENT_FIELDS = frozenset({
    "fact_id", "text", "source", "scope", "session", "version",
    "sequence", "event_time", "role", "speaker", "span", "text_sha256",
})
_STRUCTURED_DOCUMENT_REQUIRED = frozenset({
    "fact_id", "text", "source", "scope", "session", "version",
})

# Deploy-time config for the optional `polish` step -- never caller input (see
# build_polish_config's docstring for why).
POLISH_BASE_URL = _brand_env(
    "POLISH_BASE_URL", "https://api.groq.com/openai/v1/chat/completions")
POLISH_API_KEY_ENV = _brand_env("POLISH_API_KEY_ENV")

# Deploy-time config for which "activation mode" gates ENGINE.answer() (see maybe_answer()):
# "direct" (default -- every request runs the pipeline, today's only behavior, unchanged) or
# "keyword" (only run the pipeline when the question matches one of ACTIVATION_KEYWORDS). Never a
# per-request caller field -- same reasoning as POLISH_BASE_URL/POLISH_API_KEY_ENV above: a
# setting that changes whether/how much server-side work a request triggers must come from this
# process's own environment, not an unauthenticated caller's request body.
ACTIVATION_MODE = (_brand_env("ACTIVATION_MODE", "direct") or "direct").strip().lower()

# A small, closed, server-configurable trigger-phrase set -- not a growing dictionary. Overridable
# via PROOFRAY_ACTIVATION_KEYWORDS (legacy HORIZON_ACTIVATION_KEYWORDS; comma-separated),
# server-side only, mirroring the same
# closed-list discipline already used for `_RETRACTION_MARKER`/`_ZH_CORRECTION_MARKER` elsewhere
# in this project: a fixed, small set of trigger phrases, not an attempt to enumerate every way a
# caller might ask ProofRay to recall something.
DEFAULT_ACTIVATION_KEYWORDS = frozenset({
    "remember", "recall", "what did", "when did", "do you remember",
    "lembra", "lembrar", "lembra-se", "lembras", "você lembra", "se lembra",
})


def _parse_activation_keywords(raw: str | None) -> frozenset[str]:
    if not raw:
        return DEFAULT_ACTIVATION_KEYWORDS
    return frozenset(word.strip().lower() for word in raw.split(",") if word.strip())


ACTIVATION_KEYWORDS = _parse_activation_keywords(_brand_env("ACTIVATION_KEYWORDS"))

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


def _integer_field(raw: Mapping, field: str, position: int, *, minimum: int,
                   nullable: bool = False) -> int | None:
    value = raw.get(field)
    if nullable and value is None:
        return None
    # JSON booleans are Python ints, but accepting true as FactId/version/time silently corrupts
    # identity.  Require the exact scalar type at this transport boundary.
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        suffix = " or null" if nullable else ""
        raise ValueError(
            f"documents[{position}].{field} must be an integer >= {minimum}{suffix}")
    return value


def _text_field(raw: Mapping, field: str, position: int, *, nullable: bool = False) \
        -> str | None:
    value = raw.get(field)
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        suffix = " or null" if nullable else ""
        raise ValueError(f"documents[{position}].{field} must be non-empty text{suffix}")
    if field != "text" and (
            _utf8_size(value) > MAX_METADATA_BYTES or
            any(ord(character) < 32 or ord(character) == 127 for character in value)):
        raise ValueError(
            f"documents[{position}].{field} contains invalid or oversized metadata")
    return value


def _structured_document(raw: Mapping, position: int) -> RouteDocument:
    keys = frozenset(raw)
    unknown = keys - _STRUCTURED_DOCUMENT_FIELDS
    missing = _STRUCTURED_DOCUMENT_REQUIRED - keys
    if unknown:
        raise ValueError(
            f"documents[{position}] contains unknown fields: {', '.join(sorted(map(str, unknown)))}")
    if missing:
        raise ValueError(
            f"documents[{position}] is missing required fields: {', '.join(sorted(missing))}")

    fact_id = _integer_field(raw, "fact_id", position, minimum=0)
    scope = _integer_field(raw, "scope", position, minimum=0)
    version = _integer_field(raw, "version", position, minimum=1)
    sequence = _integer_field(raw, "sequence", position, minimum=0, nullable=True)
    event_time = _integer_field(raw, "event_time", position, minimum=0, nullable=True)
    text = _text_field(raw, "text", position)
    source = _text_field(raw, "source", position)
    session = _text_field(raw, "session", position)
    role = _text_field(raw, "role", position, nullable=True)
    speaker = _text_field(raw, "speaker", position, nullable=True)
    raw_span = raw.get("span")
    span = None
    if raw_span is not None:
        if (not isinstance(raw_span, list) or len(raw_span) != 2
                or any(isinstance(item, bool) or not isinstance(item, int)
                       for item in raw_span)
                or raw_span[0] < 0 or raw_span[1] <= raw_span[0]):
            raise ValueError(
                f"documents[{position}].span must be a non-empty two-integer interval or null")
        span = tuple(raw_span)
    supplied_digest = raw.get("text_sha256")
    if supplied_digest is not None:
        if (not isinstance(supplied_digest, str) or len(supplied_digest) != 64
                or any(character not in "0123456789abcdef" for character in supplied_digest)):
            raise ValueError(
                f"documents[{position}].text_sha256 must be a lowercase SHA-256 or null")
        observed_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if supplied_digest != observed_digest:
            raise ValueError(f"documents[{position}].text_sha256 does not match text")
    if fact_id >= MAX_FACT_ID:
        raise ValueError(
            f"documents[{position}].fact_id exceeds the supported identity domain")
    if version > MAX_VERSION:
        raise ValueError(
            f"documents[{position}].version exceeds the supported storage domain")
    if scope != SCOPE_ID:
        raise ValueError(
            f"documents[{position}].scope is outside this server's authorized scope")
    if role is not None and role not in ("user", "assistant", "system", "tool"):
        raise ValueError(
            f"documents[{position}].role must be user, assistant, system, tool, or null")
    if _utf8_size(text) > MAX_DOCUMENT_BYTES:
        raise ValueError(
            f"documents[{position}].text exceeds the {MAX_DOCUMENT_BYTES}-byte limit")
    return RouteDocument(
        fact_id, text, scope, session, version, source,
        sequence=sequence, span=span, event_time=event_time, role=role, speaker=speaker,
    )


def build_documents(raw_documents: list) -> tuple[RouteDocument, ...]:
    """Project either the legacy ``list[str]`` or the structured conversation schema.

    A request uses exactly one representation.  Mixed arrays fail closed instead of silently
    assigning transport defaults to only part of a conversation.  Structured metadata remains
    out-of-band in ``RouteDocument``; it is never prefixed or appended to authoritative text.
    """
    if not isinstance(raw_documents, list) or not raw_documents:
        raise ValueError("`documents` must be a non-empty array")
    legacy = all(isinstance(item, str) for item in raw_documents)
    structured = all(isinstance(item, Mapping) for item in raw_documents)
    if not legacy and not structured:
        raise ValueError(
            "`documents` must contain only strings or only structured document objects")
    if legacy:
        documents = []
        for i, text in enumerate(raw_documents, start=1):
            if not text.strip():
                raise ValueError(f"documents[{i - 1}] must be a non-empty string")
            if _utf8_size(text) > MAX_DOCUMENT_BYTES:
                raise ValueError(
                    f"documents[{i - 1}] exceeds the {MAX_DOCUMENT_BYTES}-byte limit")
            # Preserve the established legacy behavior, including outer-whitespace trimming and
            # deterministic generated identities.
            documents.append(RouteDocument(
                i, text.strip(), SCOPE_ID, SESSION_ID, 1, f"doc:{i}"))
        return tuple(documents)

    documents = tuple(_structured_document(raw, position)
                      for position, raw in enumerate(raw_documents))
    fact_ids = tuple(document.fact_id for document in documents)
    if len(fact_ids) != len(set(fact_ids)):
        raise ValueError("structured documents require unique fact_id values")
    return documents


def build_context_intents(raw_intents: object,
                          documents: tuple[RouteDocument, ...]) \
        -> tuple[AnswerContextIntent, ...]:
    """Validate observed subqueries without exposing a generic scorer-field channel."""
    if raw_intents is None:
        return ()
    if not isinstance(raw_intents, list) or len(raw_intents) > MAX_CONTEXT_INTENTS:
        raise ValueError(
            f"`context_intents` must be an array of at most {MAX_CONTEXT_INTENTS} objects")
    known = {document.fact_id for document in documents}
    seen = set()
    result = []
    for position, raw in enumerate(raw_intents):
        if not isinstance(raw, Mapping):
            raise ValueError(
                f"context_intents[{position}] must be an object")
        keys = frozenset(raw)
        required = frozenset({"intent_id", "text", "fact_ids"})
        allowed = required | {"turn_index", "session_id"}
        if not required <= keys or keys - allowed:
            raise ValueError(
                f"context_intents[{position}] requires intent_id, text, fact_ids and only "
                "optional turn_index/session_id")
        intent_id = _text_field(raw, "intent_id", position)
        intent_text = _text_field(raw, "text", position)
        if _utf8_size(intent_text) > MAX_QUESTION_BYTES:
            raise ValueError(
                f"context_intents[{position}].text exceeds the {MAX_QUESTION_BYTES}-byte limit")
        raw_fact_ids = raw.get("fact_ids")
        if not isinstance(raw_fact_ids, list) or not raw_fact_ids:
            raise ValueError(f"context_intents[{position}].fact_ids must be a non-empty array")
        fact_ids = []
        for fact_position, value in enumerate(raw_fact_ids):
            if isinstance(value, bool) or not isinstance(value, int) or value not in known:
                raise ValueError(
                    f"context_intents[{position}].fact_ids[{fact_position}] is unknown")
            fact_ids.append(value)
        canonical = tuple(sorted(set(fact_ids)))
        if len(canonical) != len(fact_ids):
            raise ValueError(f"context_intents[{position}].fact_ids must be unique")
        if intent_id in seen:
            raise ValueError("context_intents require unique intent_id values")
        seen.add(intent_id)
        turn_index = raw.get("turn_index")
        if (isinstance(turn_index, bool) or
                (turn_index is not None and
                 (not isinstance(turn_index, int) or turn_index < 0))):
            raise ValueError(
                f"context_intents[{position}].turn_index must be an integer >= 0 or null")
        session_id = raw.get("session_id")
        if session_id is not None:
            if not isinstance(session_id, str) or not session_id.strip():
                raise ValueError(
                    f"context_intents[{position}].session_id must be non-empty text or null")
            referenced_sessions = {
                document.session_id for document in documents
                if document.fact_id in canonical
            }
            if referenced_sessions != {session_id}:
                raise ValueError(
                    f"context_intents[{position}].session_id does not match its facts")
        result.append(AnswerContextIntent(
            intent_id, intent_text, canonical, turn_index=turn_index, session_id=session_id))
    return tuple(result)


def keyword_gate_matches(text: str, keywords: frozenset[str]) -> bool:
    """Case-insensitive substring match against a small, closed trigger-phrase set -- supports
    multi-word phrases ("what did", "você lembra") without needing tokenization, since these are
    meant as simple trigger phrases, not a word-class grammar."""
    lowered = text.casefold()
    return any(keyword in lowered for keyword in keywords)


def maybe_answer(question: str, documents: tuple[RouteDocument, ...], *,
                 context_intents: tuple[AnswerContextIntent, ...] = ()) \
        -> AnsweredResult | None:
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
    return ENGINE.answer(question, documents, context_intents=context_intents)


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
            "action": "not_activated",
            "answer": None,
            "evidence": None,
            "direct_answer": None,
            "direct_answer_state": None,
            "direct_answer_method": None,
            "direct_answer_sources": [],
            "direct_answer_proof_closed": None,
            "direct_answer_residual": [],
            "direct_answer_certificate": None,
            "direct_answer_certificate_encoding": None,
            "direct_answer_evidence": [],
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
    direct_sources = (frozenset(result.direct_answer.source_ids)
                      if include_sources else frozenset())
    direct_evidence = [
        {
            "fact_id": claim.fact_id,
            "text": claim.text,
            "source_id": claim.source_id,
            "scope": claim.scope_id,
            "session": claim.session_id,
            "version": claim.version,
            "generation_id": claim.generation_id,
            "sequence": claim.sequence,
            "event_time": claim.event_time,
            "role": claim.role,
            "speaker": claim.speaker,
            "source_span": (list(claim.source_span) if claim.source_span is not None else None),
            "parent_sha256": claim.parent_sha256,
        }
        for claim in result.resolver_evidence if claim.source_id in direct_sources
    ]
    payload = {
        "id": answer_id,
        "object": "answer",
        "created": created,
        "state": result.state.lower(),
        "action": "answer" if result.state == "RESOLVED" else "abstain",
        "answer": result.final_answer_text,
        "evidence": result.evidence_text,
        "direct_answer": result.direct_answer.text or None,
        "direct_answer_state": result.direct_answer.state,
        "direct_answer_method": result.direct_answer.method,
        "direct_answer_sources": list(result.direct_answer.source_ids),
        "direct_answer_proof_closed": result.direct_answer.proof_closed,
        "direct_answer_residual": list(result.direct_answer.residual),
        "direct_answer_certificate": (result.direct_answer.certificate.hex()
                                      if result.direct_answer.certificate else None),
        "direct_answer_certificate_encoding": (
            "hex" if result.direct_answer.certificate else None),
        "direct_answer_evidence": direct_evidence,
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
    (PROOFRAY_POLISH_BASE_URL / PROOFRAY_POLISH_API_KEY_ENV), never from the request body. An
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
    polish_result = adapter.polish(question, result.final_answer_text, config)
    if polish_result.state == "polished":
        return polish_result.text, "polished"
    return None, "error"


def new_answer_id_and_timestamp() -> tuple[str, int]:
    return new_id(), int(time.time())
