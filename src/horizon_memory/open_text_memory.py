# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Universal lossless text ingestion feeding deterministic Horizon composition.

Open text first enters the sidecar as the weakest universally true statement available:
an exact source contains this exact document span.  No subject/relation semantics are invented.
The existing multilingual router/composer may then propose verified evidence; typed semantic
operators remain available only when a stronger adapter supplies stronger authority.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from dataclasses import dataclass

from .answer_engine import AnswerContextIntent, AnsweredClaim, AnsweredResult, HorizonAnswerEngine
from .causal_adapter_protocol import CausalAdapterBatch
from .engine_profile import EngineProfile
from .routing import CandidateGenerator, RouteDocument
from .claim_routing import claim_spans
from .materialized_proof_pressure_search import MaterializedIndependentHorizonSearchEngine
from .proof_pressure_search import HorizonSearchEngine
from .raw_causal_channels import RawCausalDocument
from .typed_causal_ingest import StructuredCausalDeclaration
from .typed_sidecar import (
    AuthorizedSidecarMemory, DeclarativeSidecarAdapter, SidecarAuthority,
    SidecarFactDeclaration, SidecarIngestReceipt, SidecarLifecycle,
)
from .durable_typed_sidecar import DurableAuthorizedSidecarMemory
from .english_atomic_relations import (
    EnglishAtomicRelationCompiler, EnglishAtomicRelationResult,
)
from .portuguese_atomic_relations import RoleReadResult
from .portuguese_atomic_relations import read as _read_pt_atomic_relation


OPEN_TEXT_SCHEMA_SHA256 = hashlib.sha256(
    b"horizon.open-text.surface-document.v1: exact source span only").hexdigest()

MEMGYM_REFERENCE_PROFILE = EngineProfile(
    name="memgym-reference-port-v1", claim_limit=8192,
    priority_aware_merge=True, answer_render_mode="full_dossier")


@dataclass(frozen=True)
class OpenTextEvidenceResult:
    state: str
    claims: tuple[AnsweredClaim, ...]
    proof_closed: bool
    residual: tuple[str, ...]
    evidence_bytes: int

    @property
    def text(self) -> str:
        return "\n".join(item.text for item in self.claims)


@dataclass(frozen=True)
class OpenTextAtomicRelationResult:
    fact_id: int
    source_id: str
    relation: EnglishAtomicRelationResult

    @property
    def proof_closed(self) -> bool:
        return self.relation.proof_closed


@dataclass(frozen=True)
class OpenTextAtomicRelationResultPT:
    fact_id: int
    source_id: str
    relation: RoleReadResult

    @property
    def proof_closed(self) -> bool:
        return self.relation.state == "resolved" and self.relation.answer is not None


