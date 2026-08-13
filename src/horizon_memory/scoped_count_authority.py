# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Proof-carrying, scope-exact authorities for deterministic COUNT queries.

The module deliberately distinguishes a number stated near an event noun from a
number authorized for a temporal scope.  A bare ``kicked two field goals`` is a
local statement; it is not silently promoted to a whole-game total.  Resolution
is possible only from an explicit scoped aggregate or an explicitly exhaustive
enumeration.  Independent authorities must agree exactly.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re


_SENTENCE = re.compile(r"[^.!?]+(?:[.!?]+|$)")
_COUNT_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9,
}
_COUNT = r"(?:one|two|three|four|five|six|seven|eight|nine|\d{1,2})"
_NAME = r"(?:Both teams|[A-Z][A-Za-z'’.-]*(?:\s+[A-Z][A-Za-z'’.-]*){0,4})"
_AGGREGATE = re.compile(
    rf"\b(?P<actor>{_NAME})(?:,\s*[^,]{{1,120}},)?\s+"
    rf"(?P<verb>kicked|made|scored|converted|had)\s+"
    rf"(?:(?:a\s+)?combined\s+)?(?P<count>{_COUNT})\s+"
    r"(?P<increment>more\s+|other\s+|additional\s+)?field goals?\b"
)
_ONLY_ENUMERATION = re.compile(
    rf"\b(?P<actor>{_NAME})['’]s\s+only\s+field goals?\s+were\s+"
    r"(?:from\s+)?(?P<values>\d{1,3}(?:[- ]yard(?:er)?s?)?"
    r"(?:\s*(?:,|and)\s*(?:a\s+)?\d{1,3}(?:[- ]yard(?:er)?s?)?)+)\b"
)
_LEADING_SCOPE = re.compile(
    r"^\s*(?:In|During)\s+(?P<label>this game|the game|the contest|"
    r"the first half|the second half|the first quarter|the second quarter|"
    r"the third quarter|the fourth quarter)\s*,",
)
_TRAILING_SCOPE = re.compile(
    r"^\s*(?:in|during|for|on)\s+(?P<label>this game|the game|the contest|"
    r"the day|the first half|the second half|the first quarter|the second quarter|"
    r"the third quarter|the fourth quarter)\b|^\s+in\s+total\b",
    re.IGNORECASE,
)
_TITLE_TOKENS = frozenset({"kicker", "placekicker", "veteran", "rookie", "former"})


def _source_sha256(source: str) -> str:
    return hashlib.sha256(source.encode()).hexdigest()


def _count(raw: str) -> int:
    folded = raw.casefold()
    return int(folded) if folded.isdigit() else _COUNT_WORDS[folded]


def _scope(label: str) -> str:
    folded = re.sub(r"\s+", " ", label.casefold()).strip()
    return {
        "this game": "whole_game",
        "the game": "whole_game",
        "the contest": "whole_game",
        "the day": "whole_game",
        "in total": "whole_game",
        "the first half": "first_half",
        "the second half": "second_half",
        "the first quarter": "quarter:1",
        "the second quarter": "quarter:2",
        "the third quarter": "quarter:3",
        "the fourth quarter": "quarter:4",
    }[folded]


def _explicit_scope(sentence: str, evidence_end: int) -> tuple[str, str]:
    """Return an authorized scope only from a syntactically attached marker."""
    trailing = _TRAILING_SCOPE.match(sentence[evidence_end:])
    if trailing:
        label = trailing.group("label") or "in total"
        return _scope(label), "scope is attached immediately after the count fact"
    leading = _LEADING_SCOPE.match(sentence)
    if leading:
        return _scope(leading.group("label")), "scope is an explicit sentence frame"
    return "local_unknown", "no explicit temporal scope is attached to the count fact"


def _actor_tokens(actor: str) -> tuple[str, ...]:
    tokens = tuple(
        token.casefold().strip(".'’-")
        for token in actor.split()
        if token.casefold().strip(".'’-") not in _TITLE_TOKENS
    )
    return tuple(token for token in tokens if token)


