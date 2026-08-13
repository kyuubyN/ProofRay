# SPDX-License-Identifier: Apache-2.0 OR AGPL-3.0-or-later
# Copyright (c) 2026 kyuubyN
"""Gemini reader adapter with an offline-by-default network boundary.

No secret is accepted as an argument. The key is read only from GEMINI_API_KEY at the
last possible moment and never enters URLs, ledgers, exceptions or result objects.
"""
from __future__ import annotations

import email.utils
import hashlib
import json
import os
import random
import threading
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from .base import (
    GenerationConfig, ModelAdapter, ModelRun, ModelRunState, PrefillMetrics, build_prompt,
    request_digest,
)
from ..evidence import EvidencePack


DEFAULT_MODEL_ID = "gemma-4-31b-it"
API_VERSION = "v1beta"
SDK_VERSION = "stdlib-rest-v1"


@dataclass(frozen=True)
class GeminiPricing:
    input_usd_per_million: float = 0.0
    output_usd_per_million: float = 0.0

    def estimate(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.input_usd_per_million +
                output_tokens * self.output_usd_per_million) / 1_000_000


@dataclass(frozen=True)
class TransportResponse:
    status: int
    headers: dict[str, str]
    body: bytes


class GeminiTransport(Protocol):
    def request(self, url: str, headers: dict[str, str], body: bytes | None,
                timeout: float) -> TransportResponse: ...


