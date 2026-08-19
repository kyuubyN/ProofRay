# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D48 exact-span question obligations and claim extraction -- promoted to core (2026-08-18).

This is the foundation `proof_dossier.py`/`lossless_proof_answer.py` build on: given the
already-verified evidence a caller obtained through `SemanticRouter`/`EvidencePack` (routing,
identity, provenance -- the storage core's own guarantee), seal it into `ClaimSource`s and split
it into `AuthorizedClaim`s, each an exact, re-verifiable substring of its own sealed source. This
module never touches durable storage itself -- it is a pure, deterministic transform over
already-verified text.

Ported from the private research line's `lab/deterministic_claim_composer.py` (D48), the
foundation under the D103-D142 MemGym-DR result line documented in this project's own research
notes. Only the extraction half is promoted here -- `compose_claim_answer` (D48's own
connectivity/proof-path composer) is NOT ported: later research (`build_proof_dossier`/D49, in
`proof_dossier.py`) found a submodular budget-fill composer over the same claims decisively
outperforms it, so `compose_claim_answer` would be dead weight in the stable surface. Reuses
`claim_routing.claim_spans` for sentence segmentation instead of a second copy of the same regex.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import re
from typing import Iterable

from .claim_routing import claim_spans
from .raw_causal_channels import RawCausalChannels, observe_raw_text


RULE = "d48.claim-composer.v1"
_ENTITY = re.compile(r"\b(?:[A-Z][A-Za-z0-9_-]*|[A-Za-z]+\d[A-Za-z0-9_-]*)\b")
_ANCHOR_STOP = frozenset(
    "a an and how what which whether the within through from into by at for in on "
    "evidence source partial".split())
_CLAUSE_BOUNDARY = re.compile(
    r";\s*|,\s+(?=(?:and|while|whereas|how|what|which|whether)\b)|"
    r"\s+and\s+(?=(?:how|what|which|whether)\b)", re.IGNORECASE)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _tokens(channels: RawCausalChannels) -> frozenset[str]:
    return frozenset(channels.lexical)


def _anchors(text: str, channels: RawCausalChannels) -> frozenset[str]:
    values = {item.casefold() for item in _ENTITY.findall(text)
              if item.casefold() not in _ANCHOR_STOP}
    values.update(channels.entities)
    values.update(channels.numbers)
    return frozenset(values)


@dataclass(frozen=True)
class ClaimSource:
    source_id: str
    content: str
    sha256: str

    @classmethod
    def seal(cls, source_id: str, content: str) -> "ClaimSource":
        if not source_id or not content:
            raise ValueError("claim sources require identity and content")
        return cls(source_id, content, _digest(content))

    def verify(self) -> bool:
        return bool(self.source_id and self.content and self.sha256 == _digest(self.content))


@dataclass(frozen=True)
class ContextIntent:
    intent_id: str
    text: str
    source_ids: frozenset[str]
    sha256: str

    @classmethod
    def seal(cls, intent_id: str, text: str,
             source_ids: Iterable[str]) -> "ContextIntent":
        frozen = frozenset(str(item) for item in source_ids)
        if not intent_id or not text or not frozen:
            raise ValueError("context intent requires text and a non-empty source fiber")
        return cls(intent_id, text, frozen, _digest(text))

    def verify(self, known_sources: frozenset[str]) -> bool:
        return bool(self.intent_id and self.text and self.sha256 == _digest(self.text)
                    and self.source_ids and self.source_ids <= known_sources)


@dataclass(frozen=True)
class QuestionObligation:
    obligation_id: str
    authority_sha256: str
    span: tuple[int, int]
    surface: str
    lexical: frozenset[str]
    anchors: frozenset[str]
    relations: frozenset[str]
    polarity: str
    modality: str

    def verify(self, question: str) -> bool:
        start, end = self.span
        return self.authority_sha256 == _digest(question) and \
            0 <= start < end <= len(question) and question[start:end] == self.surface


@dataclass(frozen=True)
class AuthorizedClaim:
    claim_id: str
    source_id: str
    source_sha256: str
    span: tuple[int, int]
    surface: str
    lexical: frozenset[str]
    anchors: frozenset[str]
    relations: frozenset[str]
    polarity: str
    modality: str

    def verify(self, sources: dict[str, ClaimSource]) -> bool:
        source = sources.get(self.source_id)
        if source is None or not source.verify() or source.sha256 != self.source_sha256:
            return False
        start, end = self.span
        return 0 <= start < end <= len(source.content) and \
            source.content[start:end] == self.surface


def compile_question_obligations(question: str, *,
                                 authority_id: str = "final-question") \
        -> tuple[QuestionObligation, ...]:
    if not question or len(question) > 4096:
        raise ValueError("question outside bounded input")
    boundaries = [0]
    for match in _CLAUSE_BOUNDARY.finditer(question):
        boundaries.extend((match.start(), match.end()))
    boundaries.append(len(question))
    segments = []
    for left, right in zip(boundaries[::2], boundaries[1::2]):
        start, end = left, right
        while start < end and question[start].isspace():
            start += 1
        while end > start and question[end - 1].isspace():
            end -= 1
        if start < end:
            segments.append((start, end))
    if not segments:
        segments = [(0, len(question))]

    obligations = []
    for index, (start, end) in enumerate(segments):
        surface = question[start:end]
        channels = observe_raw_text(surface, question=True)
        lexical = _tokens(channels)
        if not lexical:
            continue
        obligations.append(QuestionObligation(
            f"{authority_id}:qoc:{index}", _digest(question),
            (start, end), surface, lexical,
            _anchors(surface, channels), frozenset(channels.relations),
            channels.polarity, channels.modality,
        ))
    if not obligations or any(not item.verify(question) for item in obligations):
        raise ValueError("could not authorize question obligations")
    return tuple(obligations)


def extract_authorized_claims(sources: Iterable[ClaimSource]) \
        -> tuple[AuthorizedClaim, ...]:
    source_map = {}
    claims = []
    for source in sources:
        if source.source_id in source_map or not source.verify():
            raise ValueError("claim sources must be unique and sealed")
        source_map[source.source_id] = source
        for index, (start, end) in enumerate(claim_spans(source.content)):
            while start < end and source.content[start].isspace():
                start += 1
            while end > start and source.content[end - 1].isspace():
                end -= 1
            if end - start < 12:
                continue
            surface = source.content[start:end]
            channels = observe_raw_text(surface)
            if not channels.lexical:
                continue
            claims.append(AuthorizedClaim(
                f"{source.source_id}:claim:{index}", source.source_id, source.sha256,
                (start, end), surface, _tokens(channels), _anchors(surface, channels),
                frozenset(channels.relations), channels.polarity, channels.modality,
            ))
    if not claims or any(not item.verify(source_map) for item in claims):
        raise ValueError("claim extraction failed exact-span authority")
    return tuple(claims)


__all__ = [
    "AuthorizedClaim", "ClaimSource", "ContextIntent", "RULE",
    "QuestionObligation", "compile_question_obligations", "extract_authorized_claims",
]
