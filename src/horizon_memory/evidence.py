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
import re
from dataclasses import dataclass


_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def _content_tokens(text: str | None) -> frozenset[str]:
    if not text:
        return frozenset()
    return frozenset(_TOKEN.findall(text.casefold()))


def _jaccard(a: str, b: str) -> float:
    ta, tb = _content_tokens(a), _content_tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


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
    relevance_score: float | None = None  # FH-06.1: score bruto do Candidate que gerou este item;
    # usado só como sinal de ordenação em budgeted_items(global_sort_alpha=...), nunca como
    # autoridade -- não entra no integrity_digest (não é conteúdo, é um sinal de roteamento).

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
    span_key = item.content_span if item.content_span is not None else (-1, -1)
    return ((0, item.sequence, item.fact_id, span_key) if item.sequence is not None else
            (1, item.fact_id, item.fact_id, span_key))


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
        identities = {(item.fact_id, item.content_span) for item in self.items}
        if len(identities) != n or tuple(sorted(self.items, key=_item_order)) != self.items:
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
        citations = tuple(
            f"{item.source}#fact-{item.fact_id}:{item.content_span[0]}-{item.content_span[1]}"
            if item.content_span is not None else f"{item.source}#fact-{item.fact_id}"
            for item in ordered)
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

    def budgeted_items(self, max_chars: int = 32_000, *, global_sort_alpha: float | None = None,
                       source_priority: dict[str, float] | None = None,
                       dedup_threshold: float | None = None) -> tuple:
        """Select whole turns/claims under a byte-like character budget, then return them in
        causal pack order.

        `global_sort_alpha` (D137 Variant 3 port, 2026-08-17): the default merge is rank-major --
        every item is offered budget in `(retrieval_rank, canonical order)` tiers, so with many
        distinct sources included a strongly-relevant source's 2nd/3rd claim still queues behind
        every other source's own single best claim (the flooding pattern D137 diagnosed). When
        set (float in [0,1]), replaces rank-major entirely with one global sort key per item:
        `alpha * source_priority.get(item.source, 0.0) + (1-alpha) * (item.relevance_score or
        0.0)` -- blending a document-level routing confidence (e.g. a conformal p-value, passed
        via `source_priority`) with the item's own channel relevance score (`Candidate.score`,
        carried onto `EvidenceItem.relevance_score` by `HorizonVerifier`). Defaults to None,
        which preserves the original retrieval_rank-tier order and every digest/selection
        computed before this parameter existed.

        `dedup_threshold` (D135 port, corrected): rejects a candidate item whose content is more
        than `dedup_threshold` Jaccard-similar (token overlap) to an already-selected item's
        content -- moderate near-duplicate rejection (>0.6) was D135's validated production
        default; no per-query calibration is required (D135's own correction: a fixed threshold
        matched a fully-calibrated strategist's output on 190/200 held-out episodes). Defaults to
        None (no dedup), preserving prior behavior.
        """
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        if global_sort_alpha is not None and not (0.0 <= global_sort_alpha <= 1.0):
            raise ValueError("global_sort_alpha must be in [0,1]")
        if dedup_threshold is not None and not (0.0 <= dedup_threshold <= 1.0):
            raise ValueError("dedup_threshold must be in [0,1]")
        used = 0
        chosen = set()
        selected_contents: list[str] = []
        pairs = list(zip(self.items, self.citation_labels))
        if global_sort_alpha is not None:
            def _sort_key(pair):
                item, _citation = pair
                doc_score = (source_priority.get(item.source, 0.0)
                            if source_priority is not None else 0.0)
                claim_score = item.relevance_score if item.relevance_score is not None else 0.0
                combined = global_sort_alpha * doc_score + (1 - global_sort_alpha) * claim_score
                return (-combined, _item_order(item))
            ranked = sorted(pairs, key=_sort_key)
        else:
            ranked = sorted(pairs, key=lambda pair: (
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
            if used + separator + len(block) > max_chars:
                continue
            if dedup_threshold is not None and any(
                    _jaccard(content, existing) > dedup_threshold
                    for existing in selected_contents):
                continue
            chosen.add((item.fact_id, item.content_span))
            selected_contents.append(content)
            used += separator + len(block)
        return tuple(item for item in self.items if (item.fact_id, item.content_span) in chosen)

    def render_untrusted(self, max_chars: int = 32_000, *, global_sort_alpha: float | None = None,
                         source_priority: dict[str, float] | None = None,
                         dedup_threshold: float | None = None) -> str:
        """Renderiza dados como citação não confiável, nunca como instrução de sistema.

        `global_sort_alpha`/`source_priority`/`dedup_threshold` (2026-08-17): forwarded verbatim
        to `budgeted_items()` -- this method previously always used the default rank-major merge
        regardless of what selection a caller wanted, silently discarding any D137/D135 merge
        preference before rendering. All default `None`, preserving prior output exactly."""
        selected = self.budgeted_items(max_chars, global_sort_alpha=global_sort_alpha,
                                       source_priority=source_priority,
                                       dedup_threshold=dedup_threshold)
        blocks = []
        citations = {(item.fact_id, item.content_span): citation
                     for item, citation in zip(self.items, self.citation_labels)}
        for item in selected:
            citation = citations[(item.fact_id, item.content_span)]
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
