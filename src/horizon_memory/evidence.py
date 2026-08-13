# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""EvidencePack K0 — evidência mínima, verificável e segura para adaptadores externos.

Definido aqui como tipo público estável para que o schema seja versionado desde o FH-04. A produção do
pack (rota real, verificador) é FH-06; o consumo por modelos é FH-08. Todo conteúdo recuperado é ENTRADA
NÃO CONFIÁVEL para prompt/tool: a delimitação e a política anti prompt-injection vivem no leitor.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceItem:
    fact_id: int
    source: str
    version: int | None
    value: int | None
    content: str | None = None
    span: tuple | None = None          # (start, end) quando aplicável
    verifier_state: str = "unverified"  # verified | rejected | unverified
    sequence: int | None = None        # ordem causal/temporal; ausente preserva ordem por FactId
    retrieval_rank: int | None = None  # seleção de orçamento; não altera ordem causal de leitura
    event_time: int | None = None      # relógio integral da aplicação; ordinal em datasets de calendário
    content_span: tuple[int, int] | None = None  # offsets exatos no conteúdo pai
    parent_sha256: str | None = None
    event_label: str | None = None

    def __post_init__(self) -> None:
        if self.fact_id < 0 or not self.source:
            raise ValueError("fact_id and source are required")
        if self.verifier_state not in ("verified", "rejected", "unverified"):
            raise ValueError("invalid verifier_state")
        if self.sequence is not None and self.sequence < 0:
            raise ValueError("sequence must be non-negative")
        if self.retrieval_rank is not None and self.retrieval_rank < 1:
            raise ValueError("retrieval_rank must be positive")
        if self.event_time is not None and self.event_time < 0:
            raise ValueError("event_time must be non-negative")
        if self.content_span is not None and (
                len(self.content_span) != 2 or self.content_span[0] < 0 or
                self.content_span[1] < self.content_span[0]):
            raise ValueError("invalid content_span")
        if self.parent_sha256 is not None and (
                len(self.parent_sha256) != 64 or any(ch not in "0123456789abcdef"
                                                     for ch in self.parent_sha256)):
            raise ValueError("invalid parent_sha256")
        if self.event_label is not None and (not self.event_label.strip() or "\n" in self.event_label):
            raise ValueError("invalid event_label")


def _item_order(item: EvidenceItem) -> tuple:
    return ((0, item.sequence, item.fact_id) if item.sequence is not None else
            (1, item.fact_id, item.fact_id))