class UrllibTransport:
    def request(self, url, headers, body, timeout):
        request = urllib.request.Request(url, data=body, headers=headers,
                                         method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return TransportResponse(response.status, dict(response.headers), response.read())
        except urllib.error.HTTPError as exc:
            return TransportResponse(exc.code, dict(exc.headers), exc.read())


class RateLimiter:
    """Process-local fixed window limiter for RPM and input TPM."""

    def __init__(self, rpm: int, tpm: int, clock=time.monotonic, sleeper=time.sleep):
        if rpm <= 0 or tpm <= 0:
            raise ValueError("rpm and tpm must be positive")
        self.rpm, self.tpm, self._clock, self._sleep = rpm, tpm, clock, sleeper
        self._lock = threading.Lock()
        self._window = clock()
        self._requests = self._tokens = 0

    def acquire(self, input_tokens: int) -> None:
        if input_tokens > self.tpm:
            raise ValueError("single request exceeds TPM")
        with self._lock:
            now = self._clock()
            if now - self._window >= 60:
                self._window, self._requests, self._tokens = now, 0, 0
            if self._requests + 1 > self.rpm or self._tokens + input_tokens > self.tpm:
                delay = max(0.0, 60 - (now - self._window))
                self._sleep(delay)
                self._window, self._requests, self._tokens = self._clock(), 0, 0
            self._requests += 1
            self._tokens += input_tokens


class RequestLedger:
    """Append-only metadata ledger. Prompts, outputs and credentials are deliberately absent."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.entries: list[dict] = []
        self.completed: set[str] = set()
        if self.path and self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    entry = json.loads(line)
                    self.entries.append(entry)
                    if entry.get("terminal"):
                        self.completed.add(entry["request_digest"])
                except (ValueError, KeyError):
                    raise ValueError("invalid request ledger") from None

    def append(self, entry: dict) -> None:
        forbidden = {"prompt", "output", "api_key", "authorization", "x-goog-api-key"}
        if forbidden.intersection(key.casefold() for key in entry):
            raise ValueError("sensitive field forbidden in ledger")
        safe = json.loads(json.dumps(entry, sort_keys=True))
        self.entries.append(safe)
        if safe.get("terminal"):
            self.completed.add(safe["request_digest"])
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, (json.dumps(safe, sort_keys=True) + "\n").encode())
                os.fsync(fd)
            finally:
                os.close(fd)


def _approx_tokens(text: str) -> int:
    return max(1, len(text.encode("utf-8")) // 4)


def _retry_after(headers: dict[str, str]) -> float | None:
    value = next((v for k, v in headers.items() if k.casefold() == "retry-after"), None)
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value).timestamp() - time.time()
            return max(0.0, parsed)
        except (TypeError, ValueError):
            return None


class GeminiModelAdapter(ModelAdapter):
    def __init__(self, *, model_id: str = DEFAULT_MODEL_ID, allow_network: bool = False,
                 transport: GeminiTransport | None = None, ledger: RequestLedger | None = None,
                 rate_limiter: RateLimiter | None = None, pricing: GeminiPricing = GeminiPricing(),
                 max_attempts: int = 4, sleeper=time.sleep, rng: random.Random | None = None):
        if any(tag in model_id.casefold() for tag in ("latest", "preview", "experimental", "-exp")):
            raise ValueError("Gemini model must be a specific stable identifier")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.model_id, self.backend = model_id, "gemini-developer-api-rest"
        self.allow_network = allow_network
        self.transport = transport or UrllibTransport()
        self.ledger = ledger or RequestLedger()
        self.rate_limiter = rate_limiter
        self.pricing, self.max_attempts = pricing, max_attempts
        self._sleep, self._rng = sleeper, rng or random.Random(2608)
        self._metadata_verified = any(
            entry.get("kind") == "model_metadata" and entry.get("status") == "verified" and
            entry.get("model_id") == self.model_id
            for entry in self.ledger.entries
        )

    def dry_run(self, question: str, evidence_pack: EvidencePack | None,
                config: GenerationConfig) -> ModelRun:
        prompt = build_prompt(question, evidence_pack, config)
        digest = request_digest(self.model_id, prompt, config)
        tokens = _approx_tokens(prompt)
        return ModelRun(
            ModelRunState.DRY_RUN, self.model_id, self.backend, "", tokens, 0, None, None,
            None, None, "dry_run", digest,
            evidence_pack.integrity_digest if evidence_pack else "",
            evidence_pack.citations if evidence_pack else (), attempts=0,
            token_count_method="utf8_bytes_div4_estimate",
        )

    def generate(self, question, evidence_pack, config):
        if not self.allow_network:
            return self.dry_run(question, evidence_pack, config)
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is required for an authorized external run")
        prompt = build_prompt(question, evidence_pack, config)
        digest = request_digest(self.model_id, prompt, config)
        if digest in self.ledger.completed:
            return ModelRun(
                ModelRunState.BLOCKED, self.model_id, self.backend, "", _approx_tokens(prompt), 0,
                None, None, None, None, "checkpoint_complete", digest,
                evidence_pack.integrity_digest if evidence_pack else "",
                evidence_pack.citations if evidence_pack else (), attempts=0, cache_hit=True,
                error_code="checkpoint_complete", token_count_method="utf8_bytes_div4_estimate",
            )
        input_estimate = _approx_tokens(prompt)
        metadata_error = self._ensure_model_metadata(key, config.timeout_seconds)
        if metadata_error:
            return self._error_run(digest, evidence_pack, input_estimate, 1,
                                   metadata_error, time.perf_counter())
        if self.rate_limiter:
            self.rate_limiter.acquire(input_estimate)
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": config.max_output_tokens, "temperature": config.temperature,
                "topP": config.top_p,
            },
        }
        if config.seed is not None:
            payload["generationConfig"]["seed"] = config.seed
        url = (f"https://generativelanguage.googleapis.com/{API_VERSION}/models/"
               f"{self.model_id}:generateContent")
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        started, response, attempts = time.perf_counter(), None, 0
        for attempt in range(1, self.max_attempts + 1):
            attempts = attempt
            try:
                response = self.transport.request(
                    url, {"Content-Type": "application/json", "x-goog-api-key": key}, body,
                    config.timeout_seconds,
                )
            except Exception as exc:
                if attempt == self.max_attempts:
                    return self._error_run(digest, evidence_pack, input_estimate, attempts,
                                           type(exc).__name__, started)
                self._sleep(min(30.0, 2 ** (attempt - 1) + self._rng.random()))
                continue
            if response.status not in (429, 500, 502, 503, 504):
                break
            if attempt < self.max_attempts:
                delay = _retry_after(response.headers)
                self._sleep(delay if delay is not None else
                            min(30.0, 2 ** (attempt - 1) + self._rng.random()))
        latency = time.perf_counter() - started
        if response is None or response.status != 200:
            return self._error_run(digest, evidence_pack, input_estimate, attempts,
                                   f"http_{response.status if response else 'transport'}", started)
        try:
            decoded = json.loads(response.body)
            candidate = decoded.get("candidates", [])[0]
            output = "".join(part.get("text", "") for part in
                             candidate.get("content", {}).get("parts", []))
            usage = decoded.get("usageMetadata", {})
            input_tokens = int(usage.get("promptTokenCount", input_estimate))
            output_tokens = int(usage.get("candidatesTokenCount", _approx_tokens(output)))
            cached_input_tokens = int(usage.get("cachedContentTokenCount", 0) or 0)
            total_tokens = int(usage.get("totalTokenCount", input_tokens + output_tokens) or
                               (input_tokens + output_tokens))
            finish = str(candidate.get("finishReason", "STOP")).casefold()
            state = ModelRunState.ABSTAINED if output.strip() == "ABSTAIN" else ModelRunState.GENERATED
        except (ValueError, TypeError, IndexError, KeyError):
            return self._error_run(digest, evidence_pack, input_estimate, attempts,
                                   "invalid_response_schema", started)
        entry = {
            "schema": "qhdre.gemini-request-ledger.v1", "request_digest": digest,
            "model_id": self.model_id, "status": state.value, "attempts": attempts,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cached_input_tokens": cached_input_tokens, "total_tokens": total_tokens,
            "latency_seconds": round(latency, 6),
            "estimated_cost_usd": round(self.pricing.estimate(input_tokens, output_tokens), 8),
            "terminal": True,
        }
        self.ledger.append(entry)
        return ModelRun(
            state, self.model_id, self.backend, output, input_tokens, output_tokens, None, latency,
            latency, output_tokens / latency if latency else None, finish, digest,
            evidence_pack.integrity_digest if evidence_pack else "",
            evidence_pack.citations if evidence_pack else (), attempts=attempts,
            token_count_method="api_usage_metadata", cached_input_tokens=cached_input_tokens,
            total_tokens=total_tokens,
        )

    def _ensure_model_metadata(self, key: str, timeout: float) -> str:
        if self._metadata_verified:
            return ""
        url = f"https://generativelanguage.googleapis.com/{API_VERSION}/models/{self.model_id}"
        try:
            response = self.transport.request(url, {"x-goog-api-key": key}, None, timeout)
        except Exception as exc:
            return f"metadata_{type(exc).__name__}"
        if response.status != 200:
            return f"metadata_http_{response.status}"
        try:
            metadata = json.loads(response.body)
            name = str(metadata["name"]).removeprefix("models/")
            input_limit = int(metadata["inputTokenLimit"])
            output_limit = int(metadata["outputTokenLimit"])
            methods = tuple(metadata.get("supportedGenerationMethods", ()))
            if name != self.model_id or input_limit < 1 or output_limit < 1:
                return "metadata_identity_mismatch"
            if methods and "generateContent" not in methods:
                return "metadata_generate_unsupported"
        except (ValueError, TypeError, KeyError):
            return "metadata_invalid_schema"
        digest = hashlib.sha256(f"model-metadata:{name}:{input_limit}:{output_limit}".encode()).hexdigest()
        self.ledger.append({
            "schema": "qhdre.gemini-request-ledger.v1", "kind": "model_metadata",
            "request_digest": digest, "model_id": name, "status": "verified",
            "input_token_limit": input_limit, "output_token_limit": output_limit,
            "terminal": True,
        })
        self._metadata_verified = True
        return ""

    def _error_run(self, digest, pack, input_tokens, attempts, error_code, started):
        latency = time.perf_counter() - started
        self.ledger.append({
            "schema": "qhdre.gemini-request-ledger.v1", "request_digest": digest,
            "model_id": self.model_id, "status": "error", "attempts": attempts,
            "input_tokens": input_tokens, "output_tokens": 0,
            "latency_seconds": round(latency, 6), "estimated_cost_usd": 0.0,
            "error_code": error_code, "terminal": False,
        })
        return ModelRun(
            ModelRunState.ERROR, self.model_id, self.backend, "", input_tokens, 0, None, None,
            latency, None, "error", digest, pack.integrity_digest if pack else "",
            pack.citations if pack else (), attempts=attempts, error_code=error_code,
            token_count_method="utf8_bytes_div4_estimate",
        )

    def measure_prefill(self, question, evidence_pack, config):
        tokens = _approx_tokens(build_prompt(question, evidence_pack, config))
        return PrefillMetrics(tokens, None, None)


def scan_for_gemini_secrets(paths, known_key: str | None = None) -> tuple[str, ...]:
    """Scans text artifacts without returning matched secret material."""
    findings = []
    markers = (b"AIza", b"x-goog-api-key", b"Authorization: Bearer", b"GEMINI_API_KEY=")
    known = known_key.encode() if known_key else None
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_file() or path.stat().st_size > 16 * 1024 * 1024:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            findings.append(f"{path}:unreadable")
            continue
        if (known and known in data) or any(marker in data for marker in markers):
            findings.append(f"{path}:credential-pattern")
    return tuple(findings)
