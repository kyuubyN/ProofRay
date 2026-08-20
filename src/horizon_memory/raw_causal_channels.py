# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cold-path observables and one signed retrieval amplitude from raw text."""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass


_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)
# `\b\d+\b` used to require a real Unicode word-boundary on both sides -- but CJK ideographs are
# `\w`, so a digit run glued directly to CJK text on either side (e.g. "在2023年", completely
# ordinary Chinese phrasing, not an identifier) never had a `\b` transition and was silently
# invisible to this channel. Uses explicit ASCII-alphanumeric lookaround instead of `\b` so a CJK
# neighbor counts as a boundary while "abc123def" still correctly does not split out "123"
# (2026-08-19, found via code review, confirmed by direct reproduction).
_NUMBER = re.compile(r"(?<![A-Za-z0-9_])\d+(?:[.,]\d+)?(?![A-Za-z0-9_])")
# CJK ideographs (CJK Unified Ideographs + Extension A) -- no letter-casing, no whitespace between
# words, so neither `_WORD`'s regex nor `_stem`'s suffix rules apply meaningfully to them.
_CJK_CHAR = re.compile(r"[一-鿿㐀-䶿]")
_CJK_RUN = re.compile(r"[一-鿿㐀-䶿]+|[^一-鿿㐀-䶿]+")
_MAX_ZH_WORD_LEN = 4


def is_cjk(text: str) -> bool:
    return bool(_CJK_CHAR.search(text))


_MERGED_ZH_DICT: frozenset[str] | None = None


def _zh_dictionary() -> frozenset[str]:
    """`ZH_WORD_DICTIONARY` (corpus-specific, casual-chat vocabulary) unioned with
    `zh_word_dictionary_extended.WORDS` (general open-domain vocabulary filtered from Jieba's
    `dict.txt.big`, MIT-licensed data only, no Jieba code -- 2026-08-19, re-tested this session:
    a same-day union attempt was tried and reverted once before, against an OLDER version of
    `supersession_collapse.py`'s resolution mechanism, and cost 7.05% -> 9.84% on the 1,290-pair
    false-positive generality check for no measured gain. Re-tested fresh this session against
    the CURRENT mechanism -- which gained a correction-marker gate, a type-homogeneity filter,
    and a given-new-asymmetry check since that earlier test -- and now costs only 8.29% -> 9.22%,
    a materially different premise than the one the earlier revert was based on). Computed once
    and cached at module scope: unioning two frozensets is cheap, but not free enough to redo on
    every `segment_zh` call in a tight loop."""
    global _MERGED_ZH_DICT
    if _MERGED_ZH_DICT is None:
        from .zh_word_dictionary import ZH_WORD_DICTIONARY
        from .zh_word_dictionary_extended import WORDS as ZH_WORD_DICTIONARY_EXTENDED
        _MERGED_ZH_DICT = ZH_WORD_DICTIONARY | ZH_WORD_DICTIONARY_EXTENDED
    return _MERGED_ZH_DICT


def segment_zh(text: str) -> list[str]:
    """Bidirectional maximum-matching segmentation against `_zh_dictionary()`. Falls back to
    single characters for spans the dictionary doesn't cover -- an incomplete dictionary fails
    toward more (but still real) single-character tokens, not spurious multi-character ones.
    Ported here (2026-08-19) from `supersession_collapse.py`, its original home -- this is now
    the canonical copy; `supersession_collapse.py` imports it from here instead of keeping its
    own duplicate, since `observe_raw_text` below needed the same segmentation to fix a much
    more fundamental gap: `_WORD`'s regex has no concept of CJK word boundaries at all, so an
    entire punctuation-delimited Chinese clause previously matched as ONE opaque token -- two
    clauses describing the same fact in different words shared exactly zero lexical overlap,
    confirmed end-to-end: a trivial Chinese question with an unambiguous answer in the same
    document abstained completely through the real `HorizonAnswerEngine` pipeline."""
    _DICT = _zh_dictionary()

    chars = list(text)
    n = len(chars)

    def _forward() -> list[str]:
        words, i = [], 0
        while i < n:
            if not _CJK_CHAR.match(chars[i]):
                words.append(chars[i])
                i += 1
                continue
            matched = None
            for length in range(min(_MAX_ZH_WORD_LEN, n - i), 1, -1):
                candidate = "".join(chars[i:i + length])
                if candidate in _DICT:
                    matched = candidate
                    break
            words.append(matched or chars[i])
            i += len(matched) if matched else 1
        return words

    def _backward() -> list[str]:
        words, i = [], n
        while i > 0:
            if not _CJK_CHAR.match(chars[i - 1]):
                words.append(chars[i - 1])
                i -= 1
                continue
            matched = None
            for length in range(min(_MAX_ZH_WORD_LEN, i), 1, -1):
                candidate = "".join(chars[i - length:i])
                if candidate in _DICT:
                    matched = candidate
                    break
            words.append(matched or chars[i - 1])
            i -= len(matched) if matched else 1
        return list(reversed(words))

    forward, backward = _forward(), _backward()
    if len(forward) != len(backward):
        return forward if len(forward) < len(backward) else backward
    singles = lambda seq: sum(1 for w in seq if len(w) == 1 and _CJK_CHAR.match(w))
    return forward if singles(forward) <= singles(backward) else backward