@dataclass(frozen=True)
class EvidencePack:
    """Evidência mínima, ordenada deterministicamente (Final_Horizon §13)."""
    query_id: str
    items: tuple                        # tuple[EvidenceItem, ...]
    fact_ids: tuple
    sources: tuple
    versions: tuple
    generation_id: int | None
    recovery_reason: str                # bulk | residual | fallback | cold-store
    verifier_state: str                 # verified | rejected | mixed
    citations: tuple
    integrity_digest: str
    query_plan: str | None = None
    citation_labels: tuple = ()

    def __post_init__(self) -> None:
        n = len(self.items)
        if not self.query_id:
            raise ValueError("query_id is required")
        if any(len(values) != n for values in
               (self.fact_ids, self.sources, self.versions, self.citations, self.citation_labels)):
            raise ValueError("EvidencePack columns must be aligned")
        if tuple(item.fact_id for item in self.items) != self.fact_ids:
            raise ValueError("fact_ids do not match items")
        if tuple(item.source for item in self.items) != self.sources:
            raise ValueError("sources do not match items")
        if tuple(item.version for item in self.items) != self.versions:
            raise ValueError("versions do not match items")
        if len(set(self.fact_ids)) != n or tuple(sorted(self.items, key=_item_order)) != self.items:
            raise ValueError("evidence must be unique and canonically ordered")
        if self.verifier_state not in ("verified", "rejected", "unverified", "mixed"):
            raise ValueError("invalid verifier_state")
        if self.query_plan is not None and (not self.query_plan.strip() or "\n" in self.query_plan):
            raise ValueError("invalid query_plan")

    @staticmethod
    def build(query_id: str, items, *, generation_id: int | None,
              recovery_reason: str, query_plan: str | None = None,
              citation_labels: dict[int, str] | None = None) -> "EvidencePack":
        """Canoniza itens e vincula identidades/conteúdo por SHA-256.

        O digest serve para integridade/reprodução; não é MAC e não concede autoridade.
        """
        ordered = tuple(sorted(items, key=_item_order))
        payload = [{
            "fact_id": item.fact_id, "source": item.source, "version": item.version,
            "value": item.value, "content": item.content, "span": item.span,
            "verifier_state": item.verifier_state, "sequence": item.sequence,
            "retrieval_rank": item.retrieval_rank, "event_time": item.event_time,
            "content_span": item.content_span, "parent_sha256": item.parent_sha256,
            "event_label": item.event_label,
        } for item in ordered]
        citations = tuple(f"{item.source}#fact-{item.fact_id}" for item in ordered)
        labels = tuple((citation_labels or {}).get(item.fact_id, citation)
                       for item, citation in zip(ordered, citations))
        if any(not label or "\n" in label or "]" in label for label in labels):
            raise ValueError("invalid citation label")
        digest_payload = {"items": payload, "query_plan": query_plan, "citation_labels": labels}
        digest = hashlib.sha256(json.dumps(
            digest_payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")).hexdigest() if payload else ""
        states = {item.verifier_state for item in ordered}
        verifier = next(iter(states)) if len(states) == 1 else ("mixed" if states else "rejected")
        return EvidencePack(
            query_id, ordered, tuple(item.fact_id for item in ordered),
            tuple(item.source for item in ordered), tuple(item.version for item in ordered),
            generation_id, recovery_reason, verifier,
            citations, digest, query_plan, labels,
        )

    def budgeted_items(self, max_chars: int = 32_000) -> tuple:
        """Select whole turns by retrieval rank, then return them in causal pack order."""
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        used = 0
        chosen = set()
        ranked = sorted(zip(self.items, self.citation_labels), key=lambda pair: (
            pair[0].retrieval_rank is None,
            pair[0].retrieval_rank if pair[0].retrieval_rank is not None else 2 ** 63 - 1,
            _item_order(pair[0]),
        ))
        for item, citation in ranked:
            content = item.content if item.content is not None else str(item.value)
            content = content.replace("</HORIZON_EVIDENCE>", "&lt;/HORIZON_EVIDENCE&gt;")
            metadata = f" date={item.event_label}" if item.event_label else ""
            block = f"[{citation}{metadata}]\n{content}"
            separator = 2 if chosen else 0
            if used + separator + len(block) <= max_chars:
                chosen.add(item.fact_id)
                used += separator + len(block)
        return tuple(item for item in self.items if item.fact_id in chosen)

    def render_untrusted(self, max_chars: int = 32_000) -> str:
        """Renderiza dados como citação não confiável, nunca como instrução de sistema."""
        selected = self.budgeted_items(max_chars)
        blocks = []
        citations = {item.fact_id: citation
                     for item, citation in zip(self.items, self.citation_labels)}
        for item in selected:
            citation = citations[item.fact_id]
            content = item.content if item.content is not None else str(item.value)
            # Neutraliza o único marcador estrutural usado pelo template.
            content = content.replace("</HORIZON_EVIDENCE>", "&lt;/HORIZON_EVIDENCE&gt;")
            metadata = f" date={item.event_label}" if item.event_label else ""
            blocks.append(f"[{citation}{metadata}]\n{content}")
        body = "\n\n".join(blocks)
        plan = (f"<HORIZON_QUERY_PLAN>{self.query_plan}</HORIZON_QUERY_PLAN>\n"
                if self.query_plan else "")
        return (plan + "<HORIZON_EVIDENCE trust=\"untrusted-data\">\n" + body +
                "\n</HORIZON_EVIDENCE>") if body else plan.rstrip()

    @staticmethod
    def empty(query_id: str) -> "EvidencePack":
        return EvidencePack(query_id, (), (), (), (), None, "cold-store", "rejected", (), "", None, ())
