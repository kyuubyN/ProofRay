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


class AnthropicProvider:
    capabilities = ProviderCapabilities(True, True, True)

    def __init__(self, config: ProviderConfig,
                 *, transport: ProviderTransport | None = None):
        if config.kind != ProviderKind.ANTHROPIC:
            raise ValueError("Anthropic provider requires Anthropic configuration")
        self.config = config
        self.transport = transport or UrllibProviderTransport()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if self.config.secret:
            headers["x-api-key"] = self.config.secret
        return headers

    @property
    def base_url(self) -> str:
        return self.config.endpoint.rstrip("/")

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
            result.append(ModelDescriptor(
                model_id, str(item.get("display_name") or model_id), supports_tools))
        return tuple(result)

    def test_connection(self) -> None:
        models = self.list_models()
        if not models:
            raise RuntimeError("provider_has_no_models")
        if not self.config.custom_model and self.config.model_id not in {
                item.model_id for item in models}:
            raise RuntimeError("configured_model_unavailable")

    def stream_chat(self, request: ChatRequest) -> Iterator[ProviderEvent]:
        messages = [{"role": turn.role, "content": turn.text} for turn in request.turns]
        memory = memory_instruction(request.memory)
        if memory:
            messages.append({"role": "user", "content": memory})
            messages.append({"role": "assistant", "content": "Memory state received."})
        messages.append({"role": "user", "content": request.question})
        payload: dict[str, object] = {
            "model": self.config.model_id,
            "system": SYSTEM_PROMPT,
            "messages": messages,
            "stream": True,
            "temperature": request.temperature,
            "max_tokens": request.max_output_tokens,
        }
        if (request.memory_mode == "tool"
                and self.config.tool_calling_override is not False):
            tool = proofray_tool_schema()
            payload["tools"] = [{
                "name": tool["name"], "description": tool["description"],
                "input_schema": tool["parameters"],
            }]
        text_parts = []
        tool_name = None
        tool_json = []
        try:
            stream = self.transport.stream(
                "POST", f"{self.base_url}/messages", self._headers(),
                json_body(payload), 90)
            for value in sse_payloads(stream):
                if not isinstance(value, dict):
                    continue
                event_type = value.get("type")
                block = value.get("content_block")
                delta = value.get("delta")
                if event_type == "content_block_start" and isinstance(block, dict) \
                        and block.get("type") == "tool_use":
                    tool_name = block.get("name")
                    initial = block.get("input")
                    if isinstance(initial, dict) and initial:
                        tool_json.append(json.dumps(initial))
                if event_type == "content_block_delta" and isinstance(delta, dict):
                    if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
                        text = delta["text"]
                        text_parts.append(text)
                        yield ProviderEvent("model.delta", {"text": text})
                    if delta.get("type") == "input_json_delta":
                        tool_json.append(str(delta.get("partial_json", "")))
            if tool_name is not None:
                try:
                    arguments = json.loads("".join(tool_json) or "{}")
                except json.JSONDecodeError:
                    raise RuntimeError("provider_tool_arguments") from None
                if tool_name != "proofray_recall" or not isinstance(arguments, dict):
                    raise RuntimeError("provider_unknown_tool")
                yield ProviderEvent("tool.call", {"name": tool_name, "arguments": arguments})
            yield ProviderEvent("completed", {"text": "".join(text_parts)})
        except RuntimeError as error:
            yield ProviderEvent("error", {"code": str(error)})

    def cancel(self) -> None:
        cancel = getattr(self.transport, "cancel", None)
        if callable(cancel):
            cancel()


__all__ = ["AnthropicProvider"]
