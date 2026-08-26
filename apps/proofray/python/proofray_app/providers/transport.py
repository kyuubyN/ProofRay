from __future__ import annotations

from dataclasses import dataclass
import json
from threading import Event, Lock
from typing import Iterator, Mapping, Protocol
import urllib.error
import urllib.request


_MAX_PROVIDER_RESPONSE_BYTES = 8 * 1024 * 1024


class _NoCredentialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code, "provider redirects are forbidden", headers, fp)


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes


class ProviderTransport(Protocol):
    def request(self, method: str, url: str, headers: Mapping[str, str],
                body: bytes | None, timeout: float) -> HttpResponse: ...
    def stream(self, method: str, url: str, headers: Mapping[str, str],
               body: bytes | None, timeout: float) -> Iterator[bytes]: ...


class UrllibProviderTransport:
    def __init__(self):
        self._cancelled = Event()
        self._opener = urllib.request.build_opener(_NoCredentialRedirect)
        self._active_response = None
        self._response_lock = Lock()

    def cancel(self) -> None:
        self._cancelled.set()
        with self._response_lock:
            response = self._active_response
        if response is not None:
            try:
                response.close()
            except OSError:
                pass

    def request(self, method, url, headers, body, timeout):
        request = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
        try:
            with self._opener.open(request, timeout=timeout) as response:
                payload = response.read(_MAX_PROVIDER_RESPONSE_BYTES + 1)
                if len(payload) > _MAX_PROVIDER_RESPONSE_BYTES:
                    raise RuntimeError("provider_response_too_large")
                return HttpResponse(response.status, dict(response.headers), payload)
        except urllib.error.HTTPError as error:
            payload = error.read(_MAX_PROVIDER_RESPONSE_BYTES + 1)
            if len(payload) > _MAX_PROVIDER_RESPONSE_BYTES:
                raise RuntimeError("provider_response_too_large") from None
            return HttpResponse(error.code, dict(error.headers), payload)

    def stream(self, method, url, headers, body, timeout):
        self._cancelled.clear()
        request = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
        try:
            with self._opener.open(request, timeout=timeout) as response:
                with self._response_lock:
                    self._active_response = response
                if response.status != 200:
                    response.read()
                    raise RuntimeError(f"provider_http_{response.status}")
                total = 0
                while not self._cancelled.is_set():
                    line = response.readline()
                    if not line:
                        break
                    total += len(line)
                    if total > _MAX_PROVIDER_RESPONSE_BYTES or len(line) > 1024 * 1024:
                        raise RuntimeError("provider_stream_too_large")
                    yield line.rstrip(b"\r\n")
        except urllib.error.HTTPError as error:
            error.read(_MAX_PROVIDER_RESPONSE_BYTES + 1)
            raise RuntimeError(f"provider_http_{error.code}") from None
        except OSError:
            if self._cancelled.is_set():
                return
            raise RuntimeError("provider_transport_error") from None
        finally:
            with self._response_lock:
                self._active_response = None


def json_body(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def json_response(response: HttpResponse) -> object:
    if response.status != 200:
        raise RuntimeError(f"provider_http_{response.status}")
    try:
        return json.loads(response.body)
    except json.JSONDecodeError:
        raise RuntimeError("provider_invalid_json") from None


def sse_payloads(lines: Iterator[bytes]) -> Iterator[object]:
    for raw in lines:
        if not raw.startswith(b"data:"):
            continue
        payload = raw[5:].strip()
        if payload == b"[DONE]":
            break
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            raise RuntimeError("provider_invalid_sse") from None


__all__ = [
    "HttpResponse", "ProviderTransport", "UrllibProviderTransport", "json_body",
    "json_response", "sse_payloads",
]
