from __future__ import annotations

from typing import Iterator

from .base import (
    SYSTEM_PROMPT, ChatRequest, ModelDescriptor, ProviderCapabilities, ProviderConfig,
    ProviderEvent, ProviderKind, memory_instruction, proofray_tool_schema,
)
from .transport import (
    ProviderTransport, UrllibProviderTransport, json_body, json_response, sse_payloads,
)


class GeminiProvider:
    capabilities = ProviderCapabilities(True, True, True)

    def __init__(self, config: ProviderConfig,
                 *, transport: ProviderTransport | None = None):
        if config.kind != ProviderKind.GEMINI:
            raise ValueError("Gemini provider requires Gemini configuration")
        self.config = config
        self.transport = transport or UrllibProviderTransport()

    @property
    def base_url(self) -> str:
        return self.config.endpoint.rstrip("/")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.secret:
            headers["x-goog-api-key"] = self.config.secret
        return headers

    def list_models(self) -> tuple[ModelDescriptor, ...]:
        value = json_response(self.transport.request(
            "GET", f"{self.base_url}/models", self._headers(), None, 15))
        if not isinstance(value, dict) or not isinstance(value.get("models"), list):
            raise RuntimeError("provider_model_schema")
        result = []
        supports_tools = self.config.tool_calling_override is not False
        for item in value["models"]:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            methods = item.get("supportedGenerationMethods", ())
            if methods and "generateContent" not in methods:
                continue
            model_id = str(item["name"]).removeprefix("models/")
            context = item.get("inputTokenLimit")
            result.append(ModelDescriptor(
                model_id, str(item.get("displayName") or model_id), supports_tools,
                int(context) if isinstance(context, int) else None,
            ))
        return tuple(result)

    def test_connection(self) -> None:
        models = self.list_models()
        if not models:
            raise RuntimeError("provider_has_no_models")
        if not self.config.custom_model and self.config.model_id not in {
                item.model_id for item in models}:
            raise RuntimeError("configured_model_unavailable")

    def stream_chat(self, request: ChatRequest) -> Iterator[ProviderEvent]:
        contents = [{
            "role": "model" if turn.role == "assistant" else "user",
            "parts": [{"text": turn.text}],
        } for turn in request.turns]
        memory = memory_instruction(request.memory)
        if memory:
            contents.append({"role": "user", "parts": [{"text": memory}]})
            contents.append({
                "role": "model", "parts": [{"text": "Memory state received."}],
            })
        contents.append({"role": "user", "parts": [{"text": request.question}]})
        payload: dict[str, object] = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": contents,
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_output_tokens,
            },
        }
        if (request.memory_mode == "tool"
                and self.config.tool_calling_override is not False):
            payload["tools"] = [{"functionDeclarations": [proofray_tool_schema()]}]
        text_parts = []
        try:
            stream = self.transport.stream(
                "POST",
                f"{self.base_url}/models/{self.config.model_id}:streamGenerateContent?alt=sse",
                self._headers(), json_body(payload), 90)
            for value in sse_payloads(stream):
                if not isinstance(value, dict):
                    continue
                candidates = value.get("candidates")
                if not isinstance(candidates, list) or not candidates:
                    continue
                parts = candidates[0].get("content", {}).get("parts", ())
                for part in parts if isinstance(parts, list) else ():
                    if not isinstance(part, dict):
                        continue
                    text = part.get("text")
                    if isinstance(text, str) and text:
                        text_parts.append(text)
                        yield ProviderEvent("model.delta", {"text": text})
                    call = part.get("functionCall")
                    if isinstance(call, dict):
                        name = call.get("name")
                        arguments = call.get("args", {})
                        if name != "proofray_recall" or not isinstance(arguments, dict):
                            raise RuntimeError("provider_unknown_tool")
                        yield ProviderEvent("tool.call", {
                            "name": name, "arguments": arguments,
                        })
            yield ProviderEvent("completed", {"text": "".join(text_parts)})
        except RuntimeError as error:
            yield ProviderEvent("error", {"code": str(error)})

    def cancel(self) -> None:
        cancel = getattr(self.transport, "cancel", None)
        if callable(cancel):
            cancel()


__all__ = ["GeminiProvider"]
