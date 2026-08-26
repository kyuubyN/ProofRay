from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import ipaddress
import re
from typing import Iterator, Protocol
from urllib.parse import parse_qsl, urlparse


RECENT_CONTEXT_BYTES = 16 * 1024
MAX_PROVIDER_QUESTION_BYTES = 64 * 1024
_APP_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")


class ProviderKind(str, Enum):
    GEMINI = "gemini"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    OPENAI_COMPATIBLE = "openai_compatible"


@dataclass(frozen=True)
class ProviderCapabilities:
    model_discovery: bool
    streaming: bool
    tool_calling: bool


@dataclass(frozen=True)
class ProviderConfig:
    provider_id: str
    kind: ProviderKind
    model_id: str
    endpoint: str
    secret: str | None = field(default=None, repr=False, compare=False)
    custom_model: bool = False
    tool_calling_override: bool | None = None

    def __post_init__(self) -> None:
        parsed = urlparse(self.endpoint)
        if (not _APP_IDENTIFIER.fullmatch(self.provider_id)
                or not self.model_id or parsed.scheme not in ("http", "https")
                or not parsed.netloc or parsed.username or parsed.password):
            raise ValueError("invalid provider configuration")
        if parsed.scheme == "http" and not _loopback(parsed.hostname):
            raise ValueError("remote provider endpoints require TLS")
        if any(tag in self.model_id.casefold()
               for tag in ("latest", "preview", "experimental", "-exp")) \
                and not self.custom_model:
            raise ValueError("provider model must be pinned or explicitly custom")
        if any(re.search(
                r"token|secret|password|passphrase|api.?key|credential|access.?key",
                key, re.IGNORECASE) for key, _value in parse_qsl(parsed.query)):
            raise ValueError("provider query credentials must use an ephemeral secret lease")
        if parsed.query or parsed.fragment:
            raise ValueError("provider endpoint cannot contain query or fragment components")


def _loopback(host: str | None) -> bool:
    if host is None:
        return False
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class ModelDescriptor:
    model_id: str
    display_name: str
    supports_tools: bool
    context_tokens: int | None = None


@dataclass(frozen=True)
class ChatTurn:
    role: str
    text: str

    def __post_init__(self) -> None:
        if self.role not in ("user", "assistant") or not self.text.strip():
            raise ValueError("chat turn must have a supported role and text")


@dataclass(frozen=True)
class MemoryContext:
    authority: str
    deterministic_text: str
    source_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.authority not in ("proved", "evidence", "abstention", "contested"):
            raise ValueError("invalid memory authority")
        if self.authority == "proved" and (not self.deterministic_text or not self.source_ids):
            raise ValueError("proved memory requires text and sources")
        if self.authority in ("abstention", "contested") and self.deterministic_text:
            raise ValueError("non-answer memory state cannot carry answer text")


@dataclass(frozen=True)
class ChatRequest:
    question: str
    turns: tuple[ChatTurn, ...]
    memory_mode: str
    memory: MemoryContext | None = None
    max_output_tokens: int = 1024
    temperature: float = 0.2

    def __post_init__(self) -> None:
        if (not self.question.strip()
                or len(self.question.encode("utf-8")) > MAX_PROVIDER_QUESTION_BYTES
                or self.memory_mode not in ("tool", "keywords", "off")
                or not 1 <= self.max_output_tokens <= 8192
                or not 0 <= self.temperature <= 2):
            raise ValueError("invalid chat request")
        if sum(len(turn.text.encode("utf-8")) for turn in self.turns) > RECENT_CONTEXT_BYTES:
            raise ValueError("recent chat context exceeds the 16 KiB authority boundary")


@dataclass(frozen=True)
class ProviderEvent:
    event: str
    payload: dict[str, object]

    def __post_init__(self) -> None:
        if self.event not in ("model.delta", "tool.call", "completed", "error"):
            raise ValueError("invalid provider event")


class Provider(Protocol):
    config: ProviderConfig
    capabilities: ProviderCapabilities

    def list_models(self) -> tuple[ModelDescriptor, ...]: ...
    def test_connection(self) -> None: ...
    def stream_chat(self, request: ChatRequest) -> Iterator[ProviderEvent]: ...
    def cancel(self) -> None: ...


SYSTEM_PROMPT = """You are the conversational layer of ProofRay.
General knowledge answers are allowed but are not memory-verified. When a user asks about their
past, preferences, documents or prior conversations and the proofray_recall tool is available,
call it. Never invent a personal memory. A PROVED memory result may be rewritten without changing
facts. EVIDENCE is relevant source text but not a complete proved answer. ABSTENTION and CONTESTED
must be reported honestly. Never claim that model text itself is certified."""


def proofray_tool_schema() -> dict[str, object]:
    return {
        "name": "proofray_recall",
        "description": "Consult local proof-carrying memory for a personal or prior-context fact.",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
            "additionalProperties": False,
        },
    }


def bounded_turns(turns: tuple[ChatTurn, ...], *, byte_limit: int = RECENT_CONTEXT_BYTES) \
        -> tuple[ChatTurn, ...]:
    if byte_limit < 1:
        raise ValueError("context byte limit must be positive")
    selected = []
    used = 0
    for turn in reversed(turns):
        size = len(turn.text.encode("utf-8"))
        if used + size > byte_limit:
            break
        selected.append(turn)
        used += size
    return tuple(reversed(selected))


def memory_instruction(memory: MemoryContext | None) -> str | None:
    if memory is None:
        return None
    if memory.authority == "proved":
        return (
            "PROOFRAY_PROVED: the text below is a certified answer to the "
            "user's question. Answer using it; you may rephrase it, but "
            "never change or add facts.\n" + memory.deterministic_text
        )
    if memory.authority == "evidence":
        return (
            "PROOFRAY_EVIDENCE: the text below is a verified excerpt from "
            "the user's own memory, relevant to their question but not a "
            "complete pre-composed answer. If it answers the question, use "
            "it directly in your reply. Do not ignore it and do not "
            "silently fall back to general knowledge when it applies.\n"
            + memory.deterministic_text
        )
    if memory.authority == "contested":
        return (
            "PROOFRAY_CONTESTED: memory holds multiple conflicting records "
            "for this question. Tell the user the memories conflict; do not "
            "pick one and present it as certain."
        )
    return (
        "PROOFRAY_ABSTAINED: no verified memory was found for this "
        "question. Say so plainly; do not answer from general knowledge or "
        "invent a personal fact."
    )


__all__ = [
    "RECENT_CONTEXT_BYTES", "SYSTEM_PROMPT", "ChatRequest", "ChatTurn",
    "MemoryContext", "ModelDescriptor", "Provider", "ProviderCapabilities",
    "ProviderConfig", "ProviderEvent", "ProviderKind", "bounded_turns",
    "memory_instruction", "proofray_tool_schema",
]
