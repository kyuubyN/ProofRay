from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from typing import Iterator

from .memory_service import (
    DEFAULT_KEYWORDS, MAX_REPLY_TEXT_BYTES, ConversationMemoryService, MemoryReply,
    _fit_utf8_prefix, should_consult_memory,
)
from .provider_manager import ProviderManager
from .providers import ChatRequest, ChatTurn, MemoryContext, ProviderEvent
from .rewrite_guard import guard_rewrite


@dataclass(frozen=True)
class OrchestrationEvent:
    event: str
    payload: dict[str, object]


class ChatOrchestrator:
    def __init__(self, *, memory: ConversationMemoryService | None = None,
                 providers: ProviderManager | None = None):
        self.memory = memory or ConversationMemoryService()
        self.providers = providers or ProviderManager()

    @staticmethod
    def _digest(conversation_id: str, message_id: str, text: str) -> str:
        return hashlib.sha256(
            f"{conversation_id}\x00{message_id}\x00{text}".encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _memory_context(reply: MemoryReply) -> MemoryContext | None:
        if not reply.memory_consulted:
            return None
        return MemoryContext(
            reply.authority, reply.certified_text or reply.text,
            tuple(str(item["source_id"]) for item in reply.sources),
        )

    @staticmethod
    def _payload(reply: MemoryReply, query_digest: str) -> dict[str, object]:
        payload = reply.payload()
        payload["query_digest"] = query_digest
        return payload

    def _provider_events(self, provider_id: str, *, question: str,
                         turns: tuple[ChatTurn, ...], mode: str,
                         provider_secret: str | None = None,
                         memory: MemoryContext | None = None) \
            -> Iterator[ProviderEvent]:
        request_mode = mode if mode in ("tool", "keywords", "off") else "keywords"
        return self.providers.stream_chat(
            provider_id, ChatRequest(question, turns, request_mode, memory=memory),
            secret=provider_secret)

    @staticmethod
    def _tool_question(events: tuple[ProviderEvent, ...]) -> str | None:
        calls = [event for event in events if event.event == "tool.call"]
        if not calls:
            return None
        if len(calls) != 1:
            raise RuntimeError("provider_requested_multiple_tools")
        value = calls[0].payload.get("arguments")
        question = value.get("question") if isinstance(value, dict) else None
        if not isinstance(question, str) or not question.strip():
            raise RuntimeError("provider_tool_question_invalid")
        return question.strip()

    @staticmethod
    def _provider_text(events: tuple[ProviderEvent, ...]) -> str:
        errors = [event.payload.get("code") for event in events if event.event == "error"]
        if errors:
            raise RuntimeError(str(errors[-1]))
        completed = [event for event in events if event.event == "completed"]
        return str(completed[-1].payload.get("text", "")) if completed else ""

    def respond(self, *, conversation_id: str, message_id: str, text: str,
                mode: str, provider_id: str | None,
                sequence: int | None = None, timestamp: datetime | None = None,
                provider_secret: str | None = None,
                keywords: frozenset[str] | None = None,
                turns: tuple[ChatTurn, ...] = ()) -> Iterator[OrchestrationEvent]:
        digest = self._digest(conversation_id, message_id, text)
        # The user's observation crosses the durable host boundary before any
        # optional network provider is invoked. Queries below exclude this
        # exact source, preserving the no-self-answer invariant on retries.
        self.memory.remember_user_message(
            conversation_id=conversation_id, message_id=message_id, text=text,
            sequence=sequence, timestamp=timestamp)
        tool_question = None
        first_provider_events: tuple[ProviderEvent, ...] = ()
        if mode == "tool" and provider_id is not None:
            first_provider_events = tuple(self._provider_events(
                provider_id, question=text, turns=turns, mode="tool",
                provider_secret=provider_secret))
            tool_question = self._tool_question(first_provider_events)

        consulted = should_consult_memory(
            mode, text, tool_requested=tool_question is not None,
            keywords=DEFAULT_KEYWORDS if keywords is None else keywords)
        if consulted:
            yield OrchestrationEvent("memory.started", {"query_digest": digest})
            yield OrchestrationEvent("routing", {})
            yield OrchestrationEvent("verifying", {})

        reply = self.memory.answer_prior(
            conversation_id, tool_question or text,
            exclude_source_id=f"conversation:{conversation_id}:{message_id}") if consulted else \
            MemoryReply("model", "", False)

        if reply.memory_consulted:
            terminal = {
                "proved": "proof.closed", "evidence": "evidence",
                "contested": "contested", "abstention": "abstained",
            }[reply.authority]
            yield OrchestrationEvent(terminal, self._payload(reply, digest))

        if provider_id is None or reply.authority in ("abstention", "contested"):
            yield OrchestrationEvent("completed", self._payload(reply, digest))
            return

        if mode == "tool" and tool_question is None:
            model_text, model_truncated = _fit_utf8_prefix(
                self._provider_text(first_provider_events), MAX_REPLY_TEXT_BYTES)
            for event in first_provider_events:
                if event.event == "model.delta":
                    yield OrchestrationEvent(event.event, event.payload)
            payload = self._payload(MemoryReply("model", model_text, False), digest)
            payload["answer_bytes"] = len(model_text.encode("utf-8"))
            payload["text_truncated"] = model_truncated
            yield OrchestrationEvent("completed", payload)
            return

        final_events = self._provider_events(
            provider_id, question=text, turns=turns, mode="off",
            provider_secret=provider_secret, memory=self._memory_context(reply))
        collected = []
        for event in final_events:
            collected.append(event)
            if event.event == "model.delta":
                yield OrchestrationEvent(event.event, event.payload)
        frozen_final = tuple(collected)
        model_text, model_truncated = _fit_utf8_prefix(
            self._provider_text(frozen_final), MAX_REPLY_TEXT_BYTES)
        displayed = reply.text
        if reply.authority == "proved" and guard_rewrite(reply.text, model_text).accepted:
            displayed = model_text
        elif reply.authority == "evidence":
            displayed = model_text or reply.text
        elif reply.authority == "model":
            displayed = model_text
        payload = self._payload(reply, digest)
        payload["text"] = displayed
        payload["rewrite_displayed"] = displayed != reply.text
        payload["answer_bytes"] = len(displayed.encode("utf-8"))
        payload["text_truncated"] = reply.text_truncated or model_truncated
        yield OrchestrationEvent("completed", payload)


__all__ = ["ChatOrchestrator", "OrchestrationEvent"]
