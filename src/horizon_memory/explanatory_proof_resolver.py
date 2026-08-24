# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Context-bound adapter for the explanatory-obligation proof kernel.

The resolver is intentionally opt-in.  It can release only the smallest exact source packet whose
visible per-turn obligations, witnessed bridges and final join all close.  It never treats ranking,
benchmark labels, or an LLM response as authority.  Unsupported, contested and over-budget worlds
return ``None`` and therefore preserve the engine's byte-identical evidence fallback.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from .answer_engine import (
    AnswerContextIntent, AnsweredClaim, DirectAnswerResolution,
    ContextualDirectAnswerResolver,
)
from .explanatory_obligation_proof import (
    ExplanatoryIntent, ExplanatoryProofCertificate, ExplanatoryProofConfig,
    ExplanatoryProofResult, ExplanatorySource, solve_explanatory_obligations,
)
from .proof_convergent_resolver import ProofConvergentResolver


_RULE = "horizon.explanatory-proof-resolver.v1"


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _evidence_digest(evidence: tuple[AnsweredClaim, ...]) -> str:
    rows = [{
        "fact_id": item.fact_id,
        "source_id": item.source_id,
        "text_sha256": hashlib.sha256(item.text.encode("utf-8")).hexdigest(),
        "role": item.role,
        "session_id": item.session_id,
        "speaker": item.speaker,
        "sequence": item.sequence,
        "event_time": item.event_time,
        "scope_id": item.scope_id,
        "version": item.version,
        "source_span": item.source_span,
        "parent_sha256": item.parent_sha256,
        "generation_id": item.generation_id,
    } for item in evidence]
    return hashlib.sha256(
        b"HORIZON-EOP-AUTHORITY-v1\0" + _canonical(rows)).hexdigest()


def _intent_digest(intents: tuple[AnswerContextIntent, ...]) -> str:
    return hashlib.sha256(b"HORIZON-EOP-INTENTS-v1\0" + _canonical([{
        "intent_id": item.intent_id,
        "text": item.text,
        "fact_ids": item.fact_ids,
        "turn_index": item.turn_index,
        "session_id": item.session_id,
    } for item in intents])).hexdigest()


def _context_evidence(
        evidence: tuple[AnsweredClaim, ...],
        context_intents: tuple[AnswerContextIntent, ...]) -> tuple[AnsweredClaim, ...]:
    """Select exactly the caller-declared fibers; unrelated history is not proof authority."""
    fact_ids = frozenset(
        fact_id for intent in context_intents for fact_id in intent.fact_ids)
    return tuple(item for item in evidence if item.fact_id in fact_ids)


def _project(
        evidence: tuple[AnsweredClaim, ...],
        context_intents: tuple[AnswerContextIntent, ...],
        authoritative_roles: tuple[str, ...],
) -> tuple[tuple[ExplanatoryIntent, ...], tuple[ExplanatorySource, ...]] | None:
    """Project only explicit FactId-bound turn fibers; never infer a missing turn or owner."""
    if not evidence or not context_intents or any(
            item.turn_index is None for item in context_intents):
        return None
    by_fact: dict[int, AnsweredClaim] = {}
    for item in evidence:
        if item.fact_id in by_fact or not item.source_id or not item.text.strip():
            return None
        by_fact[item.fact_id] = item
    known_roles = frozenset(authoritative_roles)
    used_facts: set[int] = set()
    projected_intents = []
    projected_sources = []
    seen_source_ids: set[str] = set()
    for intent in sorted(context_intents, key=lambda item: (
            item.turn_index, item.intent_id)):
        if any(fact_id in used_facts for fact_id in intent.fact_ids):
            return None
        claims = tuple(by_fact.get(fact_id) for fact_id in intent.fact_ids)
        if any(item is None for item in claims):
            return None
        source_ids = []
        for claim in claims:
            assert claim is not None
            role = claim.role or "document"
            if role not in known_roles or claim.source_id in seen_source_ids:
                return None
            session_id = intent.session_id or claim.session_id
            if not session_id or (intent.session_id is not None
                                  and claim.session_id is not None
                                  and intent.session_id != claim.session_id):
                return None
            parent = claim.parent_sha256 or hashlib.sha256(
                claim.text.encode("utf-8")).hexdigest()
            projected_sources.append(ExplanatorySource.seal(
                claim.source_id, claim.text, turn_index=intent.turn_index,
                session_id=session_id, source_role=role,
                root_id=f"fact:{claim.fact_id}:{parent}"))
            source_ids.append(claim.source_id)
            seen_source_ids.add(claim.source_id)
        used_facts.update(intent.fact_ids)
        projected_intents.append(ExplanatoryIntent.seal(
            intent.intent_id, intent.text, turn_index=intent.turn_index,
            source_ids=source_ids))
    return tuple(projected_intents), tuple(projected_sources)


def _solve(
        question: str, evidence: tuple[AnsweredClaim, ...],
        context_intents: tuple[AnswerContextIntent, ...],
        config: ExplanatoryProofConfig, authoritative_roles: tuple[str, ...],
) -> tuple[ExplanatoryProofResult, tuple[ExplanatoryIntent, ...],
           tuple[ExplanatorySource, ...]] | None:
    projected = _project(evidence, context_intents, authoritative_roles)
    if projected is None:
        return None
    intents, sources = projected
    result = solve_explanatory_obligations(
        question=question, intents=intents, sources=sources, config=config)
    return result, intents, sources


