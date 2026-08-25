from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any

from proofray import (
    CONVERSATIONAL_HIGH_RECALL_PROFILE,
    ConversationalRecallGenerator,
    OpenTextProofRayMemory,
    RouteDocument,
)
from .connectors import MappedDocument


DEFAULT_KEYWORDS = frozenset({
    "remember", "recall", "do you remember", "what did", "when did",
    "lembra", "lembrar", "se lembra", "você lembra", "recorda",
})
MAX_CHAT_TEXT_BYTES = 64 * 1024
MAX_IMPORTED_TEXT_BYTES = 128 * 1024
MAX_INLINE_SOURCE_BYTES = 384 * 1024
MAX_PUBLISHED_SOURCES = 64
MAX_CERTIFICATE_BYTES = 128 * 1024
MAX_REPLY_TEXT_BYTES = 24_576
_VALID_MODES = frozenset({"tool", "keywords", "forceNext", "off"})
_APP_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")
_QUESTION_PREFIXES = (
    "who ", "what ", "when ", "where ", "why ", "how ", "which ",
    "do you ", "did you ", "can you ", "could you ", "would you ",
    "quem ", "o que ", "quando ", "onde ", "por que ", "porque ", "como ",
    "qual ", "quais ", "você lembra ", "voce lembra ", "lembra ",
    "remember ", "please ", "tell ", "show ", "find ",
    "lembre ", "por favor ", "diga ", "mostre ", "encontre ", "pesquise ",
)


def stable_fact_id(source_id: str) -> int:
    if not isinstance(source_id, str) or not source_id:
        raise ValueError("source identity is required")
    return int.from_bytes(hashlib.sha256(source_id.encode("utf-8")).digest()[:8], "big") \
        & ((1 << 62) - 1)


def should_consult_memory(mode: str, text: str, *, tool_requested: bool = False,
                          keywords: frozenset[str] = DEFAULT_KEYWORDS) -> bool:
    if mode not in _VALID_MODES or not isinstance(text, str):
        raise ValueError("invalid memory activation request")
    if mode == "off":
        return False
    if mode == "forceNext":
        return True
    if mode == "tool":
        return bool(tool_requested)
    folded = text.casefold()
    return any(re.search(
        rf"(?<!\w){re.escape(keyword.casefold().strip())}(?!\w)",
        folded,
    ) is not None for keyword in keywords if keyword.strip())


def is_authoritative_observation(text: str) -> bool:
    """Admit only plainly declarative user turns as source observations.

    Questions and requests remain in encrypted chat history, but cannot become
    evidence for later answers merely because the user uttered them.
    """
    if not isinstance(text, str):
        raise TypeError("observation text must be str")
    stripped = text.strip()
    if not stripped or "?" in stripped:
        return False
    folded = stripped.casefold()
    return not any(folded.startswith(prefix) for prefix in _QUESTION_PREFIXES)


def _event_day(timestamp: datetime, timezone_name: str) -> int:
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        timezone = ZoneInfo("UTC")
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=ZoneInfo("UTC"))
    return timestamp.astimezone(timezone).date().toordinal()


@dataclass(frozen=True)
class MemoryReply:
    authority: str
    text: str
    memory_consulted: bool
    certified_text: str | None = None
    certificate_hex: str | None = None
    proof_method: str | None = None
    sources: tuple[dict[str, object], ...] = ()
    documents_considered: int = 0
    verified_candidates: int = 0
    answer_bytes: int = 0
    text_truncated: bool = False

    def payload(self) -> dict[str, object]:
        remaining = MAX_INLINE_SOURCE_BYTES
        sources = []
        for source in self.sources:
            published = dict(source)
            text = published.get("text")
            size = len(json.dumps(
                text, ensure_ascii=False, separators=(",", ":"),
            ).encode("utf-8")) if isinstance(text, str) else 0
            if size > remaining:
                published["text"] = ""
                published["text_deferred"] = True
            else:
                published["text_deferred"] = False
                remaining -= size
            sources.append(published)
        return {
            "authority": self.authority,
            "text": self.text,
            "memory_consulted": self.memory_consulted,
            "certified_text": self.certified_text,
            "certificate_hex": self.certificate_hex,
            "proof_method": self.proof_method,
            "sources": sources,
            "documents_considered": self.documents_considered,
            "verified_candidates": self.verified_candidates,
            "answer_bytes": self.answer_bytes,
            "text_truncated": self.text_truncated,
        }