def _split_cjk(token: str) -> list[str]:
    """A `_WORD` match may glue a CJK run directly to adjacent Latin/digit characters with no
    separator (e.g. "Meridian项目" or "2023年") since both scripts are `\\w`. Splits back into
    maximal same-script pieces first (so `segment_zh` only ever receives pure CJK text -- it
    treats any non-CJK character as a single-character passthrough, which would otherwise shred
    an embedded Latin word into individual letters), then segments each CJK piece into real
    dictionary words instead of leaving it as one opaque blob."""
    if not _CJK_CHAR.search(token):
        return [token]
    pieces = []
    for piece in _CJK_RUN.findall(token):
        if _CJK_CHAR.match(piece):
            pieces.extend(segment_zh(piece))
        else:
            pieces.append(piece)
    return pieces
_CLOCK = re.compile(
    r"\b(?:mon|tue|wed|thu|fri|sat|sun|monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday|today|tomorrow|yesterday|week|month|year|morning|afternoon|"
    r"evening|night|january|february|march|april|may|june|july|august|september|"
    r"october|november|december)\b", re.IGNORECASE)
_NEGATION = frozenset(("no", "not", "never", "neither", "nor", "without", "didn't",
                       "doesn't", "don't", "wasn't", "weren't", "isn't", "aren't"))
_MODAL = frozenset(("may", "might", "could", "would", "should", "perhaps", "maybe", "plan",
                    "plans", "planned", "hope", "hopes", "want", "wants"))
# 2026-08-18 (plan item 1b): a claim reporting an already-confirmed finding ("the study
# demonstrated X could improve...") is not a genuine hedge just because a modal word appears
# somewhere in it -- but `RawCausalChannels.modality` (below) is a whole-text boolean deliberately
# kept simple (5+ other callers, directly tested) and must not be edited to carry this distinction.
# `_modal_is_confirmed_finding` is a separate, narrower helper for a single caller
# (`materialized_proof_pressure_search.py`'s contradiction rule) to use instead of touching the
# shared field.
_CONFIRMING_REPORT = frozenset(("demonstrated", "showed", "shown", "found", "revealed",
                                "reported", "confirmed", "established", "achieved", "observed"))
_STOP = frozenset("""
a an and are as at be been being but by did do does for from had has have he her hers him his
how i if in into is it its me my of on or our ours she so than that the their them then there
these they this those to too us was we were what when where which who why with you your
about after again all also am any because before both during each few more most other over
same some such through under until up very here just really yeah yes know think got get going
""".split())


def _stem(token: str) -> str:
    value = token.casefold().replace("’", "'").strip("'")
    if value.endswith("'s"):
        value = value[:-2]
    for suffix in ("ingly", "edly", "ing", "ed", "ies", "es", "s"):
        if value.endswith(suffix) and len(value) - len(suffix) >= 4:
            return value[:-len(suffix)] + ("y" if suffix == "ies" else "")
    return value


def _grams(token: str) -> tuple[str, ...]:
    padded = f"^{token}$"
    return tuple(sorted({padded[index:index + 3] for index in range(max(0, len(padded) - 2))}))


@dataclass(frozen=True)
class RawCausalChannels:
    lexical: tuple[str, ...]
    sublexical: tuple[str, ...]
    entities: tuple[str, ...]
    numbers: tuple[str, ...]
    temporal: tuple[str, ...]
    relations: tuple[str, ...]
    polarity: str
    modality: str
    interrogative: str


