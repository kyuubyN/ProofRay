# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.evolutionary_reward import (
    Competitor, VerifiedEvolution, VerifiedOutcome,
)


def _outcome(query, competitor, state, *, answer=True, impact=1, compute=1, context=1,
             verified_at=2):
    return VerifiedOutcome(query, competitor, state, answer, impact, compute, context,
                           1, verified_at, (99,))


def test_unique_verified_residual_solution_receives_originality_credit():
    competitors = tuple(sorted((Competitor("copy", ("lexical",)),
                                Competitor("novel", ("causal", "rhythm")))))
    outcomes = tuple(sorted((_outcome("q", "copy", "wrong"),
                             _outcome("q", "novel", "correct"))))
    result = VerifiedEvolution().evaluate(competitors, outcomes)
    novel = next(item for item in result.scores if item.competitor_id == "novel")
    assert novel.unique_solves == 1
    assert novel.originality_credit > 0
    assert result.champion_id == "novel"


def test_novel_but_wrong_is_punished_not_rewarded():
    competitors = tuple(sorted((Competitor("plain", ("lexical",)),
                                Competitor("wild", ("dark", "music", "wormhole")))))
    outcomes = tuple(sorted((_outcome("q", "plain", "correct"),
                             _outcome("q", "wild", "wrong"))))
    result = VerifiedEvolution().evaluate(competitors, outcomes)
    wild = next(item for item in result.scores if item.competitor_id == "wild")
    assert wild.originality_credit == 0
    assert result.champion_id == "plain"


def test_self_confidence_without_delayed_verifier_cannot_create_reward():
    try:
        VerifiedOutcome("q", "ego", "correct", True, 1, 0, 0, 2, 2, (1,))
        assert False
    except ValueError:
        pass


def test_false_accept_cost_exceeds_honest_abstention_and_compute_is_metabolic():
    competitors = tuple(sorted((Competitor("reckless", ("a",)),
                                Competitor("careful", ("b",)))))
    outcomes = tuple(sorted((_outcome("negative", "reckless", "wrong", answer=False,
                                      compute=1, context=1),
                             _outcome("negative", "careful", "abstain", answer=False,
                                      compute=2, context=2))))
    result = VerifiedEvolution().evaluate(competitors, outcomes)
    scores = {item.competitor_id: item for item in result.scores}
    assert scores["careful"].fitness > scores["reckless"].fitness
    assert scores["careful"].metabolic_cost == 4


def test_specialists_survive_when_they_own_distinct_verified_residuals():
    competitors = tuple(sorted((Competitor("body", ("body",)),
                                Competitor("music", ("cavity", "rhythm")))))
    outcomes = tuple(sorted((
        _outcome("identity", "body", "correct"), _outcome("identity", "music", "wrong"),
        _outcome("implicit", "body", "wrong"), _outcome("implicit", "music", "correct"),
    )))
    result = VerifiedEvolution().evaluate(competitors, outcomes)
    assert result.promoted_specialists == ("body", "music")
    assert result.residual_query_ids == ()


def test_unsolved_cases_remain_explicit_evolutionary_hunger():
    competitors = (Competitor("one", ("a",)),)
    outcomes = (_outcome("hard", "one", "abstain"),)
    result = VerifiedEvolution().evaluate(competitors, outcomes)
    assert result.residual_query_ids == ("hard",)


def test_abstention_cannot_win_utility_by_hiding_from_every_positive_case():
    competitors = tuple(sorted((Competitor("worker", ("body",)),
                                Competitor("coward", ("silence",)))))
    outcomes = tuple(sorted((
        _outcome("q1", "worker", "correct"), _outcome("q1", "coward", "abstain"),
        _outcome("q2", "worker", "correct"), _outcome("q2", "coward", "abstain"),
    )))
    assert VerifiedEvolution().evaluate(competitors, outcomes).champion_id == "worker"


def test_first_verified_solution_keeps_originality_after_a_later_copy():
    competitors = tuple(sorted((Competitor("discoverer", ("causal", "music")),
                                Competitor("copy", ("lexical",)))))
    outcomes = tuple(sorted((
        _outcome("q", "discoverer", "correct", verified_at=2),
        _outcome("q", "copy", "correct", verified_at=3),
    )))
    scores = {item.competitor_id: item for item in
              VerifiedEvolution().evaluate(competitors, outcomes).scores}
    assert scores["discoverer"].first_solves == 1
    assert scores["discoverer"].originality_credit > 0
    assert scores["copy"].first_solves == 0
    assert scores["copy"].originality_credit == 0


def test_simultaneous_verification_cannot_invent_a_first_author():
    competitors = tuple(sorted((Competitor("a", ("body",)),
                                Competitor("b", ("music",)))))
    outcomes = tuple(sorted((_outcome("q", "a", "correct"),
                             _outcome("q", "b", "correct"))))
    result = VerifiedEvolution().evaluate(competitors, outcomes)
    assert all(score.first_solves == 0 and score.originality_credit == 0
               for score in result.scores)
