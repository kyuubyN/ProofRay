# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""FH-06 / V25 — roteamento causal por lista; candidatos nunca validam conteudo.

Lexical e dense propoem apenas FactIds. A autoridade final e uma leitura da Horizon seguida de
verificacao exata de scope, versao, geracao e proveniencia registrada.
"""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from enum import Enum

from .content_safety import SafetyPolicy, UnsafeContentError, screen_text
from .evidence import EvidenceItem, EvidencePack
from .types import ReadState


_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


def _tokens(text: str) -> tuple[str, ...]:
    if not isinstance(text, str):
        raise TypeError("text must be str")
    return tuple(_TOKEN.findall(text.casefold()))


@dataclass(frozen=True)
class QueryEnvelope:
    query_id: str
    text: str
    scope_id: int
    session_id: str
    timestamp: int
    active_goals: tuple[str, ...] = ()
    event_time: int | None = None

    def __post_init__(self):
        if not self.query_id or not self.text.strip() or not self.session_id:
            raise ValueError("query_id, text and session_id are required")
        if self.scope_id < 0 or self.timestamp < 0:
            raise ValueError("scope_id and timestamp must be non-negative")
        if self.event_time is not None and self.event_time < 0:
            raise ValueError("event_time must be non-negative")


@dataclass(frozen=True)
class RouteDocument:
    fact_id: int
    text: str
    scope_id: int
    session_id: str
    version: int
    source: str
    generation_id: int | None = None
    sequence: int | None = None
    span: tuple | None = None
    role: str | None = None
    event_time: int | None = None
    # Ingestion-time safety gate (2026-08-18): screens `text` before a document can ever enter
    # the routing index at all -- see `content_safety.py` for the full design rationale and the
    # honest scope of what this does and does not catch. Off by default (`None`) -- opt-in, not
    # a hot-path default, per the project owner's own explicit call: pass a `SafetyPolicy`
    # (e.g. `DEFAULT_POLICY` for every category, or a custom policy to disable individual
    # non-CSAM categories) to enable it for a specific document/deployment. CSAM is never
    # skippable, by construction of `screen_text` itself, whenever a non-`None` policy is passed.
    safety_policy: SafetyPolicy | None = None

    def __post_init__(self):
        if self.fact_id < 0 or self.scope_id < 0 or self.version < 1:
            raise ValueError("invalid fact identity")
        if not self.text.strip() or not self.session_id or not self.source:
            raise ValueError("text, session_id and source are required")
        if self.sequence is not None and self.sequence < 0:
            raise ValueError("sequence must be non-negative")
        if self.safety_policy is not None:
            verdict = screen_text(self.text, self.safety_policy)
            if not verdict.safe:
                raise UnsafeContentError(verdict.category, verdict.reason)
        if self.event_time is not None and self.event_time < 0:
            raise ValueError("event_time must be non-negative")
        if self.role is not None and self.role not in ("user", "assistant", "system", "tool"):
            raise ValueError("invalid role")


@dataclass(frozen=True)
class Candidate:
    fact_id: int
    score: float
    channel: str
    rank: int
    namespace: str
    claim_span: tuple[int, int] | None = None
    """FH-06.1: an optional exact (start, end) span within the parent RouteDocument's own text.
    None (default) means the candidate refers to the whole document, preserving every prior
    generator's behavior unchanged. When set, HorizonVerifier still authorizes via the PARENT
    fact_id's own presence/version/generation in the Horizon store (a claim is never itself a
    separately-stored fact) but returns only the exact substring as evidence content -- the same
    discipline lab/deterministic_claim_composer.py's AuthorizedClaim already uses in the research
    line, ported here as the claim generator's own field on the routing type it already existed
    next to (EvidenceItem.content_span/parent_sha256 predate this and were never populated by any
    generator until now)."""

    def __post_init__(self):
        if self.claim_span is not None and (
                len(self.claim_span) != 2 or self.claim_span[0] < 0 or
                self.claim_span[1] <= self.claim_span[0]):
            raise ValueError("invalid claim_span")


@dataclass(frozen=True)
class CandidateList:
    candidates: tuple[Candidate, ...]

    def __post_init__(self):
        identities = [(candidate.fact_id, candidate.claim_span) for candidate in self.candidates]
        if len(identities) != len(set(identities)):
            raise ValueError("CandidateList must be deduplicated by (FactId, claim_span)")


class RouteState(Enum):
    EVIDENCE = "evidence"
    ABSTENTION = "abstention"
    ABSTAIN_SCOPE = "abstain_scope"
    ABSTAIN_UNSAFE_CONTENT = "abstain_unsafe_content"


@dataclass(frozen=True)
class RouteTrace:
    query_id: str
    channel: str
    requested_l: int
    candidates_touched: int
    horizon_lookups: int
    verifier_rejections: int
    session_fallback_used: bool
    reason: str


@dataclass(frozen=True)
class RoutedResult:
    state: RouteState
    evidence: EvidencePack
    trace: RouteTrace


def _hashed_vector(tokens: tuple[str, ...], dimensions: int = 65536) -> dict[int, float]:
    vector: dict[int, float] = {}
    for token in tokens:
        digest = hashlib.sha256(b"horizon-v25-dense\x00" + token.encode()).digest()
        index = int.from_bytes(digest[:4], "little") % dimensions
        vector[index] = vector.get(index, 0.0) + (-1.0 if digest[4] & 1 else 1.0)
    norm = math.sqrt(sum(value * value for value in vector.values())) or 1.0
    return {index: value / norm for index, value in vector.items()}


class RoutingIndex:
    """Indice read-only em memoria para o experimento V25."""

    def __init__(self, documents: tuple[RouteDocument, ...]):
        by_id = {}
        for document in documents:
            if document.fact_id in by_id:
                raise ValueError("duplicate fact_id")
            by_id[document.fact_id] = document
        self.documents = tuple(sorted(documents, key=lambda item: item.fact_id))
        self.by_id = by_id
        self.doc_tokens = {doc.fact_id: _tokens(doc.text) for doc in self.documents}
        self.doc_vectors = {fid: _hashed_vector(tokens) for fid, tokens in self.doc_tokens.items()}
        document_frequency: dict[str, int] = {}
        for tokens in self.doc_tokens.values():
            for token in set(tokens):
                document_frequency[token] = document_frequency.get(token, 0) + 1
        self.document_frequency = document_frequency

    def eligible(self, query: QueryEnvelope, same_session: bool) -> tuple[RouteDocument, ...]:
        return tuple(doc for doc in self.documents if doc.scope_id == query.scope_id and
                     (not same_session or doc.session_id == query.session_id))


class CandidateGenerator:
    channel = "base"

    def generate(self, query: QueryEnvelope, index: RoutingIndex, limit: int,
                 same_session: bool = True) -> CandidateList:
        raise NotImplementedError


class LexicalGenerator(CandidateGenerator):
    channel = "lexical"

    def generate(self, query, index, limit, same_session=True):
        query_tokens = _tokens(query.text)
        n_docs = max(1, len(index.documents))
        scored = []
        for doc in index.eligible(query, same_session):
            tokens = index.doc_tokens[doc.fact_id]
            counts = {token: tokens.count(token) for token in set(tokens)}
            score = sum(counts.get(token, 0) * math.log((n_docs + 1) /
                        (index.document_frequency.get(token, 0) + 1)) for token in set(query_tokens))
            if score > 0:
                scored.append((score, doc.fact_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        namespace = "scope_session" if same_session else "scope_fallback"
        return CandidateList(tuple(Candidate(fid, score, self.channel, rank + 1, namespace)
                                   for rank, (score, fid) in enumerate(scored[:limit])))


class BM25Generator(CandidateGenerator):
    """Turn-level BM25 with deterministic tie-breaking and no learned/gold state."""
    channel = "bm25"

    def __init__(self, k1: float = 1.2, b: float = 0.75):
        if k1 <= 0 or not 0 <= b <= 1:
            raise ValueError("invalid BM25 parameters")
        self.k1, self.b = k1, b

    def generate(self, query, index, limit, same_session=True):
        query_terms = set(_tokens(query.text))
        eligible = index.eligible(query, same_session)
        n_docs = max(1, len(eligible))
        lengths = {doc.fact_id: len(index.doc_tokens[doc.fact_id]) for doc in eligible}
        average_length = sum(lengths.values()) / n_docs
        document_frequency = {
            term: sum(term in index.doc_tokens[doc.fact_id] for doc in eligible)
            for term in query_terms
        }
        scored = []
        for doc in eligible:
            tokens = index.doc_tokens[doc.fact_id]
            counts = {term: tokens.count(term) for term in query_terms}
            score = 0.0
            for term in query_terms:
                frequency = counts[term]
                if not frequency:
                    continue
                inverse_frequency = math.log(1.0 + (n_docs - document_frequency[term] + 0.5) /
                                             (document_frequency[term] + 0.5))
                denominator = frequency + self.k1 * (
                    1.0 - self.b + self.b * lengths[doc.fact_id] / (average_length or 1.0))
                score += inverse_frequency * frequency * (self.k1 + 1.0) / denominator
            if score > 0:
                scored.append((score, doc.fact_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        namespace = "scope_session" if same_session else "scope_fallback"
        return CandidateList(tuple(Candidate(fid, score, self.channel, rank + 1, namespace)
                                   for rank, (score, fid) in enumerate(scored[:limit])))


class CausalWeaveGenerator(CandidateGenerator):
    """HCWD-v0: turn/session measurements plus causal dialogue-boundary coverage.

    This generator is deliberately deterministic and label-free.  It never sees answer
    sessions or `has_answer`; the Horizon verifier remains the content authority.
    """
    channel = "causal_weave"

    def __init__(self, boundary_fraction: float = 0.75, user_turns_per_session: int = 2,
                 session_weight: float = 0.5):
        if not 0 <= boundary_fraction <= 1 or user_turns_per_session < 1 or session_weight < 0:
            raise ValueError("invalid causal weave parameters")
        self.boundary_fraction = boundary_fraction
        self.user_turns_per_session = user_turns_per_session
        self.session_weight = session_weight
        self.bm25 = BM25Generator()

    @staticmethod
    def _session_ranking(query: QueryEnvelope, documents: tuple[RouteDocument, ...]) \
            -> tuple[list[str], dict[str, float]]:
        by_session: dict[str, list[RouteDocument]] = {}
        for document in documents:
            by_session.setdefault(document.session_id, []).append(document)
        synthetic = []
        identity = {}
        ordered_sessions = sorted(by_session.items(), key=lambda pair: (
            min((member.sequence for member in pair[1] if member.sequence is not None),
                default=2 ** 63 - 1),
            pair[0],
        ))
        for position, (session_id, members) in enumerate(ordered_sessions):
            synthetic_id = position + 1
            identity[synthetic_id] = session_id
            text = "\n".join(member.text for member in sorted(
                members, key=lambda item: (item.sequence is None, item.sequence, item.fact_id)))
            synthetic.append(RouteDocument(synthetic_id, text, query.scope_id, session_id, 1,
                                           f"session:{session_id}"))
        ranking = BM25Generator().generate(
            query, RoutingIndex(tuple(synthetic)), max(1, min(32, len(synthetic))),
            same_session=False,
        )
        ranked = [identity[candidate.fact_id] for candidate in ranking.candidates]
        scores = {identity[candidate.fact_id]: candidate.score for candidate in ranking.candidates}
        # Sessions with zero lexical score remain a deterministic uncertainty tail.
        ranked.extend(session_id for session_id, _ in ordered_sessions if session_id not in ranked)
        return ranked, scores

    def generate(self, query, index, limit, same_session=True):
        documents = index.eligible(query, same_session)
        if not documents:
            return CandidateList(())
        lexical = self.bm25.generate(query, index, max(32, limit), same_session).candidates
        turn_score = {candidate.fact_id: candidate.score for candidate in lexical}
        session_ranking, session_score = self._session_ranking(query, documents)
        max_turn = max(turn_score.values(), default=1.0) or 1.0
        max_session = max(session_score.values(), default=1.0) or 1.0

        # Both BM25 surfaces are normalized before fusion. A session macrostate can raise
        # a weak turn, but cannot manufacture a candidate outside the causal namespace.
        fused = sorted(documents, key=lambda document: (
            -(turn_score.get(document.fact_id, 0.0) / max_turn +
              self.session_weight * session_score.get(document.session_id, 0.0) / max_session),
            document.sequence is None,
            document.sequence if document.sequence is not None else 2 ** 63 - 1,
            document.fact_id,
        ))

        target_boundary = round(limit * self.boundary_fraction)
        session_count = math.ceil(target_boundary / self.user_turns_per_session)
        by_session: dict[str, list[RouteDocument]] = {}
        for document in documents:
            by_session.setdefault(document.session_id, []).append(document)
        selected = []
        for session_id in session_ranking[:session_count]:
            members = sorted(by_session[session_id], key=lambda item: (
                item.sequence is None, item.sequence, item.fact_id))
            user_events = [member for member in members if member.role == "user"]
            boundary = (user_events or members)[:self.user_turns_per_session]
            for document in boundary:
                if document.fact_id not in selected and len(selected) < target_boundary:
                    selected.append(document.fact_id)
        for document in fused:
            if document.fact_id not in selected:
                selected.append(document.fact_id)
            if len(selected) >= limit:
                break
        namespace = "scope_session" if same_session else "scope_fallback"
        return CandidateList(tuple(Candidate(
            fid, 1.0 / rank, self.channel, rank, namespace
        ) for rank, fid in enumerate(selected[:limit], 1)))


class TemporalCausalWeaveGenerator(CandidateGenerator):
    """HCWD-v1 exact relative-day projection with HCWD-v0 fallback."""

    channel = "temporal_causal_weave"
    _DAYS_AGO = re.compile(r"\b(\d+)\s+(?:day|days|dia|dias)\s+(?:ago|atras)\b", re.IGNORECASE)

    def __init__(self):
        self.weave = CausalWeaveGenerator()

    def generate(self, query, index, limit, same_session=True):
        match = self._DAYS_AGO.search(query.text)
        if match is None:
            return self.weave.generate(query, index, limit, same_session)
        if query.event_time is None:
            return self.weave.generate(query, index, limit, same_session)
        target = query.event_time - int(match.group(1))
        eligible = tuple(document for document in index.eligible(query, same_session)
                         if document.event_time == target)
        if not eligible:
            return self.weave.generate(query, index, limit, same_session)
        ordered = sorted(eligible, key=lambda document: (
            document.sequence is None,
            document.sequence if document.sequence is not None else 2 ** 63 - 1,
            document.fact_id,
        ))[:limit]
        namespace = "scope_session_time" if same_session else "scope_time"
        return CandidateList(tuple(Candidate(
            document.fact_id, 1.0 / rank, self.channel, rank, namespace
        ) for rank, document in enumerate(ordered, 1)))


class DenseGenerator(CandidateGenerator):
    """Dense local deterministico (feature hashing); sem modelo, rede ou gold."""
    channel = "dense"

    def generate(self, query, index, limit, same_session=True):
        vector = _hashed_vector(_tokens(query.text))
        scored = []
        for doc in index.eligible(query, same_session):
            doc_vector = index.doc_vectors[doc.fact_id]
            score = sum(value * doc_vector.get(feature, 0.0) for feature, value in vector.items())
            if score > 0:
                scored.append((score, doc.fact_id))
        scored.sort(key=lambda item: (-item[0], item[1]))
        namespace = "scope_session" if same_session else "scope_fallback"
        return CandidateList(tuple(Candidate(fid, score, self.channel, rank + 1, namespace)
                                   for rank, (score, fid) in enumerate(scored[:limit])))


class HybridGenerator(CandidateGenerator):
    channel = "hybrid"

    def __init__(self):
        self.lexical = LexicalGenerator()
        self.dense = DenseGenerator()

    def generate(self, query, index, limit, same_session=True):
        depth = max(limit, 32)
        channels = (self.lexical.generate(query, index, depth, same_session),
                    self.dense.generate(query, index, depth, same_session))
        scores: dict[int, float] = {}
        namespace: dict[int, str] = {}
        for candidates in channels:
            for candidate in candidates.candidates:
                scores[candidate.fact_id] = scores.get(candidate.fact_id, 0.0) + 1.0 / (60 + candidate.rank)
                namespace[candidate.fact_id] = candidate.namespace
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
        return CandidateList(tuple(Candidate(fid, score, self.channel, rank + 1, namespace[fid])
                                   for rank, (fid, score) in enumerate(ranked)))


class HorizonVerifier:
    def __init__(self, memory, index: RoutingIndex):
        self.memory = memory
        self.index = index

    def verify(self, query: QueryEnvelope, candidate: Candidate) -> EvidenceItem | None:
        document = self.index.by_id.get(candidate.fact_id)
        if document is None or document.scope_id != query.scope_id:
            return None
        read = self.memory.get(query.scope_id, candidate.fact_id)
        if read.state != ReadState.PRESENT or read.version != document.version:
            return None
        if document.generation_id is not None and read.generation_id != document.generation_id:
            return None
        if candidate.claim_span is not None:
            start, end = candidate.claim_span
            if end > len(document.text):
                return None
            surface = document.text[start:end]
            content = f"{document.role}: {surface}" if document.role else surface
            return EvidenceItem(candidate.fact_id, document.source, read.version, read.value,
                                content=content, span=document.span, verifier_state="verified",
                                sequence=document.sequence, retrieval_rank=candidate.rank,
                                event_time=document.event_time,
                                content_span=candidate.claim_span,
                                parent_sha256=hashlib.sha256(document.text.encode()).hexdigest(),
                                relevance_score=candidate.score)
        content = f"{document.role}: {document.text}" if document.role else document.text
        return EvidenceItem(candidate.fact_id, document.source, read.version, read.value,
                            content=content, span=document.span, verifier_state="verified",
                            sequence=document.sequence, retrieval_rank=candidate.rank,
                            event_time=document.event_time, relevance_score=candidate.score)


class SemanticRouter:
    def __init__(self, index: RoutingIndex, generator: CandidateGenerator, verifier: HorizonVerifier):
        self.index = index
        self.generator = generator
        self.verifier = verifier

    def route(self, query: QueryEnvelope, limit: int, allow_scope_fallback: bool = True, *,
             safety_policy: SafetyPolicy | None = None) -> RoutedResult:
        """`limit` (relaxed 2026-08-17, FH-06.2): originally restricted to the V25 experiment's
        own `{1,2,4,8,16,32}` enum -- a historical convention, not a mechanism dependency (`limit`
        is only ever used here as a candidate-list slice bound, never in power-of-2 arithmetic).
        A claim-level generator (`ClaimGenerator`/`ConformalClaimGenerator`, FH-06.1/FH-06.2)
        filling a large byte budget (`EvidencePack.budgeted_items(max_chars=...)`) with many short
        sentence-level candidates needs a claim COUNT well above 32 to avoid the candidate cap
        binding before the byte budget does -- any positive integer is accepted now.

        `safety_policy` (2026-08-18): query-time content-safety gate, second layer alongside
        `RouteDocument`'s own ingestion-time gate -- see `content_safety.py`'s module docstring
        for the full design rationale. Covers two cases the ingestion gate alone cannot: the
        QUERY text itself (never screened at ingestion, since a query is not a `RouteDocument`),
        and evidence that entered the index before this gate existed or via `safety_policy=None`
        at ingestion. Off by default (`None`), matching `RouteDocument.safety_policy`'s own
        opt-in default (project owner's own explicit call, 2026-08-18) -- pass a `SafetyPolicy`
        to enable it. When enabled, any unsafe query text, or any unsafe content among the
        verified evidence, aborts the whole route to `RouteState.ABSTAIN_UNSAFE_CONTENT` rather
        than silently dropping just the offending item -- consistent with this project's own "a
        confident wrong/partial answer is worse than an honest abstention" principle."""
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        if query.scope_id != self.verifier.memory.scope_id:
            trace = RouteTrace(query.query_id, self.generator.channel, limit, 0, 0, 0, False,
                               "scope_mismatch")
            return RoutedResult(RouteState.ABSTAIN_SCOPE, EvidencePack.empty(query.query_id), trace)
        if safety_policy is not None and not screen_text(query.text, safety_policy).safe:
            trace = RouteTrace(query.query_id, self.generator.channel, limit, 0, 0, 0, False,
                               "unsafe_query")
            return RoutedResult(RouteState.ABSTAIN_UNSAFE_CONTENT,
                                EvidencePack.empty(query.query_id), trace)

        primary = self.generator.generate(query, self.index, limit, same_session=True)
        candidates = list(primary.candidates)
        fallback_used = False
        if allow_scope_fallback:
            fallback = self.generator.generate(query, self.index, limit, same_session=False)
            # Combina sempre os dois rankings. Condicionar o fallback a uma lista local incompleta
            # bloquearia transferencia quando uma sessao densa preenchesse L com candidatos errados.
            # O score vem do mesmo gerador nos dois namespaces; empate favorece a sessao corrente.
            # Chave por (fact_id, claim_span): um documento pode contribuir varios candidatos de
            # alegacao distintos (FH-06.1), cada um deduplicado/fundido independentemente.
            merged: dict[tuple[int, tuple | None], Candidate] = {
                (candidate.fact_id, candidate.claim_span): candidate for candidate in candidates}
            for candidate in fallback.candidates:
                key = (candidate.fact_id, candidate.claim_span)
                previous = merged.get(key)
                if previous is None or candidate.score > previous.score:
                    merged[key] = candidate
            candidates = sorted(merged.values(), key=lambda candidate: (
                -candidate.score, candidate.namespace != "scope_session", candidate.fact_id,
                candidate.claim_span or (-1, -1)))[:limit]
            fallback_used = any(candidate.namespace == "scope_fallback" for candidate in candidates)

        items = []
        rejections = 0
        for candidate in candidates[:limit]:
            item = self.verifier.verify(query, candidate)
            if item is None:
                rejections += 1
            else:
                items.append(item)
        if safety_policy is not None and any(
                item.content is not None and not screen_text(item.content, safety_policy).safe
                for item in items):
            trace = RouteTrace(query.query_id, self.generator.channel, limit,
                               len(candidates[:limit]), len(candidates[:limit]), rejections,
                               fallback_used, "unsafe_evidence")
            return RoutedResult(RouteState.ABSTAIN_UNSAFE_CONTENT,
                                EvidencePack.empty(query.query_id), trace)
        generation_id = (self.verifier.memory.get(query.scope_id, items[0].fact_id).generation_id
                         if items else None)
        pack = EvidencePack.build(query.query_id, items, generation_id=generation_id,
                                  recovery_reason="fallback" if fallback_used else
                                  ("bulk" if items else "cold-store"))
        state = RouteState.EVIDENCE if items else RouteState.ABSTENTION
        trace = RouteTrace(query.query_id, self.generator.channel, limit, len(candidates[:limit]),
                           len(candidates[:limit]), rejections, fallback_used,
                           "verified" if items else "no_verified_candidate")
        return RoutedResult(state, pack, trace)
