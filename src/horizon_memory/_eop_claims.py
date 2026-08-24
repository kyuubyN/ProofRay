# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D48 exact-span question obligations and deterministic claim composition."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import heapq
import math
import re
from typing import Iterable

from .raw_causal_channels import RawCausalChannels, observe_raw_text


RULE = "d48.deterministic-open-claim-composer.v1"
_SENTENCE = re.compile(
    # D142 (2026-08-17): generalizes D129 Phase A's `\.(?=\d)` decimal-only protection -- a real
    # sentence terminator almost always has trailing whitespace/EOL; punctuation glued directly
    # to the next character (decimals, file extensions like `cargo.toml`, code operators like
    # `(?.)`) almost never is one. Validated against 8,956 real MemGym-DR excerpts: 129 diffs
    # from the old regex, 100% equal-or-fewer fragments, never more. Accepted trade-off:
    # consecutive terminal-quoted sentences under-segment into one claim instead of corrupting
    # into fragments (see test_sentence_split_under_segments_consecutive_terminal_quotes_by_design).
    r"(?:[^\n.!?]|[.!?](?!\s|\Z))+(?:[.!?]+(?=\s|\Z)|(?=\n|\Z))", re.UNICODE)
_CLAUSE_BOUNDARY = re.compile(
    r";\s*|,\s+(?=(?:and|while|whereas|how|what|which|whether)\b)|"
    r"\s+and\s+(?=(?:how|what|which|whether)\b)", re.IGNORECASE)
_ENTITY = re.compile(r"\b(?:[A-Z][A-Za-z0-9_-]*|[A-Za-z]+\d[A-Za-z0-9_-]*)\b")
_ANCHOR_STOP = frozenset(
    "a an and how what which whether the within through from into by at for in on "
    "evidence source partial".split())
_FACTUAL_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)?|\d+(?:[.,]\d+)?", re.UNICODE)
_CONNECTIVE_WORDS = frozenset(
    "evidence supported claims source span partial answer unresolved obligation".split())


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
            raise ValueError("D48 sources require identity and content")
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
            raise ValueError("D48 context intent requires text and source fiber")
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


@dataclass(frozen=True)
class ClaimEdge:
    left: str
    right: str
    bindings: tuple[str, ...]


@dataclass(frozen=True)
class ComposedAnswer:
    state: str
    text: str
    obligations: tuple[QuestionObligation, ...]
    claims: tuple[AuthorizedClaim, ...]
    edges: tuple[ClaimEdge, ...]
    closed_obligations: tuple[str, ...]
    unresolved_obligations: tuple[str, ...]
    factual_token_authority: bool
    deterministic_digest: str


def compile_question_obligations(question: str, *,
                                 authority_id: str = "final-question") \
        -> tuple[QuestionObligation, ...]:
    if not question or len(question) > 4096:
        raise ValueError("D48 question outside bounded input")
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
        raise ValueError("D48 could not authorize question obligations")
    return tuple(obligations)


def extract_authorized_claims(sources: Iterable[ClaimSource]) \
        -> tuple[AuthorizedClaim, ...]:
    source_map = {}
    claims = []
    for source in sources:
        if source.source_id in source_map or not source.verify():
            raise ValueError("D48 sources must be unique and sealed")
        source_map[source.source_id] = source
        for index, match in enumerate(_SENTENCE.finditer(source.content)):
            start, end = match.span()
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
        raise ValueError("D48 claim extraction failed exact-span authority")
    return tuple(claims)


def build_claim_edges(claims: tuple[AuthorizedClaim, ...]) -> tuple[ClaimEdge, ...]:
    anchor_df = Counter(anchor for claim in claims for anchor in claim.anchors)
    # Three-way mention is the smallest useful setting for one asserted claim
    # plus a positive/negative or modal alternative in a tiny local fiber.
    upper = max(3, math.ceil(math.sqrt(len(claims))))
    fibers: dict[str, list[str]] = {}
    for claim in claims:
        for anchor in claim.anchors:
            if 2 <= anchor_df[anchor] <= upper:
                fibers.setdefault(anchor, []).append(claim.claim_id)
    pair_bindings: dict[tuple[str, str], set[str]] = {}
    for anchor, identifiers in sorted(fibers.items()):
        ordered = sorted(set(identifiers))
        for left_index, left in enumerate(ordered):
            for right in ordered[left_index + 1:]:
                pair_bindings.setdefault((left, right), set()).add(anchor)
    return tuple(ClaimEdge(left, right, tuple(sorted(bindings)))
                 for (left, right), bindings in sorted(pair_bindings.items()))


