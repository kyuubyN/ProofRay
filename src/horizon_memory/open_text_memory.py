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
    SidecarObservedIntent, SidecarRouteMetadata,
)
from .durable_typed_sidecar import (
    AuthorizedSidecarRecordStore, DurableAuthorizedSidecarMemory,
)
from .durable_causal_memory import CausalDeleteReceipt
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
                 record_store: AuthorizedSidecarRecordStore | None = None,
                 pt_lexicon=None):
        if (scope_id < 0 or not session_id or not purpose
                or ledger_path is not None and record_store is not None):
            raise ValueError("open-text memory needs scope, session and purpose")
        self.scope_id = scope_id
        self.scope = str(scope_id)
        self.session_id = session_id
        self.authority = SidecarAuthority(
            "open-text-span-v1", "lossless-surface-document", 1,
            OPEN_TEXT_SCHEMA_SHA256, (self.scope,), ("surface_document",), purpose)
        self._sidecar = (
            AuthorizedSidecarMemory(self.scope, (self.authority,))
            if ledger_path is None and record_store is None else
            DurableAuthorizedSidecarMemory(
                self.scope, None if ledger_path is None else Path(ledger_path),
                (self.authority,), record_store=record_store))
        self._documents, self._context_intents = self._state_from_sidecar()
        self._evidence_index = None
        self._turn_index = None
        self._english_atomic_compiler = None
        self._pt_lexicon = pt_lexicon
        self._engine = HorizonAnswerEngine(
            profile=profile or EngineProfile(name="open-text-default-v1"),
            scope_id=scope_id, session_id=session_id,
            candidate_generator=candidate_generator, allow_scope_fallback=True,
            reuse_prepared_runtime=True)

    def _state_from_sidecar(self) \
            -> tuple[tuple[RouteDocument, ...], tuple[AnswerContextIntent, ...]]:
        documents = []
        intent_by_id: dict[str, SidecarObservedIntent] = {}
        occurrences: dict[str, set[int]] = {}
        for item in self._sidecar.attested_facts():
            fact = item.fact
            if fact.predicate != "surface_document" or fact.scope != self.scope:
                raise RuntimeError("open-text ledger contains a non-document fact")
            metadata = item.route_metadata
            if metadata is None:  # Legacy v1 ledger: preserve the historical reconstruction.
                documents.append(RouteDocument(
                    fact.fact_id, fact.value, self.scope_id, self.session_id, fact.version,
                    fact.subject, sequence=fact.observed_at, event_time=fact.event_time))
            else:
                if (metadata.scope_id != self.scope_id or metadata.version != fact.version
                        or (metadata.sequence is not None
                            and metadata.sequence != fact.observed_at)
                        or (metadata.event_time is not None
                            and metadata.event_time != fact.event_time)):
                    raise RuntimeError("open-text route metadata disagrees with attested fact")
                documents.append(RouteDocument(
                    fact.fact_id, fact.value, metadata.scope_id, metadata.session_id,
                    metadata.version, fact.subject, generation_id=metadata.generation_id,
                    sequence=metadata.sequence, span=metadata.span, role=metadata.role,
                    event_time=metadata.event_time, speaker=metadata.speaker))
                for intent in metadata.observed_intents:
                    if fact.fact_id not in intent.fact_ids:
                        raise RuntimeError("open-text intent is outside its FactId fiber")
                    prior = intent_by_id.get(intent.intent_id)
                    if prior is not None and prior != intent:
                        raise RuntimeError("open-text intent identity was rebound")
                    intent_by_id[intent.intent_id] = intent
                    occurrences.setdefault(intent.intent_id, set()).add(fact.fact_id)
        frozen_documents = tuple(documents)
        document_ids = {document.fact_id for document in frozen_documents}
        for intent_id, intent in intent_by_id.items():
            if (occurrences[intent_id] != set(intent.fact_ids)
                    or not set(intent.fact_ids) <= document_ids):
                raise RuntimeError("open-text intent fiber coverage is incomplete")
            sessions = {document.session_id for document in frozen_documents
                        if document.fact_id in intent.fact_ids}
            if len(sessions) != 1 or (
                    intent.session_id is not None and sessions != {intent.session_id}):
                raise RuntimeError("open-text intent session disagrees with its FactIds")
        ordered = tuple(sorted(intent_by_id.values(), key=lambda item: item.insertion_order))
        if tuple(item.insertion_order for item in ordered) != tuple(range(len(ordered))):
            raise RuntimeError("open-text intent insertion order is not canonical")
        intents = tuple(AnswerContextIntent(
            item.intent_id, item.text, item.fact_ids, item.turn_index, item.session_id)
                        for item in ordered)
        return frozen_documents, intents

    def ingest_documents(self, documents: tuple[RouteDocument, ...], *,
                         context_intents: tuple[AnswerContextIntent, ...] = (),
                         bundle_id: str | None = None) -> SidecarIngestReceipt:
        if (not documents or tuple(document.fact_id for document in documents) !=
                tuple(sorted({document.fact_id for document in documents}))):
            raise ValueError("open-text documents must be non-empty and FactId-canonical")
        if any(document.scope_id != self.scope_id for document in documents):
            raise ValueError("open-text document scope differs from memory")
        known = {document.fact_id for document in documents}
        existing = {document.fact_id for document in self._documents}
        if known & existing:
            # An exact replay is handled below by the sidecar; a mixed/rebound bundle is rejected.
            by_id = {document.fact_id: document for document in self._documents}
            if any(by_id.get(document.fact_id) != document for document in documents):
                raise ValueError("open-text FactId collision is not an update")
        if any(set(intent.fact_ids) - known for intent in context_intents):
            raise ValueError("open-text intent references an unknown document")
        documents_by_id = {document.fact_id: document for document in documents}
        for intent in context_intents:
            sessions = {documents_by_id[fact_id].session_id for fact_id in intent.fact_ids}
            if len(sessions) != 1 or (
                    intent.session_id is not None and sessions != {intent.session_id}):
                raise ValueError("open-text intent session differs from its documents")
        if len({intent.intent_id for intent in context_intents}) != len(context_intents):
            raise ValueError("open-text intent IDs must be unique per bundle")
        prior_intents = {intent.intent_id: intent for intent in self._context_intents}
        if any(intent.intent_id in prior_intents and prior_intents[intent.intent_id] != intent
               for intent in context_intents):
            raise ValueError("open-text intent identity cannot be rebound")
        existing_order = {intent.intent_id: index
                          for index, intent in enumerate(self._context_intents)}
        next_order = len(existing_order)
        observed: dict[str, SidecarObservedIntent] = {}
        for intent in context_intents:
            order = existing_order.get(intent.intent_id)
            if order is None:
                order = next_order
                next_order += 1
            observed[intent.intent_id] = SidecarObservedIntent(
                intent.intent_id, intent.text, intent.fact_ids, order,
                intent.turn_index, intent.session_id)

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
            metadata = SidecarRouteMetadata(
                document.scope_id, document.session_id, document.version,
                generation_id=document.generation_id, sequence=document.sequence,
                event_time=document.event_time, role=document.role, speaker=document.speaker,
                span=document.span, observed_intents=tuple(sorted(
                    (observed[intent.intent_id] for intent in context_intents
                     if document.fact_id in intent.fact_ids),
                    key=lambda item: item.intent_id)))
            declarations.append(SidecarFactDeclaration(declaration, lifecycle, metadata))
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
        # The engine cache is a disposable projection of the exact prior document snapshot.
        # Invalidate immediately after an attested publication; the sidecar remains the sole
        # authority and the next query rebuilds from the newly verified snapshot.
        self._engine.invalidate_prepared_runtime()
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

    def documents_snapshot(self) -> tuple[RouteDocument, ...]:
        """Return the immutable active routing snapshot, never the mutable index."""
        return self._documents

    def answer_excluding_sources(self, question: str,
                                 source_ids: tuple[str, ...]) -> AnsweredResult | None:
        """Answer while excluding exact route sources from the verified snapshot.

        This is an idempotent outbox boundary: a just-committed user turn can
        never answer the question that caused that same operation.
        """
        if (not question or source_ids != tuple(sorted(set(source_ids)))
                or any(not isinstance(item, str) or not item for item in source_ids)):
            raise ValueError("open-text exclusion needs a question and canonical sources")
        excluded = set(source_ids)
        documents = tuple(item for item in self._documents if item.source not in excluded)
        if not documents:
            return None
        if any(not self._sidecar.verify_attestation(item.fact_id) for item in documents):
            raise RuntimeError("open-text source authority failed revalidation")
        fact_ids = {item.fact_id for item in documents}
        intents = tuple(item for item in self._context_intents
                        if set(item.fact_ids) <= fact_ids)
        return self._engine.answer(question, documents, context_intents=intents)

    def purge_source(self, source_id: str) -> CausalDeleteReceipt:
        """Remove one RouteDocument source from durable authority.

        A source can span multiple FactIds.  The durable sidecar removes the
        complete matching set, rechains the ledger, revalidates the surviving
        field and commits it before this facade publishes the new snapshot.
        """
        if not source_id:
            raise ValueError("open-text purge requires an exact source identity")
        return self.purge_sources((source_id,))

    def purge_sources(self, source_ids: tuple[str, ...]) -> CausalDeleteReceipt:
        """Atomically remove a canonical set of RouteDocument sources."""
        if (not source_ids or source_ids != tuple(sorted(set(source_ids)))
                or any(not isinstance(item, str) or not item for item in source_ids)):
            raise ValueError("open-text purge requires canonical source identities")
        if not isinstance(self._sidecar, DurableAuthorizedSidecarMemory):
            raise RuntimeError("open-text purge requires a durable record store")
        selected = set(source_ids)
        fact_ids = tuple(sorted(
            document.fact_id for document in self._documents
            if document.source in selected))
        if not fact_ids:
            head = self._sidecar.ledger_head_sha256
            return CausalDeleteReceipt(
                "REJECTED_NOT_FOUND", source_ids[0], (), head, head,
                "route source is absent from open-text memory")
        purge_identity = source_ids[0] if len(source_ids) == 1 else \
            "source-set:" + hashlib.sha256("\x00".join(source_ids).encode()).hexdigest()
        receipt = self._sidecar.purge_fact_ids(fact_ids, source_id=purge_identity)
        if receipt.state != "PURGED":
            return receipt
        self._engine.invalidate_prepared_runtime()
        self._documents, self._context_intents = self._state_from_sidecar()
        self._evidence_index = None
        self._turn_index = None
        return receipt

    def purge_batch_source_prefix(self, prefix: str) -> CausalDeleteReceipt:
        """Remove durable import batches by their sealed batch-source prefix."""
        if not prefix:
            raise ValueError("open-text batch prefix is required")
        if not isinstance(self._sidecar, DurableAuthorizedSidecarMemory):
            raise RuntimeError("open-text purge requires a durable record store")
        fact_ids = tuple(sorted(
            item.fact.fact_id for item in self._sidecar.attested_facts()
            if item.fact.source_id.startswith(prefix)))
        if not fact_ids:
            head = self._sidecar.ledger_head_sha256
            return CausalDeleteReceipt(
                "REJECTED_NOT_FOUND", prefix, (), head, head,
                "batch source prefix is absent from open-text memory")
        receipt = self._sidecar.purge_fact_ids(fact_ids, source_id=prefix)
        if receipt.state == "PURGED":
            self._engine.invalidate_prepared_runtime()
            self._documents, self._context_intents = self._state_from_sidecar()
            self._evidence_index = None
            self._turn_index = None
        return receipt

    def update_document(self, replacement: RouteDocument) -> SidecarIngestReceipt:
        """Atomically replace one durable source version under the same FactId."""
        if not isinstance(self._sidecar, DurableAuthorizedSidecarMemory):
            raise RuntimeError("open-text update requires a durable record store")
        matches = [item for item in self._documents if item.fact_id == replacement.fact_id]
        if len(matches) != 1:
            raise ValueError("open-text update requires one existing FactId")
        previous = matches[0]
        if replacement == previous:
            return SidecarIngestReceipt(
                "IDEMPOTENT", self.authority.adapter_id,
                self.authority.authority_sha256, (replacement.fact_id,),
                hashlib.sha256(replacement.text.encode("utf-8")).hexdigest(),
                "replacement is already the active version")
        if (replacement.scope_id != self.scope_id or replacement.source != previous.source
                or replacement.session_id != previous.session_id
                or replacement.version <= previous.version
                or replacement.sequence != previous.sequence
                or replacement.role != previous.role or replacement.speaker != previous.speaker):
            raise ValueError("open-text update may change only content, version and event metadata")
        clock = replacement.sequence if replacement.sequence is not None else replacement.fact_id
        event_time = replacement.event_time if replacement.event_time is not None else clock
        declaration = StructuredCausalDeclaration(
            replacement.fact_id, self.scope, replacement.source, "surface_document",
            replacement.text, (0, len(replacement.text)), clock, event_time,
            version=replacement.version,
            event_id=f"{replacement.source}:surface-document:{replacement.fact_id}")
        lifecycle = SidecarLifecycle(
            clock, None, self.authority.purpose, "open-text-host-update")
        metadata = SidecarRouteMetadata(
            replacement.scope_id, replacement.session_id, replacement.version,
            generation_id=replacement.generation_id, sequence=replacement.sequence,
            event_time=replacement.event_time, role=replacement.role,
            speaker=replacement.speaker, span=replacement.span)
        bundle_id = "open-text-update:" + hashlib.sha256(
            (replacement.source + "\x00" + str(replacement.version) + "\x00" +
             replacement.text).encode("utf-8")).hexdigest()
        receipt = self._sidecar.replace_fact_ids(
            DeclarativeSidecarAdapter(self.authority),
            CausalAdapterBatch(bundle_id, replacement.text, self.scope, (
                SidecarFactDeclaration(declaration, lifecycle, metadata),)),
            (previous.fact_id,),
        )
        if receipt.state != "APPLIED":
            return receipt
        if not self._sidecar.verify_attestation(replacement.fact_id):
            raise RuntimeError("open-text update failed post-commit verification")
        self._engine.invalidate_prepared_runtime()
        self._documents, self._context_intents = self._state_from_sidecar()
        self._evidence_index = None
        self._turn_index = None
        return receipt

    def upsert_documents(self, documents: tuple[RouteDocument, ...], *,
                         bundle_id: str | None = None) -> SidecarIngestReceipt:
        """Atomically publish a canonical connector batch of inserts and updates.

        Existing FactIds are removed and reintroduced in the same durable
        replacement transaction. Exact retry rows are allowed; changed rows
        must preserve source topology and strictly increase their version.
        Observed-intent fibers are intentionally excluded because replacing one
        of their members without recompiling the full intent would weaken its
        authority.
        """
        if (not documents or tuple(document.fact_id for document in documents) !=
                tuple(sorted({document.fact_id for document in documents}))):
            raise ValueError("open-text upsert documents must be FactId-canonical")
        if any(document.scope_id != self.scope_id for document in documents):
            raise ValueError("open-text upsert document scope differs from memory")
        existing = {document.fact_id: document for document in self._documents}
        existing_ids = tuple(document.fact_id for document in documents
                             if document.fact_id in existing)
        if not existing_ids:
            return self.ingest_documents(documents, bundle_id=bundle_id)
        intent_fact_ids = {fact_id for intent in self._context_intents
                           for fact_id in intent.fact_ids}
        if set(existing_ids) & intent_fact_ids:
            raise ValueError("open-text upsert cannot replace an observed-intent fact")
        if all(existing.get(document.fact_id) == document for document in documents):
            content = "\n".join(document.text for document in documents)
            return SidecarIngestReceipt(
                "IDEMPOTENT", self.authority.adapter_id,
                self.authority.authority_sha256,
                tuple(document.fact_id for document in documents),
                hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "upsert batch is already active")
        for replacement in documents:
            previous = existing.get(replacement.fact_id)
            if previous is None or previous == replacement:
                continue
            if replacement.source != previous.source:
                raise ValueError("open-text upsert source identity was rebound")
            if (replacement.session_id != previous.session_id
                    or replacement.version <= previous.version
                    or replacement.sequence != previous.sequence
                    or replacement.role != previous.role
                    or replacement.speaker != previous.speaker):
                raise ValueError(
                    "open-text upsert may change only content, version and event metadata")
        if not isinstance(self._sidecar, DurableAuthorizedSidecarMemory):
            raise RuntimeError("open-text upsert requires a durable record store")

        chunks: list[str] = []
        declarations: list[SidecarFactDeclaration] = []
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
                clock, None, self.authority.purpose, "open-text-host-upsert")
            metadata = SidecarRouteMetadata(
                document.scope_id, document.session_id, document.version,
                generation_id=document.generation_id, sequence=document.sequence,
                event_time=document.event_time, role=document.role,
                speaker=document.speaker, span=document.span)
            declarations.append(SidecarFactDeclaration(
                declaration, lifecycle, metadata))
        content = "".join(chunks)
        if bundle_id is None:
            bundle_id = "open-text-upsert:" + hashlib.sha256(
                "\x00".join(
                    f"{item.fact_id}:{item.source}:{item.version}:{item.text}"
                    for item in documents).encode("utf-8")).hexdigest()
        receipt = self._sidecar.replace_fact_ids(
            DeclarativeSidecarAdapter(self.authority),
            CausalAdapterBatch(
                bundle_id, content, self.scope, tuple(declarations)),
            existing_ids)
        if receipt.state != "APPLIED":
            return receipt
        if any(not self._sidecar.verify_attestation(document.fact_id)
               for document in documents):
            raise RuntimeError("open-text upsert failed post-commit verification")
        self._engine.invalidate_prepared_runtime()
        self._documents, self._context_intents = self._state_from_sidecar()
        self._evidence_index = None
        self._turn_index = None
        return receipt

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
                session = document.session_id
                session_index = session_ordinals.setdefault(session, len(session_ordinals))
                turn = (document.sequence if document.sequence is not None
                        else turn_positions.get(session, 0))
                turn_positions[session] = max(turn_positions.get(session, 0), turn + 1)
                raw.append(RawCausalDocument(
                    document.fact_id, document.text, session_index, turn,
                    speaker=document.speaker or ""))
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
