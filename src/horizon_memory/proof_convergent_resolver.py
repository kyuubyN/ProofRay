# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Public proof-convergent direct-answer adapter.

The adapter projects the engine's already verified evidence into the finite executor.  It
never treats a relevance score or benchmark label as authority.  A resolved value is released
only after its compact proof reopens against the same source text and question; unsupported,
absent or contested worlds return ``None`` so the ordinary evidence answer remains available.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import struct

from .answer_engine import (
    AnsweredClaim, DirectAnswerResolution, DirectAnswerResolver,
)
from .proof_convergent_executor import (
    AttestedScalarLedger, compact_scalar_answer, open_compact_scalar_answer,
    render_convergent_answer,
)


_MAGIC = b"HCR1"


@dataclass(frozen=True, slots=True)
class _AuthorityDocument:
    fact_id: int
    text: str
    source: str
    role: str = "authority"


def _documents(evidence: tuple[AnsweredClaim, ...]) -> tuple[_AuthorityDocument, ...]:
    # Local FactIds are intentionally positional: one routed document may contribute several
    # separately sealed claims, and the proof must be able to cite each exact claim independently.
    return tuple(_AuthorityDocument(
        index, claim.text, claim.source_id, claim.role or "document")
                 for index, claim in enumerate(evidence, 1))


def _ledger(evidence: tuple[AnsweredClaim, ...],
            authoritative_roles: tuple[str, ...]) -> AttestedScalarLedger:
    sessions = tuple(sorted({claim.session_id for claim in evidence if claim.session_id}))
    session_group = {session_id: index for index, session_id in enumerate(sessions, 1)}
    fact_groups = {
        index: session_group[claim.session_id]
        for index, claim in enumerate(evidence, 1) if claim.session_id in session_group
    }
    return AttestedScalarLedger.build(
        _documents(evidence), authoritative_roles=authoritative_roles,
        fact_groups=fact_groups or None)


@dataclass(frozen=True, slots=True)
class ProofConvergentCertificate:
    """Question-bound compact proof reopened against the caller's verified evidence."""

    proof_blob: bytes
    authoritative_roles: tuple[str, ...] = ("user", "document")

    def __post_init__(self) -> None:
        if not isinstance(self.proof_blob, bytes) or not self.proof_blob:
            raise ValueError("proof_blob must be non-empty bytes")
        if (not self.authoritative_roles or
                any(not isinstance(role, str) or not role for role in self.authoritative_roles)):
            raise ValueError("authoritative_roles must contain non-empty strings")

    def compact(self) -> bytes:
        return self.proof_blob

    def reopen(self, blob: bytes, question: str,
               evidence: tuple[AnsweredClaim, ...]) -> bool:
        try:
            if not isinstance(blob, bytes) or len(blob) < 40 or blob[:4] != _MAGIC:
                return False
            question_digest = blob[4:36]
            proof_size = struct.unpack(">I", blob[36:40])[0]
            if question_digest != hashlib.sha256(question.encode("utf-8")).digest():
                return False
            if proof_size == 0 or 40 + proof_size != len(blob):
                return False
            compact = blob[40:]
            ledger = _ledger(evidence, self.authoritative_roles)
            opened_value, opened_unit, _citations = open_compact_scalar_answer(compact, ledger)
            rerun = ledger.answer_convergent(question)
            if rerun.state != "resolved" or rerun.value is None:
                return False
            if (str(rerun.value), rerun.unit) != (opened_value, opened_unit):
                return False
            return compact_scalar_answer(rerun, ledger) == compact
        except (AttributeError, TypeError, ValueError, struct.error):
            return False


@dataclass(frozen=True, slots=True)
class ProofConvergentResolver(DirectAnswerResolver):
    """Resolve only converged finite operator worlds; otherwise leave the cascade untouched."""

    authoritative_roles: tuple[str, ...] = ("user", "document")

    def __post_init__(self) -> None:
        if (not self.authoritative_roles or
                any(not isinstance(role, str) or not role for role in self.authoritative_roles)):
            raise ValueError("authoritative_roles must contain non-empty strings")

    def resolve(self, question: str,
                evidence: tuple[AnsweredClaim, ...]) -> DirectAnswerResolution | None:
        if not question.strip() or not evidence:
            return None
        ledger = _ledger(evidence, self.authoritative_roles)
        answer = ledger.answer_convergent(question)
        if answer.state != "resolved" or answer.value is None:
            return None
        compact = compact_scalar_answer(answer, ledger)
        envelope = (_MAGIC + hashlib.sha256(question.encode("utf-8")).digest() +
                    struct.pack(">I", len(compact)) + compact)
        certificate = ProofConvergentCertificate(envelope, self.authoritative_roles)
        if not certificate.reopen(envelope, question, evidence):
            return None
        cited_fact_ids = tuple(dict.fromkeys(
            fact_id for world in answer.worlds for fact_id, _start, _end in world.spans))
        if cited_fact_ids:
            source_ids = tuple(evidence[fact_id - 1].source_id for fact_id in cited_fact_ids)
        else:
            # Corpus-nonmembership is bound to the complete authority-corpus digest rather than
            # an individual positive span, so every participating source is exposed to callers.
            source_ids = tuple(dict.fromkeys(claim.source_id for claim in evidence))
        if not source_ids:
            return None
        return DirectAnswerResolution(
            render_convergent_answer(answer), "proof_convergent", source_ids, certificate)


__all__ = ["ProofConvergentCertificate", "ProofConvergentResolver"]
