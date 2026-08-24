# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reopenable homogeneous differences whose operands are bound to passage text.

The compiler intentionally recognizes a small grammar.  It is not a general
numeric QA heuristic: every accepted operand has a source span, a compatible
measure word, and either a named comparison condition or an explicit question
operand.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re


_NUMBER = re.compile(r"(?<![\w.,-])(?:\d{1,3}(?:,\d{3})+|\d+)(?![\w,-]|\.\d)")
_YEAR_OR_SEASON = re.compile(r"^(?:1[0-9]{3}|20[0-9]{2})(?:[-–]\d{2,4})?$")
_APPROX = re.compile(r"\b(?:about|approximately|approx\.?|around|roughly|nearly|some)\b", re.I)
_RANGE = re.compile(r"\b(?:between|from|range|ranging)\b", re.I)
_PERCENT_OR_MONEY = re.compile(r"[%$]|\bpercent(?:age)?\b", re.I)
_SENTENCE = re.compile(r"(?:[^.!?]|(?<=\d)\.(?=\d)|(?<=[A-Za-z])\.)+[.!?]?")
_WORD_NUMBERS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20,
}
_STOP = {
    "a", "an", "and", "at", "by", "did", "do", "does", "for", "from",
    "how", "in", "is", "many", "more", "of", "on", "than", "the", "to",
    "was", "were", "what", "which", "with",
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", text.casefold()))


def _singular(token: str) -> str:
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        return token[:-1]
    return token


def _measure_aliases(unit: str) -> frozenset[str]:
    root = _singular(unit.casefold())
    aliases = {root}
    # Closed, typed lexical equivalences.  They only establish measurement
    # compatibility; condition binding remains independently required.
    if root == "people":
        aliases.update(("person", "population", "people"))
    elif root == "club":
        aliases.update(("club", "entrant"))
    elif root == "procedure":
        aliases.update(("procedure",))
    return frozenset(aliases)


def _sentences(text: str) -> tuple[tuple[int, int, str], ...]:
    rows = []
    for match in _SENTENCE.finditer(text):
        if match.group().strip():
            rows.append((match.start(), match.end(), match.group()))
    return tuple(rows)


@dataclass(frozen=True)
class BoundDifferenceOperand:
    value: int
    origin: str  # passage or question
    span: tuple[int, int]
    surface: str
    condition: str
    approximate: bool
    unit_span: tuple[int, int]
    unit_surface: str
    unit_distance: int
    shared_carrier: bool


@dataclass(frozen=True)
class PassageHomogeneousDifferenceProof:
    question_sha256: str
    passage_sha256: str
    unit: str
    form: str
    operands: tuple[BoundDifferenceOperand, BoundDifferenceOperand]
    result: int

    def verify(self, question: str, passage: str) -> bool:
        if _sha(question) != self.question_sha256 or _sha(passage) != self.passage_sha256:
            return False
        rebuilt = _compile_passage_homogeneous_difference(question, passage)
        return rebuilt == self


def _quantity_candidates(sentence_start: int, sentence: str, unit: str) -> tuple[BoundDifferenceOperand, ...]:
    aliases = _measure_aliases(unit)
    unit_hits = []
    for match in re.finditer(r"[A-Za-z]+", sentence):
        if _singular(match.group().casefold()) in aliases:
            unit_hits.append((match.span(), match.group()))
    root = _singular(unit.casefold())
    shared_population = root == "people" and any(
        _singular(surface.casefold()) == "population" for _span, surface in unit_hits)
    for (unit_start, unit_end), _surface in unit_hits:
        prefix = sentence[max(0, unit_start - 64):unit_start]
        if (len(list(_NUMBER.finditer(prefix))) >= 2
                and re.search(r"\band\b", prefix, re.I)
                and not shared_population):
            return ()
    rows = []
    for number in _NUMBER.finditer(sentence):
        surface = number.group()
        if _YEAR_OR_SEASON.match(surface):
            continue
        left = max(0, number.start() - 48)
        right = min(len(sentence), number.end() + 48)
        context = sentence[left:right]
        if _PERCENT_OR_MONEY.search(context):
            continue
        suffix = sentence[number.end():number.end() + 16]
        if re.match(r"\s*(?:thousand|million|billion)\b", suffix, re.I):
            # Scaling belongs to a decimal/scaled-quantity compiler; using the
            # unscaled coefficient would create a numerically valid false proof.
            continue
        ranked_units = sorted(
            (min(abs(number.start() - end), abs(start - number.end())), start, end, surface)
            for (start, end), surface in unit_hits)
        if not ranked_units:
            continue
        distance, unit_start, unit_end, unit_surface = ranked_units[0]
        shared_carrier = shared_population
        if distance > 32 and not shared_carrier:
            continue
        approx_context = sentence[max(0, number.start() - 18):number.start()]
        rows.append(BoundDifferenceOperand(
            int(surface.replace(",", "")), "passage",
            (sentence_start + number.start(), sentence_start + number.end()),
            surface, "", bool(_APPROX.search(approx_context)),
            (sentence_start + unit_start, sentence_start + unit_end), unit_surface,
            distance, shared_carrier))
    return tuple(rows)


def _condition_key(text: str) -> tuple[str, ...]:
    years = re.findall(r"\b(?:1[0-9]{3}|20[0-9]{2})(?:[-–]\d{2,4})?\b", text)
    if years:
        return tuple(item.casefold() for item in years)
    return tuple(_singular(token) for token in _tokens(text)
                 if token not in _STOP and len(token) > 2)


def _bind_condition(passage: str, condition: str, unit: str) -> BoundDifferenceOperand | None:
    keys = _condition_key(condition)
    if not keys:
        return None
    ranked = []
    matched_sentences = 0
    for start, _end, sentence in _sentences(passage):
        lowered = sentence.casefold().replace("–", "-")
        normalized_keys = tuple(key.replace("–", "-") for key in keys)
        def present(key: str) -> bool:
            if key in lowered:
                return True
            if re.fullmatch(r"(?:1[0-9]{3}|20[0-9]{2})", key):
                return re.search(rf"\b\d{{4}}[-–]{re.escape(key[-2:])}\b", lowered) is not None
            return False
        if not all(present(key) for key in normalized_keys):
            continue
        anchor_positions = []
        anchor_spans = []
        for key in normalized_keys:
            position = lowered.find(key)
            if position < 0:
                abbreviated = re.search(rf"\b\d{{4}}[-–]{re.escape(key[-2:])}\b", lowered)
                position = abbreviated.start() if abbreviated else -1
                end_position = abbreviated.end() if abbreviated else -1
            else:
                end_position = position + len(key)
            anchor_positions.append(position)
            anchor_spans.append((position, end_position))
        anchor = sum(anchor_positions) / len(anchor_positions)
        candidates = _quantity_candidates(start, sentence, unit)
        if candidates:
            matched_sentences += 1
        for candidate in candidates:
            local = candidate.span[0] - start
            condition_distance = abs(local - anchor)
            local_end = candidate.span[1] - start
            relation_rank = 1
            for anchor_start, _anchor_end in anchor_spans:
                if local_end <= anchor_start and re.fullmatch(
                        r"\s*(?:in|by|as of|during)\s*",
                        sentence[local_end:anchor_start], re.I):
                    relation_rank = 0
            score = ((relation_rank, condition_distance, candidate.unit_distance)
                     if candidate.shared_carrier
                     else (relation_rank, candidate.unit_distance, condition_distance))
            ranked.append((score, candidate))
    if not ranked or matched_sentences != 1:
        return None
    ranked.sort(key=lambda item: (item[0], item[1].span))
    best_score = ranked[0][0]
    best = [candidate for score, candidate in ranked if score == best_score]
    if len(best) != 1:
        return None
    selected = best[0]
    return BoundDifferenceOperand(
        selected.value, selected.origin, selected.span, selected.surface,
        condition.strip(), selected.approximate, selected.unit_span,
        selected.unit_surface, selected.unit_distance, selected.shared_carrier)


def _comparison_form(question: str, passage: str) -> PassageHomogeneousDifferenceProof | None:
    match = re.match(
        r"\s*how many more (?P<unit>[A-Za-z]+)\s+"
        r"(?P<verb>were|was|are|is|entered|lived|participated|attended|scored|had)\b"
        r"(?P<body>.+?)\bcompared to\b(?P<right>.+?)\?*\s*$",
        question, re.I)
    if match is None:
        return None
    body, right = match.group("body"), match.group("right")
    left_years = tuple(re.finditer(r"\b(?:1[0-9]{3}|20[0-9]{2})(?:[-–]\d{2,4})?\b", body))
    if left_years:
        left = left_years[-1].group()
    else:
        pieces = re.findall(r"\b(?:in|at|during)\s+([^,?]+)", body, re.I)
        if not pieces:
            return None
        left = pieces[-1]
    right = re.sub(r"^(?:in|at|during|the)\s+", "", right.strip(), flags=re.I)
    right = re.sub(r"\s+(?:season|year)$", "", right, flags=re.I)
    unit = match.group("unit")
    first = _bind_condition(passage, left, unit)
    second = _bind_condition(passage, right, unit)
    if first is None or second is None or first.span == second.span:
        return None
    # Nominal differences are only closed when approximation status agrees.
    if first.approximate != second.approximate or first.value < second.value:
        return None
    return PassageHomogeneousDifferenceProof(
        _sha(question), _sha(passage), _singular(unit), "condition_comparison",
        (first, second), first.value - second.value)


def _explicit_baseline_form(question: str, passage: str) -> PassageHomogeneousDifferenceProof | None:
    match = re.match(
        r"\s*how many more than (?:the )?(?:top )?(?P<number>\d+|[A-Za-z]+) "
        r"(?P<unit>[A-Za-z]+) does (?P<body>.+?)\?*\s*$", question, re.I)
    if match is None:
        return None
    number_surface = match.group("number")
    if number_surface.isdigit():
        baseline = int(number_surface)
    else:
        baseline = _WORD_NUMBERS.get(number_surface.casefold(), -1)
    if baseline < 0:
        return None
    body_terms = {_singular(token) for token in _tokens(match.group("body"))
                  if token not in _STOP and len(token) > 3}
    if len(body_terms) < 2:
        return None
    ranked = []
    for start, _end, sentence in _sentences(passage):
        sentence_terms = {_singular(token) for token in _tokens(sentence)}
        overlap = body_terms & sentence_terms
        if len(overlap) < 2:
            continue
        for candidate in _quantity_candidates(start, sentence, match.group("unit")):
            ranked.append((-len(overlap), candidate.span, candidate))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (item[0], item[1]))
    best_score = ranked[0][0]
    best = [item[2] for item in ranked if item[0] == best_score]
    if len(best) != 1 or best[0].value < baseline or best[0].approximate:
        return None
    question_span = match.span("number")
    explicit = BoundDifferenceOperand(
        baseline, "question", question_span, number_surface, "explicit_baseline", False,
        match.span("unit"), match.group("unit"), 0, False)
    source = BoundDifferenceOperand(
        best[0].value, "passage", best[0].span, best[0].surface,
        "predicate_and_subject_overlap", False, best[0].unit_span,
        best[0].unit_surface, best[0].unit_distance, best[0].shared_carrier)
    return PassageHomogeneousDifferenceProof(
        _sha(question), _sha(passage), _singular(match.group("unit")),
        "explicit_baseline", (source, explicit), source.value - baseline)


def _compile_passage_homogeneous_difference(
    question: str, passage: str,
) -> PassageHomogeneousDifferenceProof | None:
    if (not question or not passage or _RANGE.search(question)
            or _PERCENT_OR_MONEY.search(question)):
        return None
    return (_comparison_form(question, passage)
            or _explicit_baseline_form(question, passage))


def compile_passage_homogeneous_difference(
    question: str, passage: str,
) -> PassageHomogeneousDifferenceProof | None:
    """Compile a source-bound subtraction, or abstain on any open binding."""
    return _compile_passage_homogeneous_difference(question, passage)


__all__ = [
    "BoundDifferenceOperand", "PassageHomogeneousDifferenceProof",
    "compile_passage_homogeneous_difference",
]
