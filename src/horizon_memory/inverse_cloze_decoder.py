# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic selective span decoding by inverse-cloze reconstruction."""
from __future__ import annotations

from dataclasses import dataclass
import re

from .raw_causal_channels import observe_raw_text


_SENTENCE = re.compile(r"[^.!?\n]+(?:[.!?]+|$)")
_WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)
_NUMBER_WORD = (r"zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
                r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
                r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|"
                r"first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth")
_MONTH = (r"January|February|March|April|May|June|July|August|September|October|November|December")
_CAPITAL = re.compile(r"\b(?:[A-ZÀ-ÖØ-Þ][\w'’.-]*)(?:\s+(?:[A-ZÀ-ÖØ-Þ][\w'’.-]*|of|the|de|van|von)){0,5}\b")
_NUMERIC = re.compile(
    rf"\b(?:\d{{1,4}}(?:[,.]\d+)?|{_NUMBER_WORD})(?:[-\s]+(?:{_NUMBER_WORD}|[A-Za-z%]+)){{0,4}}\b",
    re.I)
_DATE = re.compile(
    rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:{_MONTH})(?:,?\s+\d{{3,4}})?\b|"
    rf"\b(?:{_MONTH})(?:\s+\d{{1,2}}(?:st|nd|rd|th)?)?(?:,?\s+\d{{3,4}})?\b|"
    r"\b(?:\d{1,2}[/-]){1,2}\d{2,4}\b|\b\d{3,4}\b", re.I)
_PREPOSITION = re.compile(
    r"\b(?:in|at|from|near|outside|inside|to|on)\s+([A-ZÀ-ÖØ-Þ][\w'’.-]*(?:\s+(?:[A-ZÀ-ÖØ-Þ][\w'’.-]*|of|the)){0,5})")
_BAD_CAPITAL = frozenset(("Who", "What", "Which", "When", "Where", "How", "The", "In", "A", "An"))
_QUESTION_SCAFFOLD = frozenset("""
who whom whose what which when where how many much long old far high large wide deep
did does do is are was were has have had can could will would should the a an
""".split())


@dataclass(frozen=True)
class ExtractiveSpanCandidate:
    text: str
    source_span: tuple[int, int]
    sentence_span: tuple[int, int]
    answer_type: str
    reconstruction: float
    ordered_coverage: float
    uniqueness: float
    score: float


@dataclass(frozen=True)
class ExtractiveDecodeResult:
    state: str
    value: str | None
    source_span: tuple[int, int] | None
    confidence: float
    margin: float
    candidates: tuple[ExtractiveSpanCandidate, ...]
    reason: str


