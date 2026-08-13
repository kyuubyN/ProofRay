# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Materialized conversational cavities: hear the local phrase, cite one FactId."""
from __future__ import annotations

from dataclasses import dataclass

from .raw_causal_channels import RawCausalDocument, RawCausalSyndromeIndex, SignedChannelScore
from .raw_causal_channels import observe_raw_text
from .pragmatic_roles import observe_pragmatic_roles


@dataclass(frozen=True)
class CavityScore:
    fact_id: int
    amplitude: float
    direct: float
    incoming: float
    witness_fact_ids: tuple[int, ...]


class ContextualCavityIndex:
    """Propagate query-independent adjacency while conserving the cited center.

    Neighbor topology is compiled at ingestion. Query observables are measured once for
    every FactId, then local amplitudes resonate through the frozen cavity. Incoming
    evidence uses a max rather than a sum so verbosity cannot manufacture mass. The
    center FactId remains the only candidate identity; neighbors are provenance witnesses.
    """

    def __init__(self, documents: tuple[RawCausalDocument, ...], *, radius: int = 1,
                 decay: float = .5, forward_weight: float = 1.0,
                 backward_weight: float = 1.0):
        if radius < 1 or not 0 <= decay <= 1 or forward_weight < 0 or backward_weight < 0:
            raise ValueError("invalid contextual cavity configuration")
        self.documents = documents
        self.radius = radius
        self.decay = decay
        self.forward_weight = forward_weight
        self.backward_weight = backward_weight
        self.index = RawCausalSyndromeIndex(documents)
        ordered = sorted(documents, key=lambda item: (item.session_index, item.turn, item.fact_id))
        by_session = {}
        for document in ordered:
            by_session.setdefault(document.session_index, []).append(document)
        cavities = {}
        for session_documents in by_session.values():
            for position, center in enumerate(session_documents):
                neighbors = []
                for offset in range(1, radius + 1):
                    if position - offset >= 0:
                        neighbors.append((session_documents[position - offset].fact_id,
                                          backward_weight * decay ** (offset - 1)))
                    if position + offset < len(session_documents):
                        neighbors.append((session_documents[position + offset].fact_id,
                                          forward_weight * decay ** (offset - 1)))
                cavities[center.fact_id] = tuple(sorted(neighbors))
        self.cavities = cavities
        self.sessions = {document.fact_id: tuple(item.fact_id for item in by_session[document.session_index]
                                                 if item.fact_id != document.fact_id)
                         for document in documents}

    def rank(self, query_text: str, *, lexical_weight: float = 1.0,
             sublexical_weight: float = 0.0,
             speaker_weight: float = 0.0,
             session_weight: float = 0.0,
             role_weight: float = 0.0) -> tuple[CavityScore, ...]:
        if lexical_weight < 0 or sublexical_weight < 0 or speaker_weight < 0 \
                or session_weight < 0 or role_weight < 0 \
                or lexical_weight + sublexical_weight + speaker_weight == 0:
            raise ValueError("cavity needs a positive observable weight")
        components: tuple[SignedChannelScore, ...] = self.index.components(query_text)
        query_tokens = set(observe_raw_text(query_text, question=True).lexical)
        speakers = {item.fact_id: set(observe_raw_text(item.speaker).lexical)
                    for item in self.documents}
        query_roles = set(observe_pragmatic_roles(query_text, question=True))
        document_roles = {item.fact_id: set(observe_pragmatic_roles(item.text))
                          for item in self.documents}
        direct = {item.fact_id: lexical_weight * item.lexical +
                  sublexical_weight * item.sublexical + speaker_weight *
                  bool(query_tokens.intersection(speakers[item.fact_id])) + role_weight *
                  bool(query_roles.intersection(document_roles[item.fact_id]))
                  for item in components}
        result = []
        for document in self.documents:
            candidates = (tuple((weight * direct[fact_id], fact_id)
                                for fact_id, weight in self.cavities[document.fact_id]) +
                          tuple((session_weight * direct[fact_id], fact_id)
                                for fact_id in self.sessions[document.fact_id]))
            incoming = max((value for value, _ in candidates), default=0.0)
            witnesses = tuple(sorted(fact_id for value, fact_id in candidates
                                     if incoming > 0 and abs(value - incoming) < 1e-12))
            result.append(CavityScore(document.fact_id, direct[document.fact_id] + incoming,
                                      direct[document.fact_id], incoming, witnesses))
        return tuple(sorted(result, key=lambda item: (-item.amplitude, -item.direct,
                                                       item.fact_id)))
