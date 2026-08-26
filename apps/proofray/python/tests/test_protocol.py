import json

import pytest

from proofray_app.protocol import (
    BRIDGE_SCHEMA, MAX_FRAME_BYTES, BridgeEvent, BridgeRequest, ProtocolError,
)


def _request(**updates):
    value = {
        "schema": BRIDGE_SCHEMA,
        "request_id": "req-1",
        "method": "bridge.health",
        "payload": {},
    }
    value.update(updates)
    return json.dumps(value).encode()


def test_request_round_trip_is_closed_and_versioned():
    request = BridgeRequest.decode(_request())
    assert request.request_id == "req-1"
    assert request.method == "bridge.health"
    assert request.payload == {}


@pytest.mark.parametrize("raw", [
    b"", b"not-json", _request(schema="wrong"),
    _request(request_id=""), _request(payload=[]),
    b"{" + b"x" * MAX_FRAME_BYTES,
], ids=("empty", "invalid-json", "schema", "request-id", "payload", "oversized"))
def test_invalid_request_fails_closed(raw):
    with pytest.raises(ProtocolError):
        BridgeRequest.decode(raw)


def test_event_is_canonical_and_terminal_only_at_boundary():
    event = BridgeEvent("req-1", "completed", {"z": 2, "a": 1})
    assert event.terminal
    assert event.encode() == (
        b'{"event":"completed","payload":{"a":1,"z":2},'
        b'"request_id":"req-1","schema":"proofray.app.bridge.v1"}\n')
    assert not BridgeEvent("req-1", "routing", {}).terminal
