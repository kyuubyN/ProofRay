# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.responsible_causal_ego import (
    EgoClaim, OwnedAbility, ResponsibleCausalEgo,
)


def _claim(fid, strengths, intrinsic=.8, impact=.8, contradictions=()):
    abilities = tuple(sorted(OwnedAbility(fid, layer, strength, (fid,))
                             for layer, strength in strengths.items()))
    return EgoClaim(fid, intrinsic, impact, abilities, tuple(sorted(contradictions)))


def _ego(**changes):
    return ResponsibleCausalEgo(("body", "intention", "time"), **changes)


def test_complete_moderate_claim_beats_spectacular_but_incomplete_rival():
    complete = _claim(1, {"body": .7, "intention": .7, "time": .7})
    incomplete = _claim(2, {"body": 1, "intention": 1}, intrinsic=1, impact=1)
    result = _ego().decide(tuple(sorted((complete, incomplete))))
    assert result.state == "committed"
    assert result.winner_fact_id == 1


def test_claim_cannot_borrow_another_candidates_ability():
    try:
        EgoClaim(1, 1, 1, (OwnedAbility(2, "body", 1, (2,)),))
        assert False
    except ValueError:
        pass


def test_hard_contradiction_removes_even_the_strongest_ego():
    contradicted = _claim(1, {"body": 1, "intention": 1, "time": 1},
                          intrinsic=1, impact=1, contradictions=(9,))
    valid = _claim(2, {"body": .6, "intention": .6, "time": .6})
    assert _ego().decide(tuple(sorted((contradicted, valid)))).winner_fact_id == 2


def test_equal_accountable_drives_remain_contested_not_arbitrarily_decided():
    claims = tuple(sorted((_claim(1, {"body": .8, "intention": .8, "time": .8}),
                           _claim(2, {"body": .8, "intention": .8, "time": .8}))))
    assert _ego().decide(claims).state == "contested"


def test_impact_changes_commitment_without_becoming_truth_by_itself():
    low = _claim(1, {"body": .8, "intention": .8, "time": .8}, impact=.2)
    high = _claim(2, {"body": .8, "intention": .8, "time": .8}, impact=.9)
    assert _ego().decide(tuple(sorted((low, high)))).winner_fact_id == 2
    assert _ego().decide((_claim(3, {"body": 1}, impact=1),)).state == "abstain"


def test_winner_owns_center_while_rivals_survive_only_as_periphery():
    claims = tuple(sorted((_claim(1, {"body": .9, "intention": .9, "time": .9}),
                           _claim(2, {"body": .7, "intention": .7, "time": .7}),
                           _claim(3, {"body": .6, "intention": .6, "time": .6}))))
    result = _ego().decide(claims, halo_limit=3)
    assert result.winner_fact_id == 1
    assert result.peripheral_fact_ids == (2, 3)
    assert result.evidence_fact_ids == (1,)
