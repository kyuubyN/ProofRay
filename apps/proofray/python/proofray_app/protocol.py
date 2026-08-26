from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Mapping


BRIDGE_SCHEMA = "proofray.app.bridge.v1"
MAX_FRAME_BYTES = 1024 * 1024
_TERMINAL_EVENTS = frozenset({"completed", "error"})


class ProtocolError(ValueError):
    """A closed, non-sensitive bridge protocol failure."""


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ProtocolError(f"{label}_must_be_object")
    return dict(value)


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 160:
        raise ProtocolError(f"invalid_{label}")
    return value


@dataclass(frozen=True)
class BridgeRequest:
    request_id: str
    method: str
    payload: dict[str, object]

    @classmethod
    def decode(cls, raw: bytes) -> "BridgeRequest":
        if not raw or len(raw) > MAX_FRAME_BYTES:
            raise ProtocolError("frame_size")
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProtocolError("invalid_json") from None
        body = _object(value, "frame")
        if body.get("schema") != BRIDGE_SCHEMA:
            raise ProtocolError("schema_mismatch")
        return cls(
            _identifier(body.get("request_id"), "request_id"),
            _identifier(body.get("method"), "method"),
            _object(body.get("payload", {}), "payload"),
        )


@dataclass(frozen=True)
class BridgeEvent:
    request_id: str
    event: str
    payload: dict[str, object]

    def encode(self) -> bytes:
        body = {
            "schema": BRIDGE_SCHEMA,
            "request_id": _identifier(self.request_id, "request_id"),
            "event": _identifier(self.event, "event"),
            "payload": _object(self.payload, "payload"),
        }
        raw = json.dumps(
            body, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8") + b"\n"
        if len(raw) > MAX_FRAME_BYTES:
            raise ProtocolError("event_size")
        return raw

    @property
    def terminal(self) -> bool:
        return self.event in _TERMINAL_EVENTS


def safe_error(request_id: str, code: str) -> BridgeEvent:
    """Return only a closed error code; exception messages may contain user data."""
    return BridgeEvent(request_id, "error", {"code": _identifier(code, "error_code")})
