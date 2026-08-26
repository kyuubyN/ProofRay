from __future__ import annotations

import base64
import ipaddress
import json
from typing import Mapping
import urllib.error
import urllib.request
from urllib.parse import urlparse


MAX_JSON_RESPONSE_BYTES = 8 * 1024 * 1024


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(
            req.full_url, code, "redirects are forbidden for credentialed connectors",
            headers, fp)


class SafeJsonTransport:
    """Small transport that never forwards credentials across redirects."""

    def __init__(self, *, timeout: float = 10.0):
        self.timeout = timeout
        self._opener = urllib.request.build_opener(_NoRedirect)

    def request(self, method: str, url: str, *, headers: Mapping[str, str] | None = None,
                body: object | bytes | None = None) -> object:
        if not url.startswith(("http://", "https://")):
            raise ValueError("connector transport requires HTTP(S)")
        parsed = urlparse(url)
        if parsed.scheme == "http" and not _loopback(parsed.hostname):
            raise ValueError("remote HTTP connectors require TLS")
        raw = body if isinstance(body, bytes) else (
            None if body is None else json.dumps(
                body, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        request = urllib.request.Request(
            url, data=raw, method=method,
            headers={"Accept": "application/json", **dict(headers or {})},
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                payload = response.read(MAX_JSON_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            error.read(MAX_JSON_RESPONSE_BYTES + 1)
            raise RuntimeError(f"connector_http_{error.code}") from None
        if len(payload) > MAX_JSON_RESPONSE_BYTES:
            raise RuntimeError("connector_response_too_large")
        if not payload:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            raise RuntimeError("connector_invalid_json") from None


def _loopback(host: str | None) -> bool:
    if host is None:
        return False
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def authorization_headers(secret: str | None, kind: str | None) -> dict[str, str]:
    if not secret:
        return {}
    if kind == "basic":
        encoded = base64.b64encode(secret.encode("utf-8")).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}
    if kind == "api_key":
        return {"Authorization": f"ApiKey {secret}"}
    return {"Authorization": f"Bearer {secret}"}


__all__ = ["MAX_JSON_RESPONSE_BYTES", "SafeJsonTransport", "authorization_headers"]