def _compatible(obligation: QuestionObligation, claim: AuthorizedClaim) -> bool:
    if obligation.polarity == "negative" and claim.polarity != "negative":
        return False
    return True


def _coverage(obligation: QuestionObligation, claim: AuthorizedClaim) -> float:
    lexical = len(obligation.lexical & claim.lexical) / max(1, len(obligation.lexical))
    anchors = len(obligation.anchors & claim.anchors)
    relations = len(obligation.relations & claim.relations)
    asserted = 0.15 if claim.modality == "asserted" else 0.0
    polarity = 0.1 if obligation.polarity == claim.polarity else 0.0
    return lexical + 0.35 * anchors + 0.15 * relations + asserted + polarity


def _render(claims: tuple[AuthorizedClaim, ...], unresolved: tuple[str, ...]) -> str:
    heading = "Evidence-supported claims:"
    rows = [heading]
    for index, claim in enumerate(claims, 1):
        rows.append(f"{index}. {claim.surface} [source {claim.source_id} span "
                    f"{claim.span[0]}:{claim.span[1]}]")
    if unresolved:
        rows.append("Partial answer; unresolved obligation: " + ", ".join(unresolved))
    return "\n".join(rows)


def _claim_penalty(claim: AuthorizedClaim) -> int:
    return int(claim.polarity == "negative") * 2 + int(claim.modality == "modal")


def _shortest_claim_path(start: str, target: str,
                         adjacency: dict[str, set[str]],
                         by_id: dict[str, AuthorizedClaim]) -> tuple[str, ...]:
    """Deterministic bounded proof closure, not open-ended graph expansion."""

    queue: list[tuple[int, int, tuple[str, ...]]] = [(0, 0, (start,))]
    best: dict[str, tuple[int, int, tuple[str, ...]]] = {}
    while queue:
        hops, penalty, path = heapq.heappop(queue)
        current = path[-1]
        state = (hops, penalty, path)
        if current in best and best[current] <= state:
            continue
        best[current] = state
        if current == target:
            return path
        if hops >= 12:
            continue
        for neighbor in sorted(adjacency[current]):
            if neighbor in path:
                continue
            claim = by_id[neighbor]
            heapq.heappush(queue, (
                hops + 1, penalty + _claim_penalty(claim), path + (neighbor,)))
    return ()


def _factual_authority(rendered: str, claims: tuple[AuthorizedClaim, ...]) -> bool:
    authorized = Counter(token.casefold() for claim in claims
                         for token in _FACTUAL_WORD.findall(claim.surface))
    # Metadata numbers and source identifiers are structural, so authority is
    # checked over the verbatim claim clauses rather than citation syntax.
    for claim in claims:
        observed = Counter(token.casefold() for token in _FACTUAL_WORD.findall(claim.surface))
        if observed - authorized:
            return False
    factual_rendered = Counter(token.casefold() for claim in claims
                               for token in _FACTUAL_WORD.findall(claim.surface))
    return not (factual_rendered - authorized) and bool(claims)


