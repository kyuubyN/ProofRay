import asyncio
import json
import os
import threading

import pytest

from proofray_app.bridge_server import HostCallBroker, ProofRayBridgeServer, _load_bootstrap
from proofray_app.memory_service import MemoryReply
from proofray_app.protocol import BRIDGE_SCHEMA, BridgeRequest


class _Writer:
    def __init__(self):
        self.buffer = bytearray()
        self.closed = False

    def write(self, value):
        self.buffer.extend(value)

    async def drain(self):
        return None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None

    def events(self):
        return [json.loads(line) for line in bytes(self.buffer).splitlines()]


class _Memory:
    def answer_prior(self, conversation_id, question, **kwargs):
        return MemoryReply("abstention", "", True)

    def remember_user_message(self, **kwargs):
        return None


def _request(request_id, method, payload):
    return BridgeRequest.decode(json.dumps({
        "schema": BRIDGE_SCHEMA,
        "request_id": request_id,
        "method": method,
        "payload": payload,
    }, separators=(",", ":")).encode())


def test_bridge_rejects_wrong_token_without_echoing_it():
    asyncio.run(_wrong_token_case())


def test_bootstrap_never_contains_token_and_process_lease_is_consumed(tmp_path):
    path = tmp_path / "bootstrap.json"
    path.write_text(json.dumps({
        "schema": "proofray.app.bootstrap.v1",
        "profile_name": "Alice",
        "timezone": "America/Sao_Paulo",
    }))
    os.environ["PROOFRAY_APP_TOKEN"] = "a" * 64
    assert _load_bootstrap(path) == (
        "a" * 64, "Alice", "America/Sao_Paulo")
    assert "PROOFRAY_APP_TOKEN" not in os.environ
    assert "a" * 64 not in path.read_text()


def test_bootstrap_rejects_wrong_schema_and_oversize_without_echo(tmp_path):
    path = tmp_path / "bootstrap.json"
    path.write_text('{"schema":"wrong"}')
    os.environ["PROOFRAY_APP_TOKEN"] = "b" * 64
    with pytest.raises(ValueError, match="schema"):
        _load_bootstrap(path)
    path.write_bytes(b"x" * (16 * 1024 + 1))
    os.environ["PROOFRAY_APP_TOKEN"] = "c" * 64
    with pytest.raises(ValueError, match="too large"):
        _load_bootstrap(path)


async def _wrong_token_case():
    service = ProofRayBridgeServer("a" * 64, memory=_Memory())
    writer = _Writer()
    valid = await service._authenticate(
        _request("auth", "bridge.authenticate", {"token": "b" * 64}), writer)
    assert valid is False
    assert writer.events() == [{
        "event": "error", "payload": {"code": "authentication_failed"},
        "request_id": "auth", "schema": BRIDGE_SCHEMA,
    }]
    assert "b" * 64 not in bytes(writer.buffer).decode()
    await service.close()


def test_non_memory_turn_never_emits_memory_started():
    asyncio.run(_non_memory_case())


def test_message_identity_cannot_ambiguous_sidecar_membership():
    service = ProofRayBridgeServer("a" * 64, memory=_Memory())
    request = _request("ask", "message.send", {
        "conversation_id": "thread:ambiguous", "message_id": "m1",
        "text": "Olá", "memory_mode": "off",
        "sequence": 0, "created_at": "2026-08-25T12:00:00Z",
    })
    with pytest.raises(ValueError):
        service._message_arguments(request)
    asyncio.run(service.close())


def test_keyword_configuration_is_bounded_before_regex_compilation():
    service = ProofRayBridgeServer("a" * 64, memory=_Memory())
    request = _request("ask", "message.send", {
        "conversation_id": "thread", "message_id": "m1",
        "text": "Olá", "memory_mode": "keywords", "keywords": ["x"] * 65,
        "sequence": 0, "created_at": "2026-08-25T12:00:00Z",
    })
    with pytest.raises(ValueError):
        service._message_arguments(request)
    asyncio.run(service.close())


def test_only_one_authenticated_flutter_session_can_own_host_commits():
    asyncio.run(_single_authenticated_host_case())


async def _single_authenticated_host_case():
    service = ProofRayBridgeServer("a" * 64, memory=_Memory())
    first = _Writer()
    second = _Writer()
    request = _request("auth", "bridge.authenticate", {"token": "a" * 64})
    assert await service._authenticate(request, first) is True
    assert await service._authenticate(request, second) is False
    assert first.events()[-1]["event"] == "authenticated"
    assert second.events()[-1] == {
        "event": "error", "payload": {"code": "authentication_failed"},
        "request_id": "auth", "schema": BRIDGE_SCHEMA,
    }
    service._host_calls.unbind(first)
    await service.close()


async def _non_memory_case():
    service = ProofRayBridgeServer("a" * 64, memory=_Memory())
    writer = _Writer()
    request = _request("ask", "message.send", {
        "conversation_id": "thread", "message_id": "m1",
        "text": "Olá", "memory_mode": "off",
        "sequence": 0, "created_at": "2026-08-25T12:00:00Z",
    })
    await service._dispatch(request, writer)
    events = writer.events()
    assert [item["event"] for item in events] == ["completed"]
    assert events[0]["payload"]["memory_consulted"] is False
    await service.close()


def test_memory_turn_emits_progress_and_truthful_marker():
    asyncio.run(_memory_case())


async def _memory_case():
    service = ProofRayBridgeServer("a" * 64, memory=_Memory())
    writer = _Writer()
    request = _request("ask", "message.send", {
        "conversation_id": "thread", "message_id": "m1",
        "text": "Você lembra da viagem?", "memory_mode": "forceNext",
        "sequence": 0, "created_at": "2026-08-25T12:00:00Z",
    })
    await service._dispatch(request, writer)
    events = writer.events()
    assert [item["event"] for item in events] == [
        "memory.started", "routing", "verifying", "abstained", "completed"]
    assert events[-1]["payload"]["memory_consulted"] is True
    await service.close()


def test_host_call_broker_requires_ack_before_worker_returns():
    asyncio.run(_host_call_case())


async def _host_call_case():
    broker = HostCallBroker()
    writer = _Writer()
    broker.bind(asyncio.get_running_loop(), writer)
    result = []
    thread = threading.Thread(
        target=lambda: result.append(
            broker.call("sidecar.replace_suffix", {"records": ["YQ=="]})),
        daemon=True,
    )
    thread.start()
    events = []
    for _ in range(100):
        await asyncio.sleep(0.01)
        events = writer.events()
        if events:
            break
    assert events
    assert thread.is_alive()
    host_request = events[-1]
    assert host_request["event"] == "host.request"
    accepted = broker.resolve(_request(
        host_request["request_id"], "host.response",
        {"ok": True, "payload": {"committed": True}},
    ))
    assert accepted is True
    for _ in range(100):
        await asyncio.sleep(0.01)
        if not thread.is_alive():
            break
    assert result == [{"committed": True}]
    broker.unbind(writer)