class InverseClozeSpanDecoder:
    """Extract typed literal spans; abstain unless score and margin are declared."""

    @staticmethod
    def question_type(question: str) -> str:
        lowered = question.strip().casefold()
        if re.search(r"\b(?:who|whom|whose)\b", lowered):
            return "person"
        if re.search(r"\bwhen\b|\bwhat year\b|\bwhat date\b", lowered):
            return "time"
        if re.search(r"\bwhere\b|\bwhat (?:city|country|state|borough|location|place)\b", lowered):
            return "place"
        if re.search(r"\bhow (?:many|much|long|old|far|high|large|wide|deep)\b", lowered):
            return "quantity"
        return "unsupported"

    @staticmethod
    def _tokens(text: str) -> tuple[str, ...]:
        return observe_raw_text(text).lexical

    @staticmethod
    def _ordered_coverage(query: tuple[str, ...], sentence: tuple[str, ...]) -> float:
        if not query:
            return 0.0
        position, matched = 0, 0
        for token in query:
            while position < len(sentence) and sentence[position] != token:
                position += 1
            if position < len(sentence):
                matched += 1
                position += 1
        return matched / len(query)

    @classmethod
    def _matches(cls, answer_type: str, sentence: str):
        if answer_type == "person":
            return tuple(match for match in _CAPITAL.finditer(sentence)
                         if match.group().split()[0] not in _BAD_CAPITAL)
        if answer_type == "time":
            return tuple(_DATE.finditer(sentence))
        if answer_type == "quantity":
            return tuple(_NUMERIC.finditer(sentence))
        if answer_type == "place":
            matches = []
            for match in _PREPOSITION.finditer(sentence):
                start, end = match.span(1)
                matches.append(_SyntheticMatch(sentence, start, end))
            return tuple(matches)
        return ()

    @staticmethod
    def _raw_tokens(text: str):
        return tuple((match.group().casefold().replace("’", "'"), match.start(), match.end())
                     for match in _WORD.finditer(text))

    @classmethod
    def _gap_matches(cls, answer_type: str, question: str, sentence: str):
        query = tuple(item for item in cls._raw_tokens(question)
                      if item[0] not in _QUESTION_SCAFFOLD)
        target = cls._raw_tokens(sentence)
        if not query or not target:
            return ()
        # LCS indices conserve order without inventing paraphrase equivalence.
        table = [[0] * (len(target) + 1) for _ in range(len(query) + 1)]
        for left in range(len(query) - 1, -1, -1):
            for right in range(len(target) - 1, -1, -1):
                table[left][right] = (1 + table[left + 1][right + 1]
                                      if query[left][0] == target[right][0]
                                      else max(table[left + 1][right], table[left][right + 1]))
        left = right = 0
        matched = set()
        while left < len(query) and right < len(target):
            if query[left][0] == target[right][0]:
                matched.add(right)
                left += 1
                right += 1
            elif table[left + 1][right] >= table[left][right + 1]:
                left += 1
            else:
                right += 1
        if len(matched) < max(2, len(query) // 3):
            return ()
        result = []
        position = 0
        while position < len(target):
            if position in matched:
                position += 1
                continue
            end_position = position
            while end_position + 1 < len(target) and end_position + 1 not in matched:
                end_position += 1
            if end_position - position < 8:
                start, end = target[position][1], target[end_position][2]
                candidate = sentence[start:end]
                inner = ()
                if answer_type == "time":
                    inner = tuple(_DATE.finditer(candidate))
                elif answer_type == "quantity":
                    inner = tuple(_NUMERIC.finditer(candidate))
                elif answer_type in ("person", "place"):
                    inner = tuple(_CAPITAL.finditer(candidate))
                for match in inner:
                    result.append(_SyntheticMatch(
                        sentence, start + match.start(), start + match.end(), gap=True))
            position = end_position + 1
        return tuple(result)

    def candidates(self, question: str, document: str) -> tuple[ExtractiveSpanCandidate, ...]:
        answer_type = self.question_type(question)
        if answer_type == "unsupported":
            return ()
        query_tokens = self._tokens(question)
        query_words = {value.casefold() for value in _WORD.findall(question)}
        raw = []
        for sentence_match in _SENTENCE.finditer(document):
            sentence = sentence_match.group()
            sentence_tokens = self._tokens(sentence)
            if not sentence_tokens:
                continue
            lexical_overlap = len(set(query_tokens).intersection(sentence_tokens)) / max(
                1, len(set(query_tokens)))
            ordered = self._ordered_coverage(query_tokens, sentence_tokens)
            matches = self._matches(answer_type, sentence) + self._gap_matches(
                answer_type, question, sentence)
            for match in matches:
                text = match.group().strip(" ,.;:()[]")
                if not text or text.casefold() in query_words or len(text) > 120:
                    continue
                local_start = sentence.find(text, match.start(), match.end() + 1)
                if local_start < 0:
                    continue
                start = sentence_match.start() + local_start
                end = start + len(text)
                remaining = sentence[:local_start] + " " + sentence[local_start + len(text):]
                remaining_tokens = set(self._tokens(remaining))
                reconstruction = len(set(query_tokens).intersection(remaining_tokens)) / max(
                    1, len(set(query_tokens)))
                type_bonus = 3.0 if getattr(match, "gap", False) else 1.0
                raw.append((text, start, end, sentence_match.start(), sentence_match.end(),
                            reconstruction, ordered, lexical_overlap, type_bonus))
        frequency = {}
        for item in raw:
            frequency[item[0].casefold()] = frequency.get(item[0].casefold(), 0) + 1
        result = []
        for text, start, end, sentence_start, sentence_end, reconstruction, ordered, overlap, type_bonus in raw:
            uniqueness = 1.0 / frequency[text.casefold()]
            length_penalty = max(0, len(text.split()) - 4) * .015
            score = (.45 * reconstruction + .25 * ordered + .2 * overlap +
                     .1 * uniqueness + .02 * type_bonus - length_penalty)
            result.append(ExtractiveSpanCandidate(
                text, (start, end), (sentence_start, sentence_end), answer_type,
                reconstruction, ordered, uniqueness, round(score, 9)))
        return tuple(sorted(result, key=lambda item: (-item.score, len(item.text),
                                                       item.source_span, item.text.casefold())))

    def decode(self, question: str, documents: tuple[str, ...], *, threshold: float,
               margin: float) -> ExtractiveDecodeResult:
        all_candidates = []
        for document_index, document in enumerate(documents):
            for candidate in self.candidates(question, document):
                all_candidates.append((candidate, document_index))
        ranked = tuple(item[0] for item in sorted(
            all_candidates, key=lambda item: (-item[0].score, item[1],
                                              len(item[0].text), item[0].source_span)))
        if not ranked:
            return ExtractiveDecodeResult(
                "unsupported", None, None, 0.0, 0.0, (), "no typed literal candidate")
        top = ranked[0]
        runner_up = next((item for item in ranked[1:]
                          if item.text.casefold() != top.text.casefold()), None)
        observed_margin = top.score - (runner_up.score if runner_up else 0.0)
        if top.score < threshold or observed_margin < margin:
            return ExtractiveDecodeResult(
                "abstain", None, None, top.score, observed_margin, ranked,
                "candidate confidence or margin is insufficient")
        return ExtractiveDecodeResult(
            "resolved", top.text, top.source_span, top.score, observed_margin, ranked,
            "unique typed inverse-cloze span")


class _SyntheticMatch:
    def __init__(self, text: str, start: int, end: int, gap: bool = False):
        self.text, self._start, self._end, self.gap = text, start, end, gap

    def group(self) -> str:
        return self.text[self._start:self._end]

    def start(self) -> int:
        return self._start

    def end(self) -> int:
        return self._end
