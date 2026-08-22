# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Small deterministic EN relation pack promoted from the H-PLT laboratory gate.

This module recognizes a deliberately finite one-hole question grammar and extracts one-token
ARG1/ARG2 mentions from exact source spans. It is a textual relation reader, not a factual writer:
interrogative, conditional, modal and negated clauses retain that force and must not be promoted to
asserted memory without a stronger authority. No model, embedding, network or external parser runs.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import struct

from .surface_atomic_kernel import SurfaceSvoConfig, select_svo_readings


VERB_EXCEPTIONS_SHA256 = "dbbcf9a601b2d77e934e413b91d90e88ec7f933a8b77cfc00602a923b891b42c"
_VERB_EXCEPTIONS_PATH = Path(__file__).parent / "resources" / "wordnet-3.0" / "verb.exc"
_COMPACT_MAGIC = b"HAR1"
_COMPACT = struct.Struct(">4s32s32s32sII")
_COMPACT_DOMAIN = b"HORIZON-EN-ATOMIC-RELATION-v1\0"

_WORD = re.compile(r"[^\W_]+(?:[’'-][^\W_]+)*", re.UNICODE)
_SUBJECT_QUERY = re.compile(
    r"^\s*(?P<type>who|what)\s+(?P<predicate>[^\W_]+(?:[’'-][^\W_]+)*)\s+"
    r"(?P<known>[^\W_]+(?:[’'-][^\W_]+)*)\s*\?\s*$", re.I)
_OBJECT_QUERY = re.compile(
    r"^\s*(?P<type>who|what|where)\s+did\s+"
    r"(?P<known>[^\W_]+(?:[’'-][^\W_]+)*)\s+"
    r"(?P<predicate>[^\W_]+(?:[’'-][^\W_]+)*)\s*\?\s*$", re.I)

_AUXILIARY_LEMMA = {
    "am": "be", "are": "be", "did": "do", "does": "do", "had": "have",
    "has": "have", "is": "be", "was": "be", "were": "be",
}
_PERSON_PRONOUNS = frozenset({
    "he", "her", "hers", "him", "his", "i", "me", "mine", "our", "ours", "she",
    "their", "theirs", "them", "they", "us", "we", "you", "your", "yours",
})
_SKIP = frozenset({
    "'d", "'ll", "'m", "n't", "'re", "'s", "'t", "'ve", "a", "an", "and", "are",
    "at", "be", "been", "being", "by", "can", "could", "did", "do", "does", "ever",
    "for", "from", "had", "has", "have", "in", "into", "is", "just", "may", "might",
    "must", "never", "not", "of", "on", "onto", "or", "over", "please", "shall",
    "should", "still", "the", "to", "was", "were", "will", "would",
})
_DETERMINERS = frozenset({
    "all", "another", "any", "both", "each", "either", "every", "neither", "some",
    "that", "these", "this", "those",
})
# A tempting extension -- adding possessive determiners ("her"/"his"/"their"/"your"/"my"/"our"/
# "its") to `_DETERMINERS` above, so "her smoke canisters" reaches "canisters" instead of stopping
# at "her" -- was tried and REVERTED after a real regression on the frozen GUM test holdout
# (78/82 -> 77/82; `EnglishAtomicRelationCompiler().read("You're watching her lean out.", "What
# did You watch?")` shifted from the correct gold object "her" to the wrong "lean"). The surface
# form cannot distinguish a possessive determiner ("her canisters") from an accusative object
# pronoun immediately followed by an unrelated word, including a small-clause/ECM embedded verb
# ("watching her lean out" -- "her" is the true object of "watching"; "lean" is a separate,
# unrelated embedded verb, not a noun "her" modifies). Real Gen-Z chat gap, confirmed and
# diagnosed, but NOT safe to fix with this closed-word-class mechanism as-is -- see
# `CLAUDE.md`/`UNIVERSAL_DETERMINISTIC_COMPILATION_PROGRAM.md` for the full account. Do not
# re-add "her"/"his"/"their"/"your"/"my"/"our"/"its" here without a materially different
# disambiguation signal (e.g. checking whether the following token can itself be a predicate).

