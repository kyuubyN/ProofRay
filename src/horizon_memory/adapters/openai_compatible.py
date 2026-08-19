# SPDX-License-Identifier: Apache-2.0 OR AGPL-3.0-or-later
# Copyright (c) 2026 kyuubyN
"""A generic polish adapter for any OpenAI-compatible `chat/completions` endpoint.

This is not a reader adapter (see `base.py`'s `ModelAdapter`): a reader consumes *untrusted*
evidence and must cite or ABSTAIN. A polish adapter consumes already-composed, already-verified
text (e.g. `HorizonAnswerEngine`'s own `AnsweredResult.answer_text`) and has exactly one job --
rewrite it fluently without adding, removing, or inventing anything. No citations, no ABSTAIN,
no evidence pack. Forcing this into `ModelAdapter`'s contract would mean wrapping already-trusted
text in `EvidencePack.render_untrusted`, which is semantically backwards.

The endpoint shape (`POST {base_url}` with `{"model", "messages", ...}`, response
`choices[0].message.content`) is common to Groq, OpenAI, Ollama, llama.cpp's server, vLLM, and LM
Studio -- one adapter, `base_url`/`api_key_env` vary per deployment. `api_key_env=None` sends no
`Authorization` header at all, which is what makes the same code path work for an unauthenticated
local server and a hosted API with one constructor argument's difference.

Secret handling matches `gemini.py`'s adapter: `api_key_env` names an environment variable read
at call time, never a literal key accepted as an argument, never logged or placed in an exception
message.

**Transport is `requests`, not stdlib `urllib` (unlike `gemini.py`'s dependency-free transport).**
Verified empirically, 2026-08-19: a real call against Groq's endpoint with plain `urllib` --
even with a normal, honest `User-Agent` header set -- was rejected with HTTP 403 (Cloudflare
error 1010, a TLS/JA3-fingerprint-level bot-signature block, not a header check `urllib` can
satisfy). The identical request via `requests` succeeded (200). Since this adapter's whole
purpose is working against real hosted providers (unlike `gemini.py`, which only ever needs to
work against Google's specific infrastructure), reliability against Cloudflare-fronted APIs is
load-bearing, not optional -- `requests` is genuinely required here, `urllib` is not a viable
substitute for this specific adapter.

To keep `pip install -e .`'s core install minimal (numpy only, unaffected by this file), `requests`
is imported lazily inside `RequestsTransport.request()`, not at module load time -- importing this
module, constructing `PolishConfig`/`OpenAICompatiblePolishAdapter`, and running in dry-run mode
(`allow_network=False`, the default) all work with zero extra dependencies. Only an actual network
call requires `requests` installed (see `api/requirements.txt`).

**`polish()` requires the original question (2026-08-19, real bug found by the project owner
during a live benchmark run, confirmed and fixed same-session)**: the first shipped version of
this method took only `answer_text`, never the question -- despite `_SYSTEM_PROMPT` itself
claiming "the facts below... answer the user's question," the model was never actually shown
that question, and had to infer intent purely from which facts were selected. Every polish
benchmark run before this fix (Groq/Qwen, Gemini-3.1-Flash-Lite, local Qwen3-1.7B) was measured
under this gap -- their numbers are a lower bound on what the mechanism can do, not a ceiling.
`question` is now a required positional argument; there is no silent backward-compatible default,
since a caller silently omitting it would reproduce the exact bug this fixes.
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Protocol

_THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL)
_THINK_PREFIX = re.compile(r"^.*?</think>", re.DOTALL)
_TRANSIENT_HTTP_CODES = frozenset({429, 500, 502, 503, 504})

_SYSTEM_PROMPT = (
    "You are a writer. You are given the user's QUESTION and a set of facts that are ALREADY "
    "VERIFIED and answer it. Your only task is to rewrite those facts as one fluent, "
    "well-organized answer to the QUESTION.\n\n"
    "STRICT RULES:\n"
    "- Do not add any information that is not in the facts below.\n"
    "- Do not remove numbers, names, or specific details present in the facts.\n"
    "- Do not speculate or fill gaps -- if the facts do not cover something, simply omit it.\n"
    "- Use the QUESTION only to decide what's relevant and how to frame the answer -- never pull "
    "in outside knowledge it might suggest.\n"
    "- Only reorganize and rewrite for natural flow, as a direct answer to the question."
)


@dataclass(frozen=True)
class TransportResponse:
    status: int
    headers: dict[str, str]
    body: bytes


class Transport(Protocol):
    def request(self, url: str, headers: dict[str, str], body: bytes | None,
                timeout: float) -> TransportResponse: ...


class RequestsTransport:
    """The default network transport -- see the module docstring for why `requests` (not stdlib
    `urllib`) is required here. Import is lazy so this module and dry-run mode never need
    `requests` installed; only an actual network call does."""

    def request(self, url, headers, body, timeout):
        try:
            import requests
        except ImportError as exc:
            raise RuntimeError(
                "the 'requests' package is required for OpenAICompatiblePolishAdapter network "
                "calls (pip install requests, or pass your own Transport implementation to "
                "OpenAICompatiblePolishAdapter(transport=...))") from exc
        response = requests.post(url, headers=headers, data=body, timeout=timeout) \
            if body is not None else requests.get(url, headers=headers, timeout=timeout)
        return TransportResponse(response.status_code, dict(response.headers), response.content)


@dataclass(frozen=True)
class PolishConfig:
    model: str
    base_url: str = "https://api.groq.com/openai/v1/chat/completions"
    api_key_env: str | None = None
    temperature: float = 0.1
    max_output_tokens: int = 1200
    timeout_seconds: float = 60.0
    reasoning_effort: str | None = "none"
    # Provider-specific request body fields not covered above (e.g. a local llama.cpp server's
    # `chat_template_kwargs: {"enable_thinking": false}` to disable Qwen3's native thinking mode
    # at the source, rather than relying only on `_strip_reasoning_trace` after the fact). Merged
    # into the request payload as-is; None (the default) adds nothing.
    extra_body: dict | None = None
    # Retry/backoff for transient failures (rate limits, momentary outages) -- same defaults as
    # lab/judge_client.py's already-validated MAX_RETRIES/RETRY_BASE_SECONDS, chosen through this
    # project's own real experience with Groq/Gemini free-tier rate limits. `max_retries=0`
    # disables retries entirely (fails immediately on the first transient error).
    max_retries: int = 4
    retry_base_seconds: float = 20.0

    def __post_init__(self) -> None:
        if not self.model:
            raise ValueError("PolishConfig.model is required")
        if not self.base_url:
            raise ValueError("PolishConfig.base_url is required")
        if not 0 <= self.temperature <= 2:
            raise ValueError("temperature must be in [0,2]")
        if self.max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if self.retry_base_seconds <= 0:
            raise ValueError("retry_base_seconds must be positive")
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")


@dataclass(frozen=True)
class PolishResult:
    state: str  # "polished" | "error" | "dry_run"
    text: str
    model: str
    input_tokens: int | None
    output_tokens: int | None
    latency_seconds: float | None
    error_code: str = ""


def _strip_reasoning_trace(text: str) -> str:
    text = _THINK_BLOCK.sub("", text)
    if "</think>" in text:
        text = _THINK_PREFIX.sub("", text)
    return text.strip()


def _approx_tokens(text: str) -> int:
    return max(1, len(text.encode("utf-8")) // 4)


class OpenAICompatiblePolishAdapter:
    """Rewrites already-verified text for fluency against any OpenAI-compatible endpoint."""

    def __init__(self, *, transport: Transport | None = None, allow_network: bool = False):
        self.transport = transport or RequestsTransport()
        self.allow_network = allow_network

    def polish(self, question: str, answer_text: str, config: PolishConfig) -> PolishResult:
        if not question.strip():
            raise ValueError("question is required -- see module docstring: omitting it left "
                             "the polish model to guess the user's intent from the facts alone")
        if not answer_text.strip():
            return PolishResult("polished", answer_text, config.model, 0, 0, 0.0)

        prompt = f"QUESTION:\n{question}\n\nVERIFIED FACTS:\n{answer_text}\n\nREWRITTEN ANSWER:"
        if not self.allow_network:
            return PolishResult("dry_run", "", config.model, _approx_tokens(prompt), None, None)

        headers = {"Content-Type": "application/json"}
        if config.api_key_env:
            key = os.environ.get(config.api_key_env)
            if not key:
                return PolishResult(
                    "error", "", config.model, None, None, None,
                    error_code=f"missing_env_var:{config.api_key_env}")
            headers["Authorization"] = f"Bearer {key}"

        payload = {
            "model": config.model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": config.temperature,
            "max_tokens": config.max_output_tokens,
        }
        if config.reasoning_effort is not None:
            payload["reasoning_effort"] = config.reasoning_effort
        if config.extra_body:
            payload.update(config.extra_body)
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()

        # Retries any transport-level exception (network drop, timeout, DNS failure -- whatever
        # the concrete `Transport` implementation raises; not narrowed to specific `requests`
        # exception types so this doesn't need an eager `import requests` just to catch them) and
        # any transient HTTP status (429 rate limit, 5xx) with backoff, matching
        # lab/judge_client.py's own already-validated retry shape for the same providers.
        started = time.perf_counter()
        response, error_code = None, ""
        for attempt in range(config.max_retries + 1):
            try:
                response = self.transport.request(
                    config.base_url, headers, body, config.timeout_seconds)
            except Exception as exc:
                error_code = type(exc).__name__
                response = None
            if response is not None:
                if response.status not in _TRANSIENT_HTTP_CODES:
                    break
                error_code = f"http_{response.status}"
            if attempt < config.max_retries:
                time.sleep(config.retry_base_seconds * (attempt + 1))
        latency = time.perf_counter() - started

        if response is None:
            return PolishResult("error", "", config.model, None, None, latency,
                                error_code=error_code)
        if response.status != 200:
            return PolishResult("error", "", config.model, None, None, latency,
                                error_code=f"http_{response.status}")
        try:
            decoded = json.loads(response.body)
            text = _strip_reasoning_trace(decoded["choices"][0]["message"]["content"])
            usage = decoded.get("usage", {})
            input_tokens = usage.get("prompt_tokens")
            output_tokens = usage.get("completion_tokens")
        except (ValueError, TypeError, KeyError, IndexError):
            return PolishResult("error", "", config.model, None, None, latency,
                                error_code="invalid_response_schema")
        return PolishResult("polished", text, config.model, input_tokens, output_tokens, latency)


__all__ = [
    "OpenAICompatiblePolishAdapter", "PolishConfig", "PolishResult", "Transport",
    "TransportResponse", "RequestsTransport",
]