def _actor_matches(query_actor: str | None, evidence_actor: str) -> bool:
    evidence = _actor_tokens(evidence_actor)
    if query_actor is None:
        return evidence == ("both", "teams")
    query = _actor_tokens(query_actor)
    return bool(query and evidence and (query == evidence or query[-1] == evidence[-1]))


@dataclass(frozen=True)
class ScopedCountEvidence:
    source_span: tuple[int, int]
    sentence_span: tuple[int, int]
    actor: str
    scope: str
    count: int
    kind: str
    authorized: bool
    text: str
    reason: str


@dataclass(frozen=True)
class ScopedCountResolution:
    state: str
    count: int | None
    scope: str
    actor: str | None
    authorities: tuple[ScopedCountEvidence, ...]
    reason: str


@dataclass(frozen=True)
class ScopedCountAuthorityIndex:
    source_sha256: str
    evidence: tuple[ScopedCountEvidence, ...]

    def resolve(self, *, actor: str | None, scope: str) -> ScopedCountResolution:
        authorities = tuple(
            item for item in self.evidence
            if item.authorized and item.scope == scope and _actor_matches(actor, item.actor)
        )
        if not authorities:
            return ScopedCountResolution(
                "unsupported", None, scope, actor, (),
                "no actor-matching count authority has the exact requested scope",
            )
        values = {item.count for item in authorities}
        if len(values) != 1:
            return ScopedCountResolution(
                "conflict", None, scope, actor, authorities,
                "independent exact-scope authorities disagree",
            )
        return ScopedCountResolution(
            "closed", next(iter(values)), scope, actor, authorities,
            "all independent exact-scope authorities agree",
        )

    def verify(self, source: str) -> bool:
        if _source_sha256(source) != self.source_sha256:
            return False
        return all(
            0 <= item.source_span[0] < item.source_span[1] <= len(source)
            and source[item.source_span[0]:item.source_span[1]] == item.text
            for item in self.evidence
        )


def _make_evidence(
    *, source: str, sentence: str, sentence_start: int, sentence_end: int,
    match: re.Match[str], actor: str, count: int, kind: str,
    authorized: bool, scope: str, reason: str,
) -> ScopedCountEvidence:
    start = sentence_start + match.start()
    end = sentence_start + match.end()
    return ScopedCountEvidence(
        (start, end), (sentence_start, sentence_end), actor, scope, count,
        kind, authorized, source[start:end], reason,
    )


def build_scoped_field_goal_authorities(source: str) -> ScopedCountAuthorityIndex:
    if not source:
        raise ValueError("scoped count authority index requires non-empty source text")
    evidence: list[ScopedCountEvidence] = []
    for sentence_match in _SENTENCE.finditer(source):
        sentence = sentence_match.group()
        for match in _AGGREGATE.finditer(sentence):
            scope, scope_reason = _explicit_scope(sentence, match.end())
            incremental = match.group("increment") is not None
            authorized = scope != "local_unknown" and not incremental
            if incremental:
                reason = "incremental count is not an aggregate authority"
            elif not authorized:
                reason = scope_reason
            else:
                reason = f"literal aggregate; {scope_reason}"
            evidence.append(_make_evidence(
                source=source, sentence=sentence,
                sentence_start=sentence_match.start(), sentence_end=sentence_match.end(),
                match=match, actor=match.group("actor"), count=_count(match.group("count")),
                kind="aggregate", authorized=authorized, scope=scope, reason=reason,
            ))
        for match in _ONLY_ENUMERATION.finditer(sentence):
            scope, scope_reason = _explicit_scope(sentence, match.end())
            authorized = scope != "local_unknown"
            values = tuple(int(value) for value in re.findall(r"\d{1,3}", match.group("values")))
            evidence.append(_make_evidence(
                source=source, sentence=sentence,
                sentence_start=sentence_match.start(), sentence_end=sentence_match.end(),
                match=match, actor=match.group("actor"), count=len(values),
                kind="exhaustive_enumeration", authorized=authorized, scope=scope,
                reason=(f"literal only-enumeration; {scope_reason}" if authorized else scope_reason),
            ))
    return ScopedCountAuthorityIndex(_source_sha256(source), tuple(evidence))
