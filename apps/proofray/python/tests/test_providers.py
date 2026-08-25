import json

import pytest

from proofray_app.providers import (
    ChatRequest, ChatTurn, ProviderConfig, ProviderKind, create_provider,
)
from proofray_app.providers.anthropic_provider import AnthropicProvider
from proofray_app.providers.gemini_provider import GeminiProvider
from proofray_app.providers.openai_provider import OpenAIProvider, _official_chat_model
from proofray_app.providers.transport import HttpResponse
from proofray_app.providers.transport import UrllibProviderTransport
from proofray_app.provider_manager import ProviderManager


class _Transport:
    def __init__(self, response, lines=()):
        self.response = response
        self.lines = tuple(lines)
        self.requests = []
        self.cancelled = False

    def request(self, method, url, headers, body, timeout):
        self.requests.append((method, url, dict(headers), body, timeout))
        return self.response

    def stream(self, method, url, headers, body, timeout):
        self.requests.append((method, url, dict(headers), body, timeout))
        yield from self.lines

    def cancel(self):
        self.cancelled = True


class _ClosableResponse:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_transport_cancel_closes_the_active_stream_response():
    transport = UrllibProviderTransport()
    response = _ClosableResponse()
    transport._active_response = response
    transport.cancel()
    assert response.closed is True


def _request(mode="off"):
    return ChatRequest(
        "Olá", (ChatTurn("user", "Contexto anterior."),), mode,
        max_output_tokens=64)


def test_secret_is_ephemeral_and_never_in_provider_repr():
    config = ProviderConfig(
        "g", ProviderKind.GEMINI, "gemini-stable",
        "https://generativelanguage.googleapis.com/v1beta", secret="super-secret")
    assert "super-secret" not in repr(config)
    with pytest.raises(ValueError):
        ProviderConfig(
            "x", ProviderKind.OPENAI, "gpt-stable",
            "https://user:secret@example.test/v1")
    with pytest.raises(ValueError, match="TLS"):
        ProviderConfig(
            "x", ProviderKind.OPENAI_COMPATIBLE, "model",
            "http://models.example.test/v1")
    with pytest.raises(ValueError, match="query credentials"):
        ProviderConfig(
            "x", ProviderKind.OPENAI, "model",
            "https://api.example.test/v1?token=secret")
    with pytest.raises(ValueError, match="query or fragment"):
        ProviderConfig(
            "x", ProviderKind.OPENAI, "model",
            "https://api.example.test/v1?pretty=true")
    ProviderConfig(
        "local", ProviderKind.OPENAI_COMPATIBLE, "model",
        "http://127.0.0.1:11434/v1")
    with pytest.raises(ValueError):
        ProviderConfig(
            "provider:ambiguous", ProviderKind.OPENAI, "model",
            "https://api.example.test/v1")


def test_provider_manager_never_retains_a_secret_lease():
    manager = ProviderManager()
    manager.configure(ProviderConfig(
        "g", ProviderKind.GEMINI, "gemini-stable",
        "https://generativelanguage.googleapis.com/v1beta", secret="lease-only"))
    assert manager._configs["g"].secret is None
    assert "lease-only" not in repr(manager._configs)


def test_openai_model_discovery_and_streamed_text():
    transport = _Transport(
        HttpResponse(200, {}, b'{"data":[{"id":"gpt-stable"}]}'),
        [b'data: {"choices":[{"delta":{"content":"Ola"}}]}', b"data: [DONE]"],
    )
    config = ProviderConfig(
        "o", ProviderKind.OPENAI, "gpt-stable", "https://api.openai.com/v1",
        secret="key")
    provider = OpenAIProvider(config, transport=transport)
    assert [item.model_id for item in provider.list_models()] == ["gpt-stable"]
    events = tuple(provider.stream_chat(_request()))
    assert [(event.event, event.payload) for event in events] == [
        ("model.delta", {"text": "Ola"}),
        ("completed", {"text": "Ola"}),
    ]
    assert all("key" not in (call[3] or b"").decode() for call in transport.requests)


def test_official_openai_discovery_excludes_non_chat_catalog_products():
    catalog = (
        "gpt-5.2", "o4-mini", "text-embedding-3-small", "gpt-image-1",
        "gpt-realtime", "sora-2", "omni-moderation-latest",
    )
    transport = _Transport(HttpResponse(200, {}, json.dumps({
        "data": [{"id": model_id} for model_id in catalog],
    }).encode()))
    provider = OpenAIProvider(ProviderConfig(
        "o", ProviderKind.OPENAI, "gpt-5.2", "https://api.openai.com/v1"),
        transport=transport)
    assert [item.model_id for item in provider.list_models()] == ["gpt-5.2", "o4-mini"]
    assert _official_chat_model("ft:gpt-5.2:owner:name") is True

    # Local OpenAI-compatible servers use arbitrary IDs and remain discoverable.
    compatible = OpenAIProvider(ProviderConfig(
        "local", ProviderKind.OPENAI_COMPATIBLE, "text-embedding-named-chat",
        "http://127.0.0.1:11434/v1", custom_model=True), transport=transport)
    assert len(compatible.list_models()) == len(catalog)


