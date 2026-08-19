# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cold-path observables and one signed retrieval amplitude from raw text."""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass


_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)
_NUMBER = re.compile(r"\b\d+(?:[.,]\d+)?\b")
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
    raw_tokens = _WORD.findall(text)
    stems = tuple(_stem(token) for token in raw_tokens)
    lexical = tuple(token for token in stems if len(token) >= 3 and token not in _STOP)
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