@dataclass(frozen=True, slots=True)
class ExplanatoryDirectAnswerCertificate:
    """Compact contextual certificate reopened by rerunning the finite EOP kernel."""

    proof: ExplanatoryProofCertificate
    evidence_sha256: str
    intents_sha256: str
    config: ExplanatoryProofConfig = ExplanatoryProofConfig()
    authoritative_roles: tuple[str, ...] = ("user", "document")

    def compact(self) -> bytes:
        return _canonical({
            "rule": _RULE,
            "proof": json.loads(self.proof.compact()),
            "evidence_sha256": self.evidence_sha256,
            "intents_sha256": self.intents_sha256,
            "config": self.config.__dict__,
            "authoritative_roles": self.authoritative_roles,
        })

    def reopen(self, blob: bytes, question: str,
               evidence: tuple[AnsweredClaim, ...]) -> bool:
        # A contextual proof without its observed turn intents has incomplete authority.
        return False

    def reopen_resolution(
            self, blob: bytes, question: str, evidence: tuple[AnsweredClaim, ...], *,
            text: str, method: str, source_ids: tuple[str, ...]) -> bool:
        return False

    def reopen_contextual(
            self, blob: bytes, question: str, evidence: tuple[AnsweredClaim, ...],
            context_intents: tuple[AnswerContextIntent, ...]) -> bool:
        try:
            if blob != self.compact() or len(blob) > 65_536:
                return False
            solved = _solve(
                question, evidence, context_intents, self.config,
                self.authoritative_roles)
            if solved is None:
                return False
            result, intents, sources = solved
            return (result.state == "resolved" and result.certificate == self.proof
                    and self.evidence_sha256 == _evidence_digest(
                        _context_evidence(evidence, context_intents))
                    and self.intents_sha256 == _intent_digest(context_intents)
                    and self.proof.reopen(
                        question=question, intents=intents, sources=sources,
                        config=self.config))
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
            return False

    def reopen_contextual_resolution(
            self, blob: bytes, question: str, evidence: tuple[AnsweredClaim, ...],
            context_intents: tuple[AnswerContextIntent, ...], *, text: str,
            method: str, source_ids: tuple[str, ...]) -> bool:
        if method != "explanatory_proof" or not self.reopen_contextual(
                blob, question, evidence, context_intents):
            return False
        solved = _solve(
            question, evidence, context_intents, self.config,
            self.authoritative_roles)
        if solved is None:
            return False
        result, _intents, _sources = solved
        expected_sources = tuple(dict.fromkeys(
            item.source_id for item in result.bindings))
        return text == result.text and source_ids == expected_sources


@dataclass(frozen=True, slots=True)
class ExplanatoryProofResolver(ContextualDirectAnswerResolver):
    """Resolve only complete, uncontested, source-relative explanatory proof DAGs."""

    config: ExplanatoryProofConfig = ExplanatoryProofConfig()
    authoritative_roles: tuple[str, ...] = ("user", "document")

    def __post_init__(self) -> None:
        if not isinstance(self.config, ExplanatoryProofConfig):
            raise TypeError("config must be ExplanatoryProofConfig")
        if (not self.authoritative_roles or any(
                not isinstance(item, str) or not item
                for item in self.authoritative_roles)):
            raise ValueError("authoritative_roles must be non-empty strings")

    def resolve_contextual(
            self, question: str, evidence: tuple[AnsweredClaim, ...],
            context_intents: tuple[AnswerContextIntent, ...]) \
            -> DirectAnswerResolution | None:
        if not question.strip():
            return None
        solved = _solve(
            question, evidence, context_intents, self.config,
            self.authoritative_roles)
        if solved is None:
            return None
        result, intents, sources = solved
        if result.state != "resolved" or not result.text:
            return None
        source_ids = tuple(dict.fromkeys(
            item.source_id for item in result.bindings))
        if not source_ids:
            return None
        certificate = ExplanatoryDirectAnswerCertificate(
            result.certificate, _evidence_digest(
                _context_evidence(evidence, context_intents)),
            _intent_digest(context_intents), self.config, self.authoritative_roles)
        blob = certificate.compact()
        if not certificate.reopen_contextual(blob, question, evidence, context_intents):
            return None
        if not result.certificate.reopen(
                question=question, intents=intents, sources=sources, config=self.config):
            return None
        return DirectAnswerResolution(
            result.text, "explanatory_proof", source_ids, certificate)


@dataclass(frozen=True, slots=True)
class ProofCascadeResolver:
    """Finite scalar-first cascade with EOP as the contextual, fail-closed second layer."""

    scalar: ProofConvergentResolver = ProofConvergentResolver()
    explanatory: ExplanatoryProofResolver = ExplanatoryProofResolver()

    def __post_init__(self) -> None:
        if not isinstance(self.scalar, ProofConvergentResolver):
            raise TypeError("scalar must be ProofConvergentResolver")
        if not isinstance(self.explanatory, ExplanatoryProofResolver):
            raise TypeError("explanatory must be ExplanatoryProofResolver")

    def resolve(self, question: str,
                evidence: tuple[AnsweredClaim, ...]) -> DirectAnswerResolution | None:
        return self.scalar.resolve(question, evidence)

    def resolve_contextual(
            self, question: str, evidence: tuple[AnsweredClaim, ...],
            context_intents: tuple[AnswerContextIntent, ...]) \
            -> DirectAnswerResolution | None:
        scalar = self.scalar.resolve(question, evidence)
        if scalar is not None:
            return scalar
        return self.explanatory.resolve_contextual(
            question, evidence, context_intents)


__all__ = [
    "ExplanatoryDirectAnswerCertificate", "ExplanatoryProofResolver",
    "ProofCascadeResolver",
]