def test_openai_tool_call_is_closed_to_proofray_recall():
    chunks = [
        b'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":'
        b'{"name":"proofray_recall","arguments":"{\\"question\\":\\"viagem\\"}"}}]}}]}',
        b"data: [DONE]",
    ]
    transport = _Transport(HttpResponse(200, {}, b"{}"), chunks)
    provider = OpenAIProvider(ProviderConfig(
        "o", ProviderKind.OPENAI, "gpt-stable", "https://api.openai.com/v1"),
        transport=transport)
    events = tuple(provider.stream_chat(_request("tool")))
    assert events[-2].event == "tool.call"
    assert events[-2].payload == {
        "name": "proofray_recall", "arguments": {"question": "viagem"}}


def test_model_without_function_calling_never_receives_tool_schema():
    transport = _Transport(
        HttpResponse(200, {}, b"{}"),
        [b'data: {"choices":[{"delta":{"content":"Oi"}}]}', b"data: [DONE]"],
    )
    provider = OpenAIProvider(ProviderConfig(
        "local", ProviderKind.OPENAI_COMPATIBLE, "plain-model",
        "http://127.0.0.1:11434/v1", tool_calling_override=False),
        transport=transport)
    assert tuple(provider.stream_chat(_request("tool")))[-1].payload == {"text": "Oi"}
    payload = json.loads(transport.requests[-1][3])
    assert "tools" not in payload and "tool_choice" not in payload


@pytest.mark.parametrize(("provider_class", "kind", "endpoint", "response", "lines"), [
    (
        GeminiProvider,
        ProviderKind.GEMINI,
        "https://generativelanguage.googleapis.com/v1beta",
        {"models": [{"name": "models/plain-model"}]},
        [b'data: {"candidates":[{"content":{"parts":[{"text":"Oi"}]}}]}'],
    ),
    (
        AnthropicProvider,
        ProviderKind.ANTHROPIC,
        "https://api.anthropic.com/v1",
        {"data": [{"id": "plain-model"}]},
        [b'data: {"type":"content_block_delta","delta":{"type":"text_delta",'
         b'"text":"Oi"}}'],
    ),
])
def test_non_tool_override_is_enforced_for_every_provider(
        provider_class, kind, endpoint, response, lines):
    transport = _Transport(
        HttpResponse(200, {}, json.dumps(response).encode()), lines)
    provider = provider_class(ProviderConfig(
        "plain", kind, "plain-model", endpoint,
        tool_calling_override=False), transport=transport)
    assert provider.list_models()[0].supports_tools is False
    tuple(provider.stream_chat(_request("tool")))
    payload = json.loads(transport.requests[-1][3])
    assert "tools" not in payload


def test_gemini_discovery_and_stream_shape():
    transport = _Transport(
        HttpResponse(200, {}, json.dumps({"models": [{
            "name": "models/gemini-stable", "displayName": "Gemini stable",
            "supportedGenerationMethods": ["generateContent"], "inputTokenLimit": 1000,
        }]}).encode()),
        [b'data: {"candidates":[{"content":{"parts":[{"text":"Oi"}]}}]}'],
    )
    provider = GeminiProvider(ProviderConfig(
        "g", ProviderKind.GEMINI, "gemini-stable",
        "https://generativelanguage.googleapis.com/v1beta"), transport=transport)
    assert provider.list_models()[0].context_tokens == 1000
    assert tuple(provider.stream_chat(_request()))[-1].payload == {"text": "Oi"}


def test_anthropic_discovery_and_stream_shape():
    transport = _Transport(
        HttpResponse(200, {}, b'{"data":[{"id":"claude-stable","display_name":"Claude"}]}'),
        [b'data: {"type":"content_block_delta","delta":{"type":"text_delta","text":"Oi"}}'],
    )
    provider = AnthropicProvider(ProviderConfig(
        "a", ProviderKind.ANTHROPIC, "claude-stable",
        "https://api.anthropic.com/v1"), transport=transport)
    assert provider.list_models()[0].display_name == "Claude"
    assert tuple(provider.stream_chat(_request()))[-1].payload == {"text": "Oi"}


def test_registry_keeps_optional_provider_imports_lazy():
    assert isinstance(create_provider(ProviderConfig(
        "c", ProviderKind.OPENAI_COMPATIBLE, "custom-model",
        "http://127.0.0.1:1234/v1", custom_model=True)), OpenAIProvider)


def test_context_boundary_is_physical_utf8_bytes():
    with pytest.raises(ValueError, match="16 KiB"):
        ChatRequest("q", (ChatTurn("user", "á" * 9000),), "off")