def observe_raw_text(text: str, *, question: bool = False) -> RawCausalChannels:
    if not isinstance(text, str):
        raise TypeError("raw text must be str")
    raw_tokens = tuple(piece for match in _WORD.findall(text) for piece in _split_cjk(match))
    stems = tuple(_stem(token) for token in raw_tokens)
    # The `len(token) >= 3` floor exists to drop short, low-signal Latin fragments -- it does not
    # apply to CJK tokens, which are now real dictionary-segmented words (see `segment_zh`), not
    # arbitrary substrings: a 2-character word like "北京" or "会议" is already a complete,
    # meaningful unit, and requiring 3+ characters silently discarded most ordinary Chinese
    # vocabulary (2026-08-19, found via code review).
    lexical = tuple(token for token in stems
                    if (len(token) >= 3 or _CJK_CHAR.match(token)) and token not in _STOP)
    sublexical = tuple(sorted({gram for token in lexical for gram in _grams(token)}))
    # Capitalization is only a weak observable; sentence-initial words are excluded unless repeated.
    capitalized = [token.casefold() for index, token in enumerate(raw_tokens)
                   if index > 0 and token[:1].isupper() and len(token) > 1]
    entities = tuple(sorted(set(capitalized)))
    numbers = tuple(sorted(set(_NUMBER.findall(text))))
    temporal = tuple(sorted({_stem(value) for value in _CLOCK.findall(text)}))
    relations = tuple(sorted({f"{left}>{right}" for left, right in zip(lexical, lexical[1:])
                              if left != right}))
    lowered = tuple(token.casefold().replace("’", "'") for token in raw_tokens)
    polarity = "negative" if any(token in _NEGATION or token.endswith("n't")
                                  for token in lowered) else "positive"
    modality = "modal" if any(token in _MODAL for token in lowered) else "asserted"
    interrogative = "none"
    if question:
        lower_text = text.casefold()
        if "how many" in lower_text or "how much" in lower_text:
            interrogative = "quantity"
        elif re.search(r"\bwhen\b", lower_text):
            interrogative = "time"
        elif re.search(r"\bwho\b", lower_text):
            interrogative = "person"
        elif re.search(r"\bwhere\b", lower_text):
            interrogative = "place"
        elif re.search(r"\b(?:did|does|do|is|are|was|were|has|have|had|can|could|will|would)\b",
                       lower_text):
            interrogative = "boolean"
    return RawCausalChannels(tuple(lexical), sublexical, entities, numbers, temporal,
                             relations, polarity, modality, interrogative)


def _modal_is_confirmed_finding(text: str) -> bool:
    """True if at least one modal token in `text` is reporting something already established/
    observed ("the study demonstrated X could improve...") rather than genuinely hedging. Token-
    index comparison over the same raw tokenization `observe_raw_text` uses, not string search.

    Checks every modal token, not only the first one (2026-08-19, real bug found via code
    review, confirmed reproducible: a text with an early, unrelated modal -- "the team hopes to
    expand, but the audit demonstrated the results could improve" -- had `_MODAL` match "hopes"
    first and stop there, so a genuinely-confirmed "could" later in the same text was never
    checked; fixed, verified True on that exact input). Each modal's own confirming-verb search
    is bounded to the segment since the *previous modal* (or the start of `text` for the first
    one) -- this stops a confirming verb from one clause spuriously excusing an unrelated,
    genuinely-hedging *second* modal, but does NOT fully solve clause attribution in general:
    for the *first* modal in a text, the search window is still the entire prefix, exactly like
    the original implementation, so a confirming verb from an unrelated earlier clause can still
    wrongly excuse a single genuine hedge (e.g. "the study demonstrated X, but analysts hope for
    more" -- confirmed empirically still returns True for "hope" here, both before and after this
    fix). That is a real, separate, pre-existing limitation of using "anywhere in the preceding
    text" rather than clause-aware attribution -- not introduced or claimed to be fixed by this
    change, and not yet fixed at all. For a single-modal text with nothing before it in
    `_CONFIRMING_REPORT`, this reduces to exactly the original check, so the existing
    IKEA-bookshelf regression coverage (`tests/test_horizon_claim_generator.py`) is unaffected."""
    tokens = tuple(token.casefold().replace("’", "'") for token in _WORD.findall(text))
    modal_indices = [index for index, token in enumerate(tokens) if token in _MODAL]
    if not modal_indices:
        return False
    segment_start = 0
    for modal_index in modal_indices:
        if any(token in _CONFIRMING_REPORT for token in tokens[segment_start:modal_index]):
            return True
        segment_start = modal_index + 1
    return False


