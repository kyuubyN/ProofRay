# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bounded parser-neutral predicate/role sketches over D48 authorized spans.

The sketch deliberately avoids POS taggers, grammars, embeddings and generated text.  It
retains only conserved observables: stemmed token order, short directed skip relations,
morphological predicate carriers, typed slots and the exact D48 claim/obligation identity
that authorized the surface.
"""
from __future__ import annotations

from dataclasses import dataclass

from .raw_causal_channels import observe_raw_text
from ._eop_claims import AuthorizedClaim, QuestionObligation


_PREDICATE_WORDS = frozenset("""
achieve adapt add allow apply avoid become break build combine compare compute connect
create decrease derive detect distribute enable ensure estimate evaluate extend generate
handle improve increase introduce learn maintain match measure model optimize predict
process provide reduce refine represent require resolve scale select support train update use
accelerate augmentation benefit change complexity contraction control difference efficiency
enhancement framework mechanism method optimization performance precision reconstruction
refinement relation strategy speed synthesis technique training transport
""".split())
_PREDICATE_SUFFIXES = (
    "ate", "fy", "ise", "ize", "ing", "tion", "ment", "ance", "ence", "ivity",
)
_PREDICATE_ROOTS = tuple(sorted({
    "achiev", "adapt", "allow", "appl", "avoid", "break", "build", "combin",
    "compar", "comput", "connect", "creat", "decreas", "deriv", "detect",
    "distribut", "enabl", "ensur", "estimat", "evaluat", "extend", "generat",
    "handl", "improv", "increas", "introduc", "learn", "maintain", "match",
    "measur", "model", "optimiz", "predict", "process", "provid", "reduc",
    "refin", "represent", "requir", "resolv", "scal", "select", "support",
    "train", "transport", "updat",
}))


@dataclass(frozen=True)
class RoleSketch:
    lexical: tuple[str, ...]
    predicates: frozenset[str]
    directed: frozenset[str]
    typed_directed: frozenset[str]
    anchors: frozenset[str]
    polarity: str
    modality: str
    authority_id: str


@dataclass(frozen=True, order=True)
class RoleCompatibility:
    role_obligation: int
    directed: int
    predicates: int
    typed_directed: int
    anchors: int


def _predicate(token: str) -> bool:
    return token in _PREDICATE_WORDS or any(token.startswith(root) for root in _PREDICATE_ROOTS) or (
        len(token) >= 6 and any(token.endswith(suffix) for suffix in _PREDICATE_SUFFIXES)
    )


def _canonical_predicate(token: str) -> str:
    roots = tuple(root for root in _PREDICATE_ROOTS if token.startswith(root))
    return max(roots, key=len) if roots else token


def _typed(token: str, anchors: frozenset[str], numbers: frozenset[str],
           temporal: frozenset[str]) -> str:
    if token in numbers:
        return "@number"
    if token in temporal:
        return "@time"
    if token in anchors:
        return "@anchor"
    return token


def compile_role_sketch(surface: str, anchors: frozenset[str], authority_id: str, *,
                        max_gap: int = 4) -> RoleSketch:
    if not authority_id or not 1 <= max_gap <= 8:
        raise ValueError("bounded role sketch requires authority and max_gap 1..8")
    channels = observe_raw_text(surface)
    lexical = tuple(channels.lexical)
    number_set = frozenset(channels.numbers)
    temporal_set = frozenset(channels.temporal)
    predicate_tokens = frozenset(token for token in lexical if _predicate(token))
    predicates = frozenset(_canonical_predicate(token) for token in predicate_tokens)
    directed = set()
    typed_directed = set()
    for left_index, left in enumerate(lexical):
        for right_index in range(left_index + 1, min(len(lexical), left_index + max_gap + 1)):
            right = lexical[right_index]
            # At least one endpoint must be a relation carrier.  This prevents an O(n^2)
            # lexical bag from masquerading as a role representation.
            if left not in predicate_tokens and right not in predicate_tokens:
                continue
            canonical_left = _canonical_predicate(left) if left in predicate_tokens else left
            canonical_right = _canonical_predicate(right) if right in predicate_tokens else right
            directed.add(f"{canonical_left}>{canonical_right}")
            typed_directed.add(
                f"{_typed(canonical_left, anchors, number_set, temporal_set)}>"
                f"{_typed(canonical_right, anchors, number_set, temporal_set)}")
    return RoleSketch(
        lexical=lexical,
        predicates=predicates,
        directed=frozenset(directed),
        typed_directed=frozenset(typed_directed),
        anchors=anchors,
        polarity=channels.polarity,
        modality=channels.modality,
        authority_id=authority_id,
    )


def obligation_sketch(value: QuestionObligation) -> RoleSketch:
    if not value.verify(value.surface) and value.span != (0, len(value.surface)):
        # D48 obligations normally point into a larger question, so authorization is
        # rechecked by their caller.  The local sketch still requires a non-empty span.
        if value.span[0] >= value.span[1]:
            raise ValueError("invalid obligation span")
    return compile_role_sketch(value.surface, value.anchors, value.obligation_id)


def claim_sketch(value: AuthorizedClaim) -> RoleSketch:
    return compile_role_sketch(value.surface, value.anchors, value.claim_id)


def compatibility(obligation: RoleSketch, claim: RoleSketch, *, reverse: bool = False) \
        -> RoleCompatibility:
    if obligation.polarity == "negative" and claim.polarity != "negative":
        return RoleCompatibility(0, 0, 0, 0, 0)
    directed = claim.directed
    typed = claim.typed_directed
    if reverse:
        directed = frozenset(">".join(item.split(">")[::-1]) for item in directed)
        typed = frozenset(">".join(item.split(">")[::-1]) for item in typed)
    direct_count = len(obligation.directed & directed)
    predicate_count = len(obligation.predicates & claim.predicates)
    typed_count = len(obligation.typed_directed & typed)
    anchor_count = len(obligation.anchors & claim.anchors)
    role_obligation = int(bool(direct_count or (predicate_count and typed_count)))
    return RoleCompatibility(
        role_obligation, direct_count, predicate_count, typed_count, anchor_count)


__all__ = [
    "RoleCompatibility", "RoleSketch", "claim_sketch", "compatibility",
    "compile_role_sketch", "obligation_sketch",
]
