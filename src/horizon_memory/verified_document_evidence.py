# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Verified microcitation packs for ranked JSONL document collections."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import struct

from .raw_causal_channels import observe_raw_text


_SENTENCE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)")
_WORD = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class VerifiedJsonlDocument:
    fact_id: int
    external_id: str
    source_id: str
    raw_record: str
    document_text: str
    document_sha256: str


@dataclass(frozen=True)
class DocumentMicrocitation:
    fact_id: int
    external_id: str
    source_id: str
    source_sha256: str
    source_span: tuple[int, int]
    text: str


@dataclass(frozen=True)
class VerifiedSnippetPack:
    state: str
    citations: tuple[DocumentMicrocitation, ...]
    evidence_bytes: int
    proof_sidecar_bytes: int
    examined_fact_ids: tuple[int, ...]
    reason: str

    @property
    def fact_ids(self) -> tuple[int, ...]:
        return tuple(item.fact_id for item in self.citations)


class VerifiedJsonlDocumentCorpus:
    """Reopen decoded title/text against immutable literal JSONL records."""

    def __init__(self, source_id: str, content: str, *, id_field: str = "_id",
                 title_field: str = "title", text_field: str = "text"):
        if not source_id or not content:
            raise ValueError("verified JSONL corpus needs source identity and content")
        parsed = []
        for line_number, raw_line in enumerate(content.splitlines(), 1):
            if not raw_line.strip():
                continue
            record = json.loads(raw_line)
            external_id = str(record[id_field])
            title, text = str(record.get(title_field, "")), str(record.get(text_field, ""))
            document_text = f"{title} {text}".strip()
            if not external_id or not document_text:
                raise ValueError("JSONL document identity and decoded text are required")
            parsed.append((external_id, line_number, raw_line, document_text))
        if not parsed or len({item[0] for item in parsed}) != len(parsed):
            raise ValueError("verified JSONL corpus requires unique external identities")
        documents = []
        for fact_id, (external_id, line_number, raw_line, document_text) in enumerate(
                sorted(parsed), 1):
            documents.append(VerifiedJsonlDocument(
                fact_id, external_id, f"{source_id}#L{line_number}", raw_line, document_text,
                hashlib.sha256(document_text.encode()).hexdigest()))
        self.source_id = source_id
        self.id_field = id_field
        self.title_field = title_field
        self.text_field = text_field
        self.documents = tuple(documents)
        self.by_id = {item.fact_id: item for item in documents}

    def _reopen(self, document: VerifiedJsonlDocument) -> str | None:
        try:
            record = json.loads(document.raw_record)
        except (TypeError, json.JSONDecodeError):
            return None
        title = str(record.get(self.title_field, ""))
        text = str(record.get(self.text_field, ""))
        reopened = f"{title} {text}".strip()
        external_id = str(record.get(self.id_field, ""))
        return reopened if external_id == document.external_id else None

    def verify(self, citation: DocumentMicrocitation) -> bool:
        document = self.by_id.get(citation.fact_id)
        if document is None or citation.external_id != document.external_id or \
                citation.source_id != document.source_id or \
                citation.source_sha256 != document.document_sha256:
            return False
        reopened = self._reopen(document)
        if reopened is None or hashlib.sha256(reopened.encode()).hexdigest() != \
                document.document_sha256:
            return False
        start, end = citation.source_span
        return 0 <= start < end <= len(reopened) and reopened[start:end] == citation.text

    @staticmethod
    def compact_proof_sidecar(citations: tuple[DocumentMicrocitation, ...]) -> bytes:
        """Canonical binary proof: version/count then FactId, span and document SHA-256.

        External/source identities are recovered from the sealed corpus by FactId and
        verified by ``verify``; repeating variable-length strings is not proof mass.
        """
        if len(citations) > 65535:
            raise ValueError("compact proof sidecar exceeds u16 citation count")
        output = bytearray(b"HDP1" + struct.pack(">H", len(citations)))
        for item in citations:
            start, end = item.source_span
            if not 0 <= item.fact_id <= 0xFFFFFFFF or not 0 <= start < end <= 0xFFFFFFFF:
                raise ValueError("compact proof identity/span exceeds u32")
            output.extend(struct.pack(">III", item.fact_id, start, end))
            output.extend(bytes.fromhex(item.source_sha256))
        return bytes(output)

    def verify_compact_proof_sidecar(self, sidecar: bytes,
                                     citations: tuple[DocumentMicrocitation, ...]) -> bool:
        if not isinstance(sidecar, bytes) or len(sidecar) < 6 or sidecar[:4] != b"HDP1":
            return False
        count = struct.unpack(">H", sidecar[4:6])[0]
        if count != len(citations) or len(sidecar) != 6 + 44 * count:
            return False
        offset = 6
        for citation in citations:
            fact_id, start, end = struct.unpack(">III", sidecar[offset:offset + 12])
            digest = sidecar[offset + 12:offset + 44].hex()
            if (fact_id, (start, end), digest) != (
                    citation.fact_id, citation.source_span, citation.source_sha256):
                return False
            if not self.verify(citation):
                return False
            offset += 44
        return True

    @staticmethod
    def _sentence(document: str, query: str) -> tuple[int, int]:
        query_channels = observe_raw_text(query, question=True)
        lexical, numbers = set(query_channels.lexical), set(query_channels.numbers)
        candidates = []
        for match in _SENTENCE.finditer(document):
            channels = observe_raw_text(match.group())
            score = (len(lexical.intersection(channels.lexical)),
                     len(numbers.intersection(channels.numbers)),
                     len(set(query_channels.relations).intersection(channels.relations)))
            candidates.append((score, -len(match.group()), -match.start(), match.start(), match.end()))
        if not candidates:
            return 0, len(document)
        return max(candidates)[-2:]

    @staticmethod
    def _bounded(document: str, start: int, end: int, query: str,
                 max_bytes: int) -> tuple[int, int]:
        if len(document[start:end].encode()) <= max_bytes:
            return start, end
        raw_query_words = {word.casefold() for word in _WORD.findall(query)}
        anchor = start
        for match in _WORD.finditer(document, start, end):
            if match.group().casefold() in raw_query_words:
                anchor = match.start()
                break
        left = max(start, anchor - max_bytes // 3)
        right = min(end, left + max_bytes)
        while right > left and len(document[left:right].encode()) > max_bytes:
            right -= 1
        while left < right and document[left].isspace():
            left += 1
        return left, right

    def pack(self, query: str, fact_ids: tuple[int, ...], *, max_bytes: int = 2048,
             per_document_bytes: int = 64, max_items: int = 32) -> VerifiedSnippetPack:
        if not query.strip() or max_bytes < 1 or per_document_bytes < 1 or max_items < 1:
            raise ValueError("snippet pack needs query and positive budgets")
        if len(set(fact_ids)) != len(fact_ids) or any(fact_id not in self.by_id for fact_id in fact_ids):
            raise ValueError("snippet FactIds must be unique and known")
        citations = []
        examined = []
        evidence_bytes = 0
        for fact_id in fact_ids:
            if len(citations) >= max_items:
                break
            examined.append(fact_id)
            document = self.by_id[fact_id]
            start, end = self._sentence(document.document_text, query)
            separator_bytes = 1 if citations else 0
            remaining = max_bytes - evidence_bytes - separator_bytes
            if remaining < 1:
                break
            start, end = self._bounded(
                document.document_text, start, end, query,
                min(per_document_bytes, remaining))
            if end <= start:
                continue
            snippet = document.document_text[start:end]
            cost = len(snippet.encode())
            if evidence_bytes + separator_bytes + cost > max_bytes:
                continue
            citations.append(DocumentMicrocitation(
                fact_id, document.external_id, document.source_id,
                document.document_sha256, (start, end), snippet))
            evidence_bytes += separator_bytes + cost
            if evidence_bytes >= max_bytes:
                break
        proof_bytes = len(self.compact_proof_sidecar(tuple(citations)))
        state = "ready" if citations else "incomplete"
        return VerifiedSnippetPack(
            state, tuple(citations), evidence_bytes, proof_bytes, tuple(examined),
            "ranked microcitations fit the evidence budget" if citations
            else "no ranked microcitation fit the evidence budget")