@dataclass(frozen=True)
class RawCausalDocument:
    fact_id: int
    text: str
    session_index: int
    turn: int
    speaker: str = ""


@dataclass(frozen=True)
class SignedChannelScore:
    fact_id: int
    amplitude: float
    lexical: float
    sublexical: float
    entity: float
    relation: float
    observable: float
    contradiction: float


class RawCausalSyndromeIndex:
    """One materialized signed field over lexical and conserved raw-text observables."""

    def __init__(self, documents: tuple[RawCausalDocument, ...]):
        if not documents or len({item.fact_id for item in documents}) != len(documents):
            raise ValueError("raw causal documents require unique FactIds")
        self.documents = documents
        self.channels = {item.fact_id: observe_raw_text(item.text) for item in documents}
        self.n = len(documents)
        self.avgdl = sum(len(self.channels[item.fact_id].lexical) for item in documents) / self.n
        self.df = Counter(token for item in documents
                          for token in set(self.channels[item.fact_id].lexical))
        self.sub_df = Counter(token for item in documents
                              for token in set(self.channels[item.fact_id].sublexical))

    @staticmethod
    def _normalize(rows: list[dict], name: str) -> None:
        maximum = max((abs(row[name]) for row in rows), default=0.0) or 1.0
        for row in rows:
            row[name] /= maximum

    def _bm25(self, query: tuple[str, ...], document: tuple[str, ...], df: Counter,
              average_length: float) -> float:
        tf = Counter(document)
        score = 0.0
        for token in set(query):
            frequency = tf[token]
            if not frequency:
                continue
            idf = math.log(1.0 + (self.n - df[token] + 0.5) / (df[token] + 0.5))
            score += idf * frequency * 2.2 / (
                frequency + 1.2 * (0.25 + 0.75 * len(document) / max(average_length, 1e-9)))
        return score

    def components(self, query_text: str) -> tuple[SignedChannelScore, ...]:
        query = observe_raw_text(query_text, question=True)
        average_sub = sum(len(value.sublexical) for value in self.channels.values()) / self.n
        rows = []
        for document in self.documents:
            value = self.channels[document.fact_id]
            entity_overlap = len(set(query.entities).intersection(value.entities))
            relation_overlap = len(set(query.relations).intersection(value.relations))
            observable = 0.0
            if query.interrogative == "time" and (value.temporal or value.numbers):
                observable += 1.0
            if query.interrogative in ("person", "place") and value.entities:
                observable += 1.0
            if query.interrogative == "quantity" and (value.numbers or len(value.entities) >= 2):
                observable += 1.0
            if query.numbers and set(query.numbers).intersection(value.numbers):
                observable += 1.0
            contradiction = 0.0
            # Only declared charge conflicts are negative evidence. Missing charges remain unknown.
            if query.numbers and value.numbers and not set(query.numbers).intersection(value.numbers):
                contradiction += 1.0
            if query.polarity == "negative" and value.polarity == "positive":
                contradiction += 0.5
            if query.modality == "asserted" and value.modality == "modal":
                contradiction += 0.25
            rows.append({
                "fact_id": document.fact_id,
                "lexical": self._bm25(query.lexical, value.lexical, self.df, self.avgdl),
                "sublexical": self._bm25(query.sublexical, value.sublexical,
                                          self.sub_df, average_sub),
                "entity": float(entity_overlap), "relation": float(relation_overlap),
                "observable": observable, "contradiction": contradiction,
            })
        for name in ("lexical", "sublexical", "entity", "relation", "observable"):
            self._normalize(rows, name)
        return tuple(SignedChannelScore(**row, amplitude=0.0) for row in rows)

    @staticmethod
    def rank(components: tuple[SignedChannelScore, ...], weights: tuple[float, ...]) \
            -> tuple[SignedChannelScore, ...]:
        if len(weights) != 6 or any(weight < 0 for weight in weights):
            raise ValueError("six non-negative signed-field weights are required")
        result = []
        for row in components:
            amplitude = (weights[0] * row.lexical + weights[1] * row.sublexical +
                         weights[2] * row.entity + weights[3] * row.relation +
                         weights[4] * row.observable - weights[5] * row.contradiction)
            result.append(SignedChannelScore(
                row.fact_id, amplitude, row.lexical, row.sublexical, row.entity,
                row.relation, row.observable, row.contradiction))
        return tuple(sorted(result, key=lambda item: (-item.amplitude, item.fact_id)))
