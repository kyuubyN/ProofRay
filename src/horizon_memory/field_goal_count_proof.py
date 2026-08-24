# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Complete-enumeration COUNT proof for passive field-goal questions."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re

from .field_goal_extremum_proof import FieldGoalDistance, _observations, _selected_scope


_QUERY = re.compile(
    r"^how many field goals were (?:made|kicked|scored|converted)"
    r"(?: (?:in|during) (?:the )?(?P<scope>first half|second half|first quarter|"
    r"second quarter|third quarter|fourth quarter))?\??$", re.I)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class FieldGoalCountProof:
    question_sha256: str
    passage_sha256: str
    scope: str
    observations: tuple[FieldGoalDistance, ...]
    result: int

    def verify(self, question: str, passage: str) -> bool:
        if _sha(question) != self.question_sha256 or _sha(passage) != self.passage_sha256:
            return False
        return _compile_field_goal_count(question, passage) == self


def _compile_field_goal_count(question: str, passage: str) -> FieldGoalCountProof | None:
    match = _QUERY.fullmatch(question.strip())
    if match is None or not passage:
        return None
    compiled = _observations(passage)
    if compiled is None or not compiled[0]:
        return None
    observations = compiled[0]
    scope = (match.group("scope") or "game").casefold().replace(" ", "_")
    selected = _selected_scope(observations, scope)
    if not selected:
        return None
    return FieldGoalCountProof(
        _sha(question), _sha(passage), scope, observations,
        sum(item.multiplicity for item in selected))


def compile_field_goal_count(question: str, passage: str) -> FieldGoalCountProof | None:
    """Count a completely enumerated field-goal scope or abstain."""
    return _compile_field_goal_count(question, passage)


__all__ = ["FieldGoalCountProof", "compile_field_goal_count"]