class OpenTextHorizonMemory:
    """One bounded, source-verified open-text memory session."""

    def __init__(self, *, scope_id: int = 1, session_id: str = "s1",
                 purpose: str = "answer questions from remembered open text",
                 profile: EngineProfile | None = None,
                 candidate_generator: CandidateGenerator | None = None,
                 ledger_path: str | Path | None = None,
                 pt_lexicon=None):
        if scope_id < 0 or not session_id or not purpose:
            raise ValueError("open-text memory needs scope, session and purpose")
        self.scope_id = scope_id
        self.scope = str(scope_id)
        self.session_id = session_id
        self.authority = SidecarAuthority(
            "open-text-span-v1", "lossless-surface-document", 1,
            OPEN_TEXT_SCHEMA_SHA256, (self.scope,), ("surface_document",), purpose)
        self._sidecar = (AuthorizedSidecarMemory(self.scope, (self.authority,))
                         if ledger_path is None else
                         DurableAuthorizedSidecarMemory(
                             self.scope, Path(ledger_path), (self.authority,)))
        self._documents = self._documents_from_sidecar()
        self._context_intents: tuple[AnswerContextIntent, ...] = ()
        self._evidence_index = None
        self._turn_index = None
        self._english_atomic_compiler = None
        self._pt_lexicon = pt_lexicon
        self._engine = HorizonAnswerEngine(
            profile=profile or EngineProfile(name="open-text-default-v1"),
            scope_id=scope_id, session_id=session_id,
            candidate_generator=candidate_generator)

    def _documents_from_sidecar(self) -> tuple[RouteDocument, ...]:
        documents = []
        for item in self._sidecar.attested_facts():
            fact = item.fact
            if fact.predicate != "surface_document" or fact.scope != self.scope:
                raise RuntimeError("open-text ledger contains a non-document fact")
            documents.append(RouteDocument(
                fact.fact_id, fact.value, self.scope_id, self.session_id, fact.version,
                fact.subject, sequence=fact.observed_at, event_time=fact.event_time))
        return tuple(documents)

    def ingest_documents(self, documents: tuple[RouteDocument, ...], *,
                         context_intents: tuple[AnswerContextIntent, ...] = (),
                         bundle_id: str | None = None) -> SidecarIngestReceipt:
        if (not documents or tuple(document.fact_id for document in documents) !=
                tuple(sorted({document.fact_id for document in documents}))):
            raise ValueError("open-text documents must be non-empty and FactId-canonical")
        if any(document.scope_id != self.scope_id or document.session_id != self.session_id
               for document in documents):
            raise ValueError("open-text document scope/session differs from memory")
        known = {document.fact_id for document in documents}
        existing = {document.fact_id for document in self._documents}
        if known & existing:
            # An exact replay is handled below by the sidecar; a mixed/rebound bundle is rejected.
            by_id = {document.fact_id: document for document in self._documents}
            if any(by_id.get(document.fact_id) != document for document in documents):
                raise ValueError("open-text FactId collision is not an update")
        if any(set(intent.fact_ids) - known for intent in context_intents):
            raise ValueError("open-text intent references an unknown document")

        chunks = []
        declarations = []
        offset = 0
        for document in documents:
            if chunks:
                chunks.append("\n")
                offset += 1
            start = offset
            chunks.append(document.text)
            offset += len(document.text)
            clock = document.sequence if document.sequence is not None else document.fact_id
            event_time = document.event_time if document.event_time is not None else clock
            declaration = StructuredCausalDeclaration(
                document.fact_id, self.scope, document.source, "surface_document",
                document.text, (start, offset), clock, event_time,
                version=document.version,
                event_id=f"{document.source}:surface-document:{document.fact_id}")
            lifecycle = SidecarLifecycle(
                clock, None, self.authority.purpose, "open-text-host-ingest")
            declarations.append(SidecarFactDeclaration(declaration, lifecycle))
        content = "".join(chunks)
        if bundle_id is None:
            bundle_id = "open-text:" + hashlib.sha256(content.encode()).hexdigest()
        receipt = self._sidecar.ingest(
            DeclarativeSidecarAdapter(self.authority),
            CausalAdapterBatch(bundle_id, content, self.scope, tuple(declarations)))
        if receipt.state not in ("APPLIED", "IDEMPOTENT"):
            return receipt
        if any(not self._sidecar.verify_attestation(document.fact_id) for document in documents):
            raise RuntimeError("open-text sidecar publication failed post-commit verification")
        merged = {document.fact_id: document for document in self._documents}
        merged.update({document.fact_id: document for document in documents})
        self._documents = tuple(merged[fact_id] for fact_id in sorted(merged))
        self._evidence_index = None
        self._turn_index = None
        if context_intents:
            by_intent = {intent.intent_id: intent for intent in self._context_intents}
            # Intent IDs identify fibers; they are not clocks. Lexicographic sorting puts
            # ``session:10`` before ``session:2`` and destroys causal/session order, changing
            # deterministic dossier tie breaks. Preserve first insertion order; replacement
            # keeps the original position.
            intent_order = [intent.intent_id for intent in self._context_intents]
            for intent in context_intents:
                if intent.intent_id not in by_intent:
                    intent_order.append(intent.intent_id)
                by_intent[intent.intent_id] = intent
            self._context_intents = tuple(by_intent[key] for key in intent_order)
        return receipt

    def answer(self, question: str, *,
               context_intents: tuple[AnswerContextIntent, ...] | None = None) -> AnsweredResult:
        if not self._documents:
            raise RuntimeError("open-text memory has no ingested document bundle")
        if any(not self._sidecar.verify_attestation(document.fact_id)
               for document in self._documents):
            raise RuntimeError("open-text source authority failed revalidation")
        return self._engine.answer(
            question, self._documents,
            context_intents=self._context_intents if context_intents is None else context_intents)

    def answer_atomic_relation_en(self, question: str, *, fact_id: int) \
            -> OpenTextAtomicRelationResult:
        """Read one frozen EN atomic relation from one explicitly selected attested document.

        Source selection stays outside this reader: silently scanning every remembered document
        would turn missing compiler coverage into a false closed-world claim. The returned force
        distinguishes asserted candidates from questions, conditions, modality and negation.
        """
        matches = [document for document in self._documents if document.fact_id == fact_id]
        if len(matches) != 1:
            raise ValueError("EN atomic relation requires one known document FactId")
        document = matches[0]
        if not self._sidecar.verify_attestation(document.fact_id):
            raise RuntimeError("open-text source authority failed revalidation")
        if self._english_atomic_compiler is None:
            self._english_atomic_compiler = EnglishAtomicRelationCompiler()
        return OpenTextAtomicRelationResult(
            document.fact_id, document.source,
            self._english_atomic_compiler.read(document.text, question))

    def answer_atomic_relation_pt(self, question: str, *, fact_id: int) \
            -> OpenTextAtomicRelationResultPT:
        """Read one PT atomic relation from one explicitly selected attested document.

        Same source-selection discipline as `answer_atomic_relation_en`: silently scanning every
        remembered document would turn missing compiler coverage into a false closed-world claim.
        Uses the optional PortiLexicon-UD lexicon passed as `pt_lexicon` at construction time, if
        any -- omitting it preserves every result computed without it. Not yet confirmed against a
        genuinely fresh, never-touched holdout the way `answer_atomic_relation_en` was; see
        `portuguese_atomic_relations.py`'s own module docstring for the current promotion status.
        """
        matches = [document for document in self._documents if document.fact_id == fact_id]
        if len(matches) != 1:
            raise ValueError("PT atomic relation requires one known document FactId")
        document = matches[0]
        if not self._sidecar.verify_attestation(document.fact_id):
            raise RuntimeError("open-text source authority failed revalidation")
        return OpenTextAtomicRelationResultPT(
            document.fact_id, document.source,
            _read_pt_atomic_relation(document.text, question, lexicon=self._pt_lexicon))

    def retrieve_evidence(self, question: str, *, max_results: int = 5,
                          exploration_reserve: int | None = None) -> OpenTextEvidenceResult:
        """Return a bounded HPPS list of exact source spans for arbitrary-language text."""
        if not self._documents:
            raise RuntimeError("open-text memory has no ingested document bundle")
        if not question.strip() or max_results < 1:
            return OpenTextEvidenceResult("abstain", (), False,
                                          ("invalid_query_or_budget",), 0)
        if exploration_reserve is None:
            exploration_reserve = max_results
        if not 0 <= exploration_reserve <= max_results:
            raise ValueError("exploration reserve must be in [0,max_results]")
        if self._evidence_index is None:
            rows = []
            identity = 1
            for document in self._documents:
                if not self._sidecar.verify_attestation(document.fact_id):
                    raise RuntimeError("open-text source authority failed revalidation")
                for span in claim_spans(document.text):
                    start, end = span
                    surface = document.text[start:end]
                    rows.append((identity, document, span, surface))
                    identity += 1
            if rows:
                engine = MaterializedIndependentHorizonSearchEngine(tuple(
                    RawCausalDocument(identity, surface, 0, identity)
                    for identity, _document, _span, surface in rows))
                self._evidence_index = (tuple(rows), engine)
            else:
                self._evidence_index = ((), None)
        rows, engine = self._evidence_index
        if not rows:
            return OpenTextEvidenceResult("abstain", (), False, ("no_claim_spans",), 0)
        result = engine.search(
            question, max_results=max_results, exploration_reserve=exploration_reserve,
            core_width=1)
        by_id = {identity: (document, span, surface)
                 for identity, document, span, surface in rows}
        claims = tuple(AnsweredClaim(
            by_id[fact_id][2], by_id[fact_id][0].fact_id,
            f"{by_id[fact_id][0].source}:{by_id[fact_id][1]}", 0.0)
                       for fact_id in result.fact_ids)
        if any(claim.text not in next(document.text for document in self._documents
                                      if document.fact_id == claim.fact_id)
               for claim in claims):
            raise RuntimeError("open-text HPPS emitted a span outside its attested parent")
        text = "\n".join(claim.text for claim in claims)
        return OpenTextEvidenceResult(
            "evidence" if claims else "abstain", claims, result.proof_closed,
            result.residual, len(text.encode("utf-8")))

    def retrieve_turns(self, question: str, *, max_results: int = 32,
                       exploration_reserve: int = 32,
                       core_width: int = 2) -> OpenTextEvidenceResult:
        """Retrieve complete conversational turns while preserving session topology."""
        if not self._documents:
            raise RuntimeError("open-text memory has no ingested document bundle")
        if not question.strip() or max_results < 1:
            return OpenTextEvidenceResult("abstain", (), False, ("invalid_query_or_budget",), 0)
        if not 0 <= exploration_reserve <= max_results or core_width < 1:
            raise ValueError("invalid turn retrieval budget")
        if self._turn_index is None:
            session_ordinals = {}
            turn_positions = {}
            raw = []
            for document in self._documents:
                if not self._sidecar.verify_attestation(document.fact_id):
                    raise RuntimeError("open-text source authority failed revalidation")
                session = document.source
                session_index = session_ordinals.setdefault(session, len(session_ordinals))
                turn = turn_positions.get(session, 0)
                turn_positions[session] = turn + 1
                raw.append(RawCausalDocument(
                    document.fact_id, document.text, session_index, turn,
                    speaker=document.role or ""))
            self._turn_index = HorizonSearchEngine(
                tuple(raw), core_width=core_width, frontier_width=max(32, max_results))
        engine = self._turn_index
        result = engine.search(
            question, max_results=max_results, exploration_reserve=exploration_reserve,
            core_width=core_width)
        by_id = {document.fact_id: document for document in self._documents}
        claims = tuple(AnsweredClaim(
            by_id[fact_id].text, fact_id, by_id[fact_id].source, 0.0)
                       for fact_id in result.fact_ids)
        text = "\n".join(claim.text for claim in claims)
        return OpenTextEvidenceResult(
            "evidence" if claims else "abstain", claims, result.proof_closed,
            result.residual, len(text.encode("utf-8")))

    @property
    def fact_count(self) -> int:
        return self._sidecar.fact_count


__all__ = ["MEMGYM_REFERENCE_PROFILE", "OPEN_TEXT_SCHEMA_SHA256",
           "OpenTextAtomicRelationResult", "OpenTextAtomicRelationResultPT",
           "OpenTextEvidenceResult", "OpenTextHorizonMemory"]