_OBJECT_PRONOUNS = frozenset({"her", "him", "me", "them", "us", "you"})
_DITRANSITIVE = frozenset({
    "award", "bring", "buy", "cook", "fetch", "give", "grant", "hand", "lend", "offer",
    "owe", "pass", "pay", "read", "sell", "send", "show", "teach", "tell", "write",
})
_ADJECTIVAL_SUFFIXES = ("able", "al", "ed", "ful", "ible", "ic", "ing", "ive", "less", "ous")
_MODALS = frozenset({"can", "could", "may", "might", "must", "shall", "should", "will", "would"})
# Closed-class spelled-out English cardinal numbers, for the quantity head-shift in
# `surface_atomic_kernel.phrase_head` ("five headshots" -> "headshots", not "five"). A digit-only
# token ("5", "1,000") already triggers the same shift once this set is non-empty -- see
# `is_numeral()` there -- so this set only needs to cover words, never digits. Deliberately
# excludes "a"/"an"/"one" as a determiner-shaped article use ("a headshot") is already handled by
# `_DETERMINERS`/`article_gap`; "one" specifically is ambiguous between the cardinal ("one kill")
# and the indefinite pronoun ("the one that got away") and is left out rather than risk a second,
# competing reading the way the PT bridge's own "um"/"uma" exclusion already established.
_NUMERAL_WORDS = frozenset({
    "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
    "hundred", "thousand", "million", "billion",
})
_EN_SVO_CONFIG = SurfaceSvoConfig(
    person_pronouns=_PERSON_PRONOUNS,
    skip=_SKIP,
    determiners=_DETERMINERS,
    object_pronouns=_OBJECT_PRONOUNS,
    ditransitive_predicates=_DITRANSITIVE,
    adjectival_suffixes=_ADJECTIVAL_SUFFIXES,
    fronted_what=frozenset({"what", "which"}),
    fronted_who=frozenset({"who", "whom"}),
    fronted_where=frozenset({"where"}),
    article_gap=frozenset({"a", "an", "the"}),
    coordinators=frozenset({"and", "or"}),
    together_markers=frozenset({"together"}),
    numeral_words=_NUMERAL_WORDS,
    # "one" is unambiguous only as the prefix of a hyphenated compound ("one-tap", "one-shot") --
    # never added to `_NUMERAL_WORDS` itself, since a bare standalone "one" is also the indefinite
    # pronoun head ("the one that got away"), which must stay reachable as an answer on its own.
    numeral_hyphen_prefixes=frozenset({"one"}),
)


@dataclass(frozen=True, order=True)
class BinaryQueryDemand:
    predicate: str
    answer_role: str
    answer_type: str
    known_role: str
    known_value: str


@dataclass(frozen=True, order=True)
class BinarySpanReading:
    candidate: str
    span: tuple[int, int]
    predicate_span: tuple[int, int]
    known_span: tuple[int, int]
    rule: str


@dataclass(frozen=True)
class EnglishAtomicRelationProof:
    source_sha256: str
    question_sha256: str
    morphology_sha256: str
    answer: str
    answer_span: tuple[int, int]
    demand: BinaryQueryDemand
    source_force: str
    witnesses: tuple[BinarySpanReading, ...]

    def reopen(self, source: str, question: str,
               compiler: "EnglishAtomicRelationCompiler | None" = None) -> bool:
        active = compiler or EnglishAtomicRelationCompiler()
        result = active.read(source, question)
        return result.state == "resolved" and self in result.proofs


@dataclass(frozen=True)
class EnglishAtomicRelationResult:
    state: str
    answer: str | None
    answer_span: tuple[int, int] | None
    proofs: tuple[EnglishAtomicRelationProof, ...]
    source_force: str
    reason: str

    @property
    def proof_closed(self) -> bool:
        return self.state == "resolved" and bool(self.proofs)


@dataclass(frozen=True, order=True)
class _Token:
    index: int
    surface: str
    span: tuple[int, int]