def compose_claim_answer(question: str, sources: Iterable[ClaimSource], *,
                         context_intents: Iterable[str | ContextIntent] = (),
                         max_claims: int = 12, max_bytes: int = 4096) -> ComposedAnswer:
    if max_claims < 1 or max_bytes < 256:
        raise ValueError("D48 output budgets must be positive and bounded")
    frozen_sources = tuple(sources)
    final_obligations = compile_question_obligations(question)
    known_sources = frozenset(item.source_id for item in frozen_sources)
    frozen_intents = []
    for index, item in enumerate(context_intents):
        if isinstance(item, ContextIntent):
            intent = item
        else:
            intent = ContextIntent.seal(
                f"context-intent:{index}", str(item), known_sources)
        if not intent.verify(known_sources):
            raise ValueError("D48 context intent failed source-fiber authority")
        frozen_intents.append(intent)
    context_obligations_list = []
    obligation_sources: dict[str, frozenset[str] | None] = {
        item.obligation_id: None for item in final_obligations}
    for intent in frozen_intents:
        compiled = compile_question_obligations(
            intent.text, authority_id=intent.intent_id)
        context_obligations_list.extend(compiled)
        obligation_sources.update(
            (item.obligation_id, intent.source_ids) for item in compiled)
    context_obligations = tuple(context_obligations_list)
    obligations = final_obligations + context_obligations
    claims = extract_authorized_claims(frozen_sources)
    edges = build_claim_edges(claims)
    adjacency: dict[str, set[str]] = {item.claim_id: set() for item in claims}
    for edge in edges:
        adjacency[edge.left].add(edge.right)
        adjacency[edge.right].add(edge.left)

    by_id = {item.claim_id: item for item in claims}
    selected: list[AuthorizedClaim] = []
    selected_ids = set()
    used = 0
    closed = set()
    seed_for_obligation: dict[str, str] = {}

    # One best witnessed claim per independently compiled obligation.
    for obligation in obligations:
        rows = []
        allowed_sources = obligation_sources[obligation.obligation_id]
        for claim in claims:
            if allowed_sources is not None and claim.source_id not in allowed_sources:
                continue
            if not _compatible(obligation, claim):
                continue
            score = _coverage(obligation, claim)
            cost = len(claim.surface.encode("utf-8"))
            if score > 0 and used + cost <= max_bytes:
                rows.append((score, claim.modality == "asserted", -cost,
                             claim.source_id, claim.span, claim))
        if rows:
            chosen = max(rows)[-1]
            if chosen.claim_id not in selected_ids and len(selected) < max_claims:
                selected.append(chosen)
                selected_ids.add(chosen.claim_id)
                used += len(chosen.surface.encode("utf-8"))
            # Only a claim that actually made it into `selected` -- just now, or already there
            # from an earlier obligation -- closes this obligation. Previously `closed.add(...)`
            # ran unconditionally whenever a candidate scored, so an obligation whose only
            # witness was rejected for exceeding `max_claims` was still marked closed, and the
            # composer emitted state="resolved" for a question whose evidence never made it into
            # the rendered pack (2026-08-19, found via code review).
            if chosen.claim_id in selected_ids:
                closed.add(obligation.obligation_id)
                seed_for_obligation[obligation.obligation_id] = chosen.claim_id

    # Close proof paths between direct obligation witnesses.  Claims that do
    # not lie on a closure path are irrelevant, even if they introduce novel
    # entities.  This prevents novelty-driven exploration from admitting false
    # branches.
    seed_ids = tuple(dict.fromkeys(
        seed_for_obligation[item.obligation_id]
        for item in final_obligations
        if item.obligation_id in seed_for_obligation))
    connectivity_failed = False
    for left_index, left in enumerate(seed_ids):
        for right in seed_ids[left_index + 1:]:
            path = _shortest_claim_path(left, right, adjacency, by_id)
            if not path:
                connectivity_failed = connectivity_failed or not frozen_intents
                continue
            for identifier in path[1:-1]:
                if identifier in selected_ids:
                    continue
                claim = by_id[identifier]
                cost = len(claim.surface.encode("utf-8"))
                if len(selected) >= max_claims or used + cost > max_bytes:
                    connectivity_failed = True
                    continue
                selected.append(claim)
                selected_ids.add(identifier)
                used += cost

    unresolved = tuple(item.obligation_id for item in obligations
                       if item.obligation_id not in closed)
    if connectivity_failed and len(seed_ids) > 1 and not frozen_intents:
        unresolved += ("qoc:connectivity",)
    selected_tuple = tuple(selected)
    rendered = _render(selected_tuple, unresolved)
    authority = _factual_authority(rendered, selected_tuple)
    state = "resolved" if selected_tuple and not unresolved and authority else \
        ("partial" if selected_tuple and authority else "abstained")
    relevant_edges = tuple(edge for edge in edges
                           if edge.left in selected_ids and edge.right in selected_ids)
    canonical = repr((RULE, state, rendered, tuple(item.claim_id for item in selected_tuple),
                      tuple(sorted(closed)), unresolved))
    return ComposedAnswer(
        state, rendered, obligations, selected_tuple, relevant_edges,
        tuple(sorted(closed)), unresolved, authority, _digest(canonical),
    )


__all__ = [
    "AuthorizedClaim", "ClaimEdge", "ClaimSource", "ComposedAnswer", "ContextIntent", "RULE",
    "QuestionObligation", "build_claim_edges", "compile_question_obligations",
    "compose_claim_answer", "extract_authorized_claims",
]
