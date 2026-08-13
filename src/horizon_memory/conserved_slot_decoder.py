# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""High-precision literal QA by unique conserved-slot substitution."""
from __future__ import annotations

from dataclasses import dataclass
import re

from .inverse_cloze_decoder import (
    _BAD_CAPITAL, _CAPITAL, _DATE, _NUMERIC, _PREPOSITION, _SENTENCE,
    InverseClozeSpanDecoder,
)
from .raw_causal_channels import observe_raw_text


_TYPE_WORDS = frozenset(("person", "people", "year", "date", "time", "place", "location",
                         "city", "country", "state", "number", "count", "many", "much",
                         "long", "old", "far", "high", "large", "wide", "deep"))


@dataclass(frozen=True)
class ConservedSlotResult:
    state: str
    value: str | None
    source_span: tuple[int, int] | None
    answer_type: str
    lexical_coverage: float
    sentence_margin: float
    reason: str


class ConservedSlotSpanDecoder:
    def __init__(self):
        self.type_compiler = InverseClozeSpanDecoder()

    @staticmethod
    def _query_tokens(question: str) -> set[str]:
        return {token for token in observe_raw_text(question, question=True).lexical
                if token not in _TYPE_WORDS}

    @staticmethod
    def _typed_matches(answer_type: str, sentence: str):
        if answer_type == "time":
            return tuple(_DATE.finditer(sentence))
        if answer_type == "quantity":
            return tuple(_NUMERIC.finditer(sentence))
        if answer_type == "person":
            return tuple(match for match in _CAPITAL.finditer(sentence)
                         if match.group().split()[0] not in _BAD_CAPITAL)
        if answer_type == "place":
            matches = []
            for match in _PREPOSITION.finditer(sentence):
                matches.append((match.span(1), match.group(1)))
            return tuple(_PlaceMatch(sentence, span, text) for span, text in matches)
        return ()

    def decode(self, question: str, document: str, *, minimum_coverage: float = .8,
               minimum_sentence_margin: float = .1) -> ConservedSlotResult:
        answer_type = self.type_compiler.question_type(question)
        if answer_type == "unsupported":
            return ConservedSlotResult(
                "unsupported", None, None, answer_type, 0.0, 0.0,
                "question has no supported conserved slot")
        query_tokens = self._query_tokens(question)
        if not query_tokens:
            return ConservedSlotResult(
                "abstain", None, None, answer_type, 0.0, 0.0,
                "question has no conserved address tokens")
        scored = []
        for sentence_match in _SENTENCE.finditer(document):
            sentence_tokens = set(observe_raw_text(sentence_match.group()).lexical)
            coverage = len(query_tokens.intersection(sentence_tokens)) / len(query_tokens)
            scored.append((coverage, sentence_match))
        scored.sort(key=lambda item: (-item[0], item[1].start()))
        if not scored:
            return ConservedSlotResult(
                "abstain", None, None, answer_type, 0.0, 0.0, "document has no sentence")
        coverage, sentence_match = scored[0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        sentence_margin = coverage - runner_up
        if coverage < minimum_coverage or sentence_margin < minimum_sentence_margin:
            return ConservedSlotResult(
                "abstain", None, None, answer_type, coverage, sentence_margin,
                "sentence conservation or margin is insufficient")
        question_folded = question.casefold()
        candidates = []
        for match in self._typed_matches(answer_type, sentence_match.group()):
            text = match.group().strip(" ,.;:()[]")
            if not text or text.casefold() in question_folded:
                continue
            local_start = sentence_match.group().find(text, match.start(), match.end() + 1)
            if local_start < 0:
                continue
            candidates.append((text, sentence_match.start() + local_start,
                               sentence_match.start() + local_start + len(text)))
        unique = {}
        for text, start, end in candidates:
            unique.setdefault(text.casefold(), (text, start, end))
        if len(unique) != 1:
            return ConservedSlotResult(
                "abstain", None, None, answer_type, coverage, sentence_margin,
                "conserved sentence does not expose one unique typed slot")
        text, start, end = next(iter(unique.values()))
        return ConservedSlotResult(
            "resolved", text, (start, end), answer_type, coverage, sentence_margin,
            "one literal typed value occupies the conserved sentence slot")


class _PlaceMatch:
    def __init__(self, sentence: str, span: tuple[int, int], text: str):
        self.sentence, self._span, self.text = sentence, span, text

    def group(self) -> str:
        return self.text

    def start(self) -> int:
        return self._span[0]

    def end(self) -> int:
        return self._span[1]