class ConversationMemoryService:
    """Local, source-authorized memory per conversation.

    A declarative user turn crosses the durable host boundary before optional
    provider work, while the current source is excluded from its own query.
    Questions remain history-only and cannot become evidence.
    """

    def __init__(self, *, scope_id: int = 1, profile_name: str = "User",
                 timezone_name: str = "UTC", record_store: Any | None = None):
        self.scope_id = scope_id
        self.profile_name = profile_name
        self.timezone_name = timezone_name
        self._record_store = record_store
        self._field: OpenTextProofRayMemory | None = None
        self._sequences: dict[str, int] = {}

    def update_profile(self, *, profile_name: str, timezone_name: str) -> None:
        if not isinstance(profile_name, str) or not profile_name.strip():
            raise ValueError("profile name is required")
        try:
            ZoneInfo(timezone_name)
        except (TypeError, ZoneInfoNotFoundError):
            raise ValueError("profile timezone must be a known IANA timezone") from None
        self.profile_name = profile_name.strip()
        self.timezone_name = timezone_name

    def warm(self) -> dict[str, object]:
        memory = self._memory("startup")
        documents = memory.documents_snapshot()
        return {
            "warmed": True,
            "documents": len(documents),
        }

    def _memory(self, conversation_id: str) -> OpenTextProofRayMemory:
        if not isinstance(conversation_id, str) or not _APP_IDENTIFIER.fullmatch(
                conversation_id):
            raise ValueError("conversation identity is required")
        if self._field is None:
            self._field = OpenTextProofRayMemory(
                scope_id=self.scope_id,
                session_id="proofray-personal-field",
                profile=CONVERSATIONAL_HIGH_RECALL_PROFILE,
                candidate_generator=ConversationalRecallGenerator(),
                record_store=self._record_store,
            )
            for document in self._field.documents_snapshot():
                if document.sequence is not None:
                    self._sequences[document.session_id] = max(
                        self._sequences.get(document.session_id, 0), document.sequence + 1)
        self._sequences.setdefault(conversation_id, 0)
        return self._field

    def _ingest_user_message(self, memory: OpenTextProofRayMemory, *, conversation_id: str,
                             message_id: str, text: str, timestamp: datetime,
                             sequence: int) -> None:
        if (not isinstance(message_id, str) or not _APP_IDENTIFIER.fullmatch(message_id)
                or not isinstance(text, str) or not text.strip()
                or len(text.encode("utf-8")) > MAX_CHAT_TEXT_BYTES):
            raise ValueError("authoritative chat observation exceeds its byte boundary")
        source_id = f"conversation:{conversation_id}:{message_id}"
        document = RouteDocument(
            stable_fact_id(source_id), text, self.scope_id, conversation_id, 1, source_id,
            sequence=sequence, event_time=_event_day(timestamp, self.timezone_name),
            role="user", speaker=self.profile_name,
        )
        receipt = memory.ingest_documents(
            (document,), bundle_id=f"message:{conversation_id}:{message_id}")
        if receipt.state not in ("APPLIED", "IDEMPOTENT"):
            raise RuntimeError("message_ingest_rejected")

    def answer_and_remember(self, *, conversation_id: str, message_id: str, text: str,
                            mode: str, tool_requested: bool = False,
                            timestamp: datetime | None = None) -> MemoryReply:
        if (not isinstance(message_id, str) or not _APP_IDENTIFIER.fullmatch(message_id)
                or not isinstance(text, str) or not text.strip()
                or len(text.encode("utf-8")) > MAX_CHAT_TEXT_BYTES):
            raise ValueError("message identity and text are required")
        consulted = should_consult_memory(
            mode, text, tool_requested=tool_requested)
        reply = self.answer_prior(
            conversation_id, text,
            exclude_source_id=f"conversation:{conversation_id}:{message_id}") if consulted else \
            MemoryReply("model", "", False)
        self.remember_user_message(
            conversation_id=conversation_id, message_id=message_id, text=text,
            timestamp=timestamp)
        return reply

    def answer_prior(self, conversation_id: str, question: str, *,
                     exclude_source_id: str | None = None) -> MemoryReply:
        if (not isinstance(question, str) or not question.strip()
                or len(question.encode("utf-8")) > MAX_CHAT_TEXT_BYTES):
            raise ValueError("memory question is required")
        memory = self._memory(conversation_id)
        result = memory.answer_excluding_sources(
            question, () if exclude_source_id is None else (exclude_source_id,))
        if result is None:
            return MemoryReply("abstention", "", True)
        metrics = {
            "documents_considered": result.documents_considered,
            "verified_candidates": result.verified_candidates,
            "answer_bytes": result.answer_bytes,
        }
        direct = result.direct_answer
        presentation_budget_exceeded = False
        if direct.state == "resolved":
            exact_by_source = {
                item.source_id: item for item in result.resolver_evidence}
            evidence_items = tuple(
                exact_by_source[source_id] for source_id in direct.source_ids)
            too_many_sources = len(evidence_items) > MAX_PUBLISHED_SOURCES
            certificate_too_large = len(direct.certificate) > MAX_CERTIFICATE_BYTES
            if too_many_sources or certificate_too_large:
                presentation_budget_exceeded = True
                ranked = tuple(sorted(
                    result.claims,
                    key=lambda item: (-item.relevance_score, item.fact_id, item.text)))
                evidence_items = ranked[:3]
                direct = type(direct)(
                    "abstain", method="presentation_source_budget",
                    residual=tuple(
                        reason for reason, applies in (
                            ("too_many_certificate_sources", too_many_sources),
                            ("certificate_exceeds_bridge_budget", certificate_too_large),
                        ) if applies))
        elif direct.state == "contested" and direct.source_ids:
            exact_by_source = {
                item.source_id: item for item in result.resolver_evidence}
            evidence_items = tuple(
                exact_by_source[source_id] for source_id in direct.source_ids
                if source_id in exact_by_source)
        else:
            ranked = tuple(sorted(
                result.claims,
                key=lambda item: (-item.relevance_score, item.fact_id, item.text)))
            best_score = ranked[0].relevance_score if ranked else 0.0
            selected_ids = tuple(dict.fromkeys(
                item.fact_id for item in ranked
                if best_score > 0.0 and item.relevance_score == best_score))[:3]
            exact_by_fact = {item.fact_id: item for item in result.resolver_evidence}
            evidence_items = tuple(
                exact_by_fact[fact_id] for fact_id in selected_ids
                if fact_id in exact_by_fact)
            if not evidence_items:
                evidence_items = result.answer_lines or result.claims
        sources = tuple({
            "fact_id": item.fact_id,
            "source_id": item.source_id,
            "text": item.text,
            "parent_sha256": item.parent_sha256 or "",
            "session_id": item.session_id,
            "speaker": item.speaker,
            "source_span": list(item.source_span) if item.source_span else None,
        } for item in evidence_items)
        if presentation_budget_exceeded:
            return MemoryReply("abstention", "", True, sources=sources, **metrics)
        if direct.state == "resolved":
            return MemoryReply(
                "proved", direct.text, True, direct.text,
                direct.certificate.hex(), direct.method, sources, **metrics)
        if direct.state == "contested":
            return MemoryReply("contested", "", True, sources=sources, **metrics)
        if result.resolved and evidence_items:
            # This is explicitly an evidence surface, not a composed answer.
            # Prefer the highest-relevance exact source spans over the adaptive
            # answer selector, which is allowed to optimize dossier coverage.
            evidence_text = "\n".join(item.text for item in evidence_items)
            fitted, truncated = _fit_utf8_prefix(
                evidence_text, MAX_REPLY_TEXT_BYTES)
            evidence_metrics = {
                **metrics,
                "answer_bytes": len(fitted.encode("utf-8")),
            }
            return MemoryReply(
                "evidence", fitted, True, sources=sources,
                text_truncated=truncated, **evidence_metrics)
        return MemoryReply("abstention", "", True, sources=sources, **metrics)

    def get_source(self, *, fact_id: int, source_id: str) -> dict[str, object]:
        if (isinstance(fact_id, bool) or not isinstance(fact_id, int)
                or fact_id < 0 or not isinstance(source_id, str) or not source_id):
            raise ValueError("source reference is invalid")
        memory = self._memory("source-reopen")
        matches = [item for item in memory.documents_snapshot()
                   if item.fact_id == fact_id]
        if len(matches) != 1:
            raise ValueError("source FactId is not active")
        document = matches[0]
        if not source_id.startswith(f"{document.source}:{fact_id}:"):
            raise ValueError("source identity does not match FactId")
        return {
            "fact_id": fact_id,
            "source_id": source_id,
            "text": document.text,
            "text_deferred": False,
            "parent_sha256": hashlib.sha256(
                document.text.encode("utf-8")).hexdigest(),
            "session_id": document.session_id,
            "speaker": document.speaker,
            "source_span": list(document.span or (0, len(document.text))),
        }

    def remember_user_message(self, *, conversation_id: str, message_id: str, text: str,
                              timestamp: datetime | None = None,
                              sequence: int | None = None) -> None:
        if (not isinstance(message_id, str) or not _APP_IDENTIFIER.fullmatch(message_id)
                or not isinstance(text, str) or not text.strip()
                or len(text.encode("utf-8")) > MAX_CHAT_TEXT_BYTES):
            raise ValueError("message identity and text are required")
        memory = self._memory(conversation_id)
        expected = self._sequences[conversation_id]
        if sequence is None:
            sequence = expected
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise ValueError("message sequence must be a non-negative integer")
        if is_authoritative_observation(text):
            self._ingest_user_message(
                memory,
                conversation_id=conversation_id,
                message_id=message_id,
                text=text.strip(),
                timestamp=timestamp or datetime.now(tz=ZoneInfo("UTC")),
                sequence=sequence,
            )
        self._sequences[conversation_id] = max(expected, sequence + 1)

    def ingest_mapped_documents(self, documents: tuple[MappedDocument, ...],
                                bundle_id: str) -> None:
        if not documents:
            return
        memory = self._memory("connector-import")
        routed = tuple(sorted((RouteDocument(
            item.fact_id, item.text, item.scope_id, item.session_id, item.version,
            item.source, sequence=item.sequence, event_time=item.event_time,
            role=item.role, speaker=item.speaker,
        ) for item in documents), key=lambda item: item.fact_id))
        existing = {item.fact_id: item for item in memory.documents_snapshot()}
        if any((prior := existing.get(document.fact_id)) is not None
               and prior.source != document.source for document in routed):
            raise RuntimeError("connector_fact_id_collision")
        try:
            receipt = memory.upsert_documents(routed, bundle_id=bundle_id)
        except ValueError as error:
            if "version" in str(error):
                raise RuntimeError("connector_update_version_not_increasing") from error
            raise
        if receipt.state not in ("APPLIED", "IDEMPOTENT"):
            raise RuntimeError("connector_upsert_rejected")

    def purge_source(self, source_id: str) -> dict[str, object]:
        memory = self._memory("purge")
        receipt = memory.purge_source(source_id)
        return {
            "state": receipt.state,
            "source_id": receipt.source_id,
            "removed_fact_ids": list(receipt.removed_fact_ids),
            "previous_head_sha256": receipt.previous_head_sha256,
            "new_head_sha256": receipt.new_head_sha256,
        }

    def purge_sources(self, source_ids: tuple[str, ...]) -> dict[str, object]:
        memory = self._memory("purge")
        receipt = memory.purge_sources(source_ids)
        return {
            "state": receipt.state,
            "source_id": receipt.source_id,
            "removed_fact_ids": list(receipt.removed_fact_ids),
            "previous_head_sha256": receipt.previous_head_sha256,
            "new_head_sha256": receipt.new_head_sha256,
        }

    def purge_source_prefix(self, prefix: str) -> dict[str, object]:
        if not prefix:
            raise ValueError("memory source prefix is required")
        memory = self._memory("purge")
        receipt = memory.purge_batch_source_prefix(prefix)
        return {
            "state": receipt.state,
            "source_id": receipt.source_id,
            "removed_fact_ids": list(receipt.removed_fact_ids),
            "previous_head_sha256": receipt.previous_head_sha256,
            "new_head_sha256": receipt.new_head_sha256,
        }

    def import_local_chunk(self, *, file_name: str, file_sha256: str,
                           byte_start: int, byte_end: int, text: str) -> dict[str, object]:
        encoded_text = text.encode("utf-8") if isinstance(text, str) else b""
        if (not isinstance(file_name, str) or not file_name
                or len(file_name.encode("utf-8")) > 512
                or any(ord(character) < 32 for character in file_name)
                or not isinstance(file_sha256, str)
                or not re.fullmatch(r"[0-9a-f]{64}", file_sha256)
                or isinstance(byte_start, bool) or not isinstance(byte_start, int)
                or isinstance(byte_end, bool) or not isinstance(byte_end, int)
                or byte_start < 0
                or byte_end <= byte_start or not text
                or byte_end - byte_start != len(encoded_text)
                or len(encoded_text) > MAX_IMPORTED_TEXT_BYTES):
            raise ValueError("invalid local import chunk")
        source_id = f"file:{file_sha256}:{byte_start}:{byte_end}"
        fact_id = stable_fact_id(source_id)
        memory = self._memory("local-import")
        receipt = memory.ingest_documents((RouteDocument(
            fact_id, text, self.scope_id, f"file:{file_sha256}", 1, source_id,
            sequence=byte_start, role="user", speaker=self.profile_name,
        ),), bundle_id=f"import:{file_sha256}:{byte_start}:{byte_end}")
        if receipt.state not in ("APPLIED", "IDEMPOTENT"):
            raise RuntimeError("local_import_rejected")
        return {
            "state": receipt.state,
            "fact_id": fact_id,
            "source_id": source_id,
            "file_name": file_name,
            "file_sha256": file_sha256,
            "byte_span": [byte_start, byte_end],
        }

    def confirm_user_observation(self, *, conversation_id: str, message_id: str,
                                 text: str, timestamp: datetime,
                                 sequence: int) -> dict[str, object]:
        memory = self._memory(conversation_id)
        self._ingest_user_message(
            memory, conversation_id=conversation_id, message_id=message_id,
            text=text.strip(), timestamp=timestamp, sequence=sequence)
        self._sequences[conversation_id] = max(
            self._sequences[conversation_id], sequence + 1)
        return {
            "state": "APPLIED",
            "source_id": f"conversation:{conversation_id}:{message_id}",
            "fact_id": stable_fact_id(f"conversation:{conversation_id}:{message_id}"),
        }


def _fit_utf8_prefix(text: str, limit: int) -> tuple[str, bool]:
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text, False
    end = limit
    while end > 0 and (raw[end] & 0xC0) == 0x80:
        end -= 1
    return raw[:end].decode("utf-8"), True
