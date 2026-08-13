# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from horizon_memory.adaptive_lineage import (
    CompetitiveLineage, LineageOutcome, MutationChallenge, VerifiedSurvivalPressure,
)


def _lineage(name, ability="identity"):
    return CompetitiveLineage(name, ability, 0, None, (ability,), 10)


def _outcome(query, lineage, state, *, phase="solo", answer=True, verified=2,
             published=(7,)):
    if state in ("abstain", "withheld"):
        published = ()
    return LineageOutcome(query, lineage, phase, state, answer, 1, 1, 1, verified,
                          (99,), published)


def test_first_solo_verified_winner_gets_glory_and_championship():
    lineages = tuple(sorted((_lineage("fast"), _lineage("late"))))
    outcomes = tuple(sorted((_outcome("q", "fast", "correct", verified=2),
                             _outcome("q", "late", "correct", verified=3))))
    result = VerifiedSurvivalPressure().evaluate(lineages, outcomes)
    scores = {score.lineage_id: score for score in result.scores}
    assert result.champions == (("identity", "fast"),)
    assert scores["fast"].first_verified_glories == 1
    assert scores["late"].first_verified_glories == 0


def test_seeing_a_rival_win_creates_relative_survival_pressure():
    lineages = tuple(sorted((_lineage("winner"), _lineage("loser"))))
    outcomes = tuple(sorted((_outcome("q", "winner", "correct"),
                             _outcome("q", "loser", "abstain"))))
    scores = {score.lineage_id: score for score in
              VerifiedSurvivalPressure().evaluate(lineages, outcomes).scores}
    assert scores["loser"].rival_losses == 1
    assert scores["loser"].extinction_risk > scores["winner"].extinction_risk


def test_withholding_is_worse_than_publishing_and_losing_honestly():
    lineages = tuple(sorted((_lineage("open"), _lineage("secret"))))
    outcomes = tuple(sorted((_outcome("q", "open", "wrong"),
                             _outcome("q", "secret", "withheld"))))
    scores = {score.lineage_id: score for score in
              VerifiedSurvivalPressure().evaluate(lineages, outcomes).scores}
    assert scores["open"].fitness > scores["secret"].fitness


def test_solo_glory_is_more_valuable_than_composite_assistance():
    lineages = tuple(sorted((_lineage("solo"), _lineage("helper"))))
    outcomes = tuple(sorted((
        _outcome("q", "solo", "correct"), _outcome("q", "helper", "wrong"),
        _outcome("q", "helper", "correct", phase="composite", verified=3),
    )))
    result = VerifiedSurvivalPressure().evaluate(lineages, outcomes)
    assert result.champions == (("identity", "solo"),)


def test_only_a_loser_can_mutate_after_verified_generation():
    lineages = tuple(sorted((_lineage("champion"), _lineage("challenger"))))
    outcomes = tuple(sorted((_outcome("q", "champion", "correct"),
                             _outcome("q", "challenger", "wrong"))))
    challenges = tuple(sorted((
        MutationChallenge("champion-v2", "champion", "identity", 1, ("radius",), 10, 3),
        MutationChallenge("challenger-v2", "challenger", "identity", 1,
                          ("radius",), 10, 3),
    )))
    result = VerifiedSurvivalPressure().evaluate(lineages, outcomes, challenges)
    assert result.accepted_challenges == ("challenger-v2",)


def test_mutation_before_verification_or_with_unbounded_compute_is_refused():
    lineages = tuple(sorted((_lineage("a"), _lineage("b"))))
    outcomes = tuple(sorted((_outcome("q", "a", "correct"),
                             _outcome("q", "b", "wrong"))))
    challenges = tuple(sorted((
        MutationChallenge("early", "b", "identity", 1, ("radius",), 10, 2),
        MutationChallenge("huge", "b", "identity", 1, ("radius",), 100, 3),
    )))
    assert not VerifiedSurvivalPressure().evaluate(
        lineages, outcomes, challenges).accepted_challenges


def test_missing_solo_publication_is_a_protocol_violation():
    lineages = tuple(sorted((_lineage("a"), _lineage("b"))))
    outcomes = (_outcome("q", "a", "correct"),)
    with pytest.raises(ValueError):
        VerifiedSurvivalPressure().evaluate(lineages, outcomes)