class EnglishAtomicRelationCompiler:
    """Deterministic one-token EN relation reader with exact-span proofs."""

    def __init__(self) -> None:
        raw = _VERB_EXCEPTIONS_PATH.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != VERB_EXCEPTIONS_SHA256:
            raise RuntimeError("English morphology resource failed its frozen digest")
        mapping = {}
        for line in raw.decode("ascii").splitlines():
            fields = line.split()
            if len(fields) >= 2:
                mapping[fields[0]] = tuple(sorted(set(fields[1:])))
        self._exceptions = mapping
        self.morphology_sha256 = digest

    def predicate_forms(self, value: str) -> frozenset[str]:
        word = value.casefold()
        forms = {word}
        forms.update(self._exceptions.get(word, ()))
        if word.endswith("ing") and len(word) > 4:
            stem = word[:-3]
            forms.update((stem, stem + "e"))
            if len(stem) > 2 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
                forms.add(stem[:-1])
        if word.endswith("ied") and len(word) > 3:
            forms.add(word[:-3] + "y")
        if word.endswith("ed") and len(word) > 2:
            forms.update((word[:-2], word[:-1]))
        if word.endswith("ies") and len(word) > 3:
            forms.add(word[:-3] + "y")
        if word.endswith("es") and len(word) > 2:
            forms.update((word[:-2], word[:-1]))
        if word.endswith("s") and len(word) > 1:
            forms.add(word[:-1])
        return frozenset(forms)

    @staticmethod
    def compile_query(question: str) -> BinaryQueryDemand | None:
        if not question or len(question) > 512:
            return None
        if match := _OBJECT_QUERY.fullmatch(question):
            return BinaryQueryDemand(
                match.group("predicate").casefold(), "ARG2", match.group("type").casefold(),
                "ARG1", match.group("known").casefold())
        if match := _SUBJECT_QUERY.fullmatch(question):
            return BinaryQueryDemand(
                match.group("predicate").casefold(), "ARG1", match.group("type").casefold(),
                "ARG2", match.group("known").casefold())
        return None

    @staticmethod
    def _tokens(source: str) -> tuple[_Token, ...]:
        rows = []
        for match in _WORD.finditer(source):
            surface = match.group()
            negative = re.search(r"n[’']t$", surface, re.I)
            clitic = negative or re.search(r"[’'](?:s|ll|ve|re|d|m|t)$", surface, re.I)
            spans = ((match.start(), match.end()),)
            if clitic and clitic.start() > 0:
                split = match.start() + clitic.start()
                spans = ((match.start(), split), (split, match.end()))
            for start, end in spans:
                rows.append(_Token(len(rows), source[start:end], (start, end)))
        return tuple(rows)

    @staticmethod
    def _lemma(token: _Token) -> str:
        value = token.surface.casefold()
        return _AUXILIARY_LEMMA.get(value, value)

    @staticmethod
    def _source_force(source: str, tokens: tuple[_Token, ...]) -> str:
        lemmas = {EnglishAtomicRelationCompiler._lemma(token) for token in tokens}
        if source.rstrip().endswith("?"):
            return "interrogative"
        if lemmas & {"if", "unless", "whether"}:
            return "conditional"
        if lemmas & _MODALS:
            return "modal"
        if lemmas & {"not", "never", "n't"}:
            return "negated"
        return "asserted_candidate"

    def readings(self, source: str, demand: BinaryQueryDemand) -> tuple[BinarySpanReading, ...]:
        if not source or len(source) > 4096:
            return ()
        tokens = self._tokens(source)
        return tuple(BinarySpanReading(
            reading.candidate, reading.span, reading.predicate_span,
            reading.known_span, reading.rule)
                     for reading in select_svo_readings(
                         tokens, demand, lemma=self._lemma,
                         predicate_forms=self.predicate_forms, config=_EN_SVO_CONFIG))

    def read(self, source: str, question: str) -> EnglishAtomicRelationResult:
        demand = self.compile_query(question)
        tokens = self._tokens(source) if source else ()
        force = self._source_force(source, tokens) if tokens else "unknown"
        if demand is None:
            return EnglishAtomicRelationResult(
                "unsupported", None, None, (), force, "query_outside_frozen_en_grammar")
        readings = self.readings(source, demand)
        if not readings:
            return EnglishAtomicRelationResult(
                "abstain", None, None, (), force, "no_complete_surface_reading")
        answers = {(reading.candidate, reading.span) for reading in readings}
        if len(answers) != 1:
            return EnglishAtomicRelationResult(
                "contested", None, None, (), force, "surface_readings_disagree")
        answer, span = next(iter(answers))
        proof = EnglishAtomicRelationProof(
            hashlib.sha256(source.encode()).hexdigest(),
            hashlib.sha256(question.encode()).hexdigest(), self.morphology_sha256,
            answer, span, demand, force, readings)
        return EnglishAtomicRelationResult(
            "resolved", answer, span, (proof,), force, "all_complete_surface_readings_agree")


def compact_english_atomic_relation(result: EnglishAtomicRelationResult) -> bytes:
    if not result.proof_closed or len(result.proofs) != 1 or result.answer_span is None:
        raise ValueError("only one proof-closed EN atomic relation can be compacted")
    proof = result.proofs[0]
    start, end = result.answer_span
    payload = _COMPACT.pack(
        _COMPACT_MAGIC, bytes.fromhex(proof.source_sha256), bytes.fromhex(proof.question_sha256),
        bytes.fromhex(proof.morphology_sha256), start, end)
    return payload + hashlib.sha256(_COMPACT_DOMAIN + payload).digest()


def open_compact_english_atomic_relation(blob: bytes, *, source: str, question: str,
                                         compiler: EnglishAtomicRelationCompiler | None = None) \
        -> EnglishAtomicRelationResult:
    if not isinstance(blob, bytes) or len(blob) != _COMPACT.size + 32:
        raise ValueError("invalid compact EN atomic relation length")
    payload, claimed = blob[:-32], blob[-32:]
    if hashlib.sha256(_COMPACT_DOMAIN + payload).digest() != claimed:
        raise ValueError("compact EN atomic relation integrity failure")
    magic, source_hash, question_hash, morphology_hash, start, end = _COMPACT.unpack(payload)
    active = compiler or EnglishAtomicRelationCompiler()
    if (magic != _COMPACT_MAGIC or source_hash.hex() != hashlib.sha256(source.encode()).hexdigest() or
            question_hash.hex() != hashlib.sha256(question.encode()).hexdigest() or
            morphology_hash.hex() != active.morphology_sha256):
        raise ValueError("compact EN atomic relation authority mismatch")
    result = active.read(source, question)
    if not result.proof_closed or result.answer_span != (start, end):
        raise ValueError("compact EN atomic relation no longer reopens")
    return result


__all__ = [
    "BinaryQueryDemand", "BinarySpanReading", "EnglishAtomicRelationCompiler",
    "EnglishAtomicRelationProof", "EnglishAtomicRelationResult", "VERB_EXCEPTIONS_SHA256",
    "compact_english_atomic_relation", "open_compact_english_atomic_relation",
]
