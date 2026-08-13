# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from horizon_memory.niche_tournament import (
    AbilityBid, AbilityRecord, ArenaDemand, VerifiedNicheTournament,
)


def _record(name, ability, *, correct=30, wrong=1, false=0, missed=1, first=2):
    return AbilityRecord(name, ability, correct, wrong, false, missed, first, 5, (99,))


def _bid(query, name, ability, *, strength=.9, coverage=1, cost=.1, contradiction=False):
    fact_id = sum((index + 1) * ord(character) for index, character in enumerate(name))
    return AbilityBid(query, name, ability, 6, strength, coverage, cost, (fact_id,),
                      contradiction)


def test_specialist_beats_weaker_rival_inside_its_own_ability():
    demand = ArenaDemand("q", ("identity",), 10, 1)
    records = tuple(sorted((_record("body", "identity", correct=40, wrong=0),
                            _record("general", "identity", correct=12, wrong=8, first=0))))
    bids = tuple(sorted((_bid("q", "body", "identity"),
                         _bid("q", "general", "identity"))))
    result = VerifiedNicheTournament().close(demand, records, bids)
    assert result.state == "committed"
    assert result.winners == (("identity", "body"),)


def test_winners_of_different_abilities_compose_instead_of_destroying_each_other():
    demand = ArenaDemand("q", ("identity", "temporal"), 10, 1)
    records = tuple(sorted((_record("body", "identity"),
                            _record("clock", "temporal"))))
    bids = tuple(sorted((_bid("q", "body", "identity"),
                         _bid("q", "clock", "temporal"))))
    result = VerifiedNicheTournament().close(demand, records, bids)
    assert result.state == "committed"
    assert result.winners == (("identity", "body"), ("temporal", "clock"))
    assert len(result.evidence_fact_ids) == 2


def test_false_accepts_remove_reckless_competitor_advantage():
    demand = ArenaDemand("q", ("specificity",), 10, 1)
    records = tuple(sorted((_record("hungry", "specificity", correct=30, false=15),
                            _record("skeptic", "specificity", correct=24, false=0))))
    bids = tuple(sorted((_bid("q", "hungry", "specificity", strength=1),
                         _bid("q", "skeptic", "specificity", strength=.8))))
    result = VerifiedNicheTournament(minimum_reliability=.2).close(demand, records, bids)
    assert result.winners == (("specificity", "skeptic"),)


def test_originality_has_no_power_before_reliability_gate():
    demand = ArenaDemand("q", ("novel",), 10, 1)
    records = (_record("wild", "novel", correct=1, wrong=9, first=1),)
    bids = (_bid("q", "wild", "novel", strength=1),)
    result = VerifiedNicheTournament(originality_rate=100).close(demand, records, bids)
    assert result.state == "abstain"
    assert result.missing_abilities == ("novel",)


def test_missing_ability_keeps_the_whole_puzzle_open():
    demand = ArenaDemand("q", ("identity", "temporal"), 10, 1)
    records = (_record("body", "identity"),)
    bids = (_bid("q", "body", "identity"),)
    result = VerifiedNicheTournament().close(demand, records, bids)
    assert result.state == "abstain"
    assert result.missing_abilities == ("temporal",)


def test_tied_local_fight_is_contested_not_arbitrarily_committed():
    demand = ArenaDemand("q", ("identity",), 10, 1)
    records = tuple(sorted((_record("a", "identity"), _record("b", "identity"))))
    bids = tuple(sorted((_bid("q", "a", "identity"), _bid("q", "b", "identity"))))
    assert VerifiedNicheTournament().close(demand, records, bids).state == "contested"


def test_post_query_bid_or_verification_is_rejected():
    demand = ArenaDemand("q", ("identity",), 10, 1)
    records = (AbilityRecord("a", "identity", 10, 0, 0, 0, 1, 10, (9,)),)
    bids = (_bid("q", "a", "identity"),)
    with pytest.raises(ValueError):
        VerifiedNicheTournament().close(demand, records, bids)
