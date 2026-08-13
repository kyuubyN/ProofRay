# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from horizon_memory.ideal_coalition import (
    CoalitionVerification, DominantChampion, IdealChallenger, IdealCoalitionTournament,
)


def _champion():
    return DominantChampion("king", "truth", .8, ("body", "time"), 5, (99,))


def _challenger(name, abilities, strength=.6, cost=1):
    return IdealChallenger(name, "truth", tuple(sorted(abilities)), strength, .9, cost,
                           (sum(map(ord, name)),))


def _verification(state="correct", strength=.9, contributions=(("body", .6), ("clock", .4))):
    return CoalitionVerification("alliance", ("body", "clock"), state, strength,
                                 tuple(sorted(contributions)), 6, 7, (100,))


def test_two_complementary_losers_can_depose_an_unbeatable_solo_champion():
    challengers = tuple(sorted((_challenger("body", ("body",)),
                                _challenger("clock", ("time",)))))
    result = IdealCoalitionTournament().challenge(_champion(), challengers, _verification())
    assert result.state == "coalition_won_and_dissolved"
    assert result.successor_id == "body"
    assert result.coauthor_ids == ("clock",)


def test_coalition_must_share_the_champions_declared_ideal():
    outsider = IdealChallenger("clock", "glory", ("time",), .6, .9, 1, (7,))
    challengers = tuple(sorted((_challenger("body", ("body",)), outsider)))
    assert IdealCoalitionTournament().challenge(
        _champion(), challengers, None).state == "no_coalition"


def test_candidate_who_can_win_alone_cannot_hide_inside_alliance():
    challengers = tuple(sorted((_challenger("body", ("body",), strength=.9),
                                _challenger("clock", ("time",)))))
    assert IdealCoalitionTournament().challenge(
        _champion(), challengers, None).state == "no_coalition"


def test_duplicate_skills_do_not_create_complementarity_or_mass():
    challengers = tuple(sorted((_challenger("a", ("body",)),
                                _challenger("b", ("body",)))))
    assert IdealCoalitionTournament().challenge(
        _champion(), challengers, None).state == "no_coalition"


def test_failed_joint_verification_preserves_champion():
    challengers = tuple(sorted((_challenger("body", ("body",)),
                                _challenger("clock", ("time",)))))
    result = IdealCoalitionTournament().challenge(
        _champion(), challengers, _verification(state="wrong"))
    assert result.state == "champion_retained"
    assert result.successor_id == "king"


def test_equal_contribution_victory_does_not_invent_a_best_member():
    challengers = tuple(sorted((_challenger("body", ("body",)),
                                _challenger("clock", ("time",)))))
    verification = _verification(contributions=(("body", .5), ("clock", .5)))
    result = IdealCoalitionTournament().challenge(_champion(), challengers, verification)
    assert result.state == "coalition_won_contested_succession"
    assert result.successor_id is None


def test_wrong_member_verification_is_rejected():
    challengers = tuple(sorted((_challenger("body", ("body",)),
                                _challenger("clock", ("time",)))))
    bad = CoalitionVerification("bad", ("body", "other"), "correct", .9,
                                (("body", .6), ("other", .4)), 6, 7, (100,))
    with pytest.raises(ValueError):
        IdealCoalitionTournament().challenge(_champion(), challengers, bad)
