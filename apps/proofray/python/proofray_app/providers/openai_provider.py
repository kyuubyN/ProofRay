from __future__ import annotations

import json
from typing import Iterator

from .base import (
    SYSTEM_PROMPT, ChatRequest, ModelDescriptor, ProviderCapabilities, ProviderConfig,
    ProviderEvent, ProviderKind, memory_instruction, proofray_tool_schema,
)
from .transport import (
    ProviderTransport, UrllibProviderTransport, json_body, json_response, sse_payloads,
)


_NON_CHAT_MODEL_MARKERS = (
    "audio", "codex", "computer-use", "dall-e", "deep-research",
    "embedding", "gpt-image", "moderation", "realtime", "search-preview",
    "sora", "transcribe", "tts", "whisper",
)


def _official_chat_model(model_id: str) -> bool:
    """Exclude OpenAI catalog entries that cannot use this text chat path.

    OpenAI-compatible endpoints intentionally remain unfiltered because local
    servers use arbitrary model identifiers. The official Models endpoint only
    exposes identity/ownership, so this is a fail-closed exclusion of explicit
    non-chat product families, not a claim of tool capability.
    """
    normalized = model_id.casefold()
    return not any(marker in normalized for marker in _NON_CHAT_MODEL_MARKERS)


class OpenAIProvider:
    capabilities = ProviderCapabilities(True, True, True)

    def __init__(self, config: ProviderConfig,
                 *, transport: ProviderTransport | None = None):
        if config.kind not in (ProviderKind.OPENAI, ProviderKind.OPENAI_COMPATIBLE):
            raise ValueError("OpenAI provider requires an OpenAI-shaped configuration")
        self.config = config
        self.transport = transport or UrllibProviderTransport()

    @property
    def base_url(self) -> str:
        return self.config.endpoint.rstrip("/")

    def _headers(self) -> dict[str, str]:
        result = {"Content-Type": "application/json"}
        if self.config.secret:
            result["Authorization"] = f"Bearer {self.config.secret}"
        return result

    def list_models(self) -> tuple[ModelDescriptor, ...]:
        value = json_response(self.transport.request(
            "GET", f"{self.base_url}/models", self._headers(), None, 15))
        if not isinstance(value, dict) or not isinstance(value.get("data"), list):
            raise RuntimeError("provider_model_schema")
        result = []
        supports_tools = self.config.tool_calling_override is not False
        for item in value["data"]:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            model_id = str(item["id"])
            if (self.config.kind == ProviderKind.OPENAI
                    and not _official_chat_model(model_id)):
                continue
            result.append(ModelDescriptor(model_id, model_id, supports_tools))
        return tuple(sorted(result, key=lambda item: item.model_id))

    def test_connection(self) -> None:
        models = self.list_models()
        if not models:
            raise RuntimeError("provider_has_no_models")
        if not self.config.custom_model and self.config.model_id not in {
                item.model_id for item in models}:
            raise RuntimeError("configured_model_unavailable")

    def stream_chat(self, request: ChatRequest) -> Iterator[ProviderEvent]:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        for turn in request.turns:
            messages.append({"role": turn.role, "content": turn.text})
        memory = memory_instruction(request.memory)
        if memory:
            messages.append({"role": "system", "content": memory})
        messages.append({"role": "user", "content": request.question})
        payload: dict[str, object] = {
            "model": self.config.model_id,
            "messages": messages,
            "stream": True,
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
        }
        tools_enabled = request.memory_mode == "tool" and \
            self.config.tool_calling_override is not False
        if tools_enabled:
            payload["tools"] = [{"type": "function", "function": proofray_tool_schema()}]
            payload["tool_choice"] = "auto"
        text_parts = []
        calls: dict[int, dict[str, str]] = {}
        try:
            stream = self.transport.stream(
                "POST", f"{self.base_url}/chat/completions", self._headers(),
                json_body(payload), 90)
            for value in sse_payloads(stream):
                if not isinstance(value, dict):
                    raise RuntimeError("provider_stream_schema")
                choices = value.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                delta = choices[0].get("delta", {})
                if not isinstance(delta, dict):
                    continue
                content = delta.get("content")
                if isinstance(content, str) and content:
                    text_parts.append(content)
                    yield ProviderEvent("model.delta", {"text": content})
                tool_calls = delta.get("tool_calls", ())
                if isinstance(tool_calls, list):
                    for item in tool_calls:
                        if not isinstance(item, dict):
                            continue
                        index = int(item.get("index", 0))
                        target = calls.setdefault(index, {"name": "", "arguments": ""})
                        function = item.get("function", {})
                        if isinstance(function, dict):
                            if function.get("name"):
                                target["name"] = str(function["name"])
                            if function.get("arguments"):
                                target["arguments"] += str(function["arguments"])
            for index in sorted(calls):
                call = calls[index]
                try:
                    arguments = json.loads(call["arguments"] or "{}")
                except json.JSONDecodeError:
                    raise RuntimeError("provider_tool_arguments") from None
                if call["name"] != "proofray_recall" or not isinstance(arguments, dict):
                    raise RuntimeError("provider_unknown_tool")
                yield ProviderEvent("tool.call", {
                    "name": call["name"], "arguments": arguments,
                })
            yield ProviderEvent("completed", {"text": "".join(text_parts)})
        except RuntimeError as error:
            yield ProviderEvent("error", {"code": str(error)})

    def cancel(self) -> None:
        cancel = getattr(self.transport, "cancel", None)
        if callable(cancel):
            cancel()


__all__ = ["OpenAIProvider", "_official_chat_model"]
