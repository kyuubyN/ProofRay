# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.telic_observer import (
    DecisionIntent, PreparedGoalCandidate, TelicObserverEgo, TelicPuzzlePiece,
)


def _intent():
    return DecisionIntent("g", "s", "Caroline", "career", 10, 11,
                          ("body", "intent", "value"))


def _candidate(cid, hypothesis, probability=.7, impact=1, prepared=5):
    return PreparedGoalCandidate(cid, hypothesis, prepared, probability, impact)


def _piece(fid, cid, slot, strength=.8, **changes):
    values = dict(fact_id=fid, candidate_id=cid, scope="s", subject="Caroline",
                  predicate="career", slot=slot, strength=strength,
                  observed_at=4, expires_at=20, hard_negative=False)
    values.update(changes)
    return TelicPuzzlePiece(**values)


def test_one_goal_attracts_different_pieces_into_a_complete_proof():
    candidate = _candidate("mental", "mental-health counseling")
    pieces = tuple(sorted((_piece(1, "mental", "body"),
                           _piece(2, "mental", "intent"),
                           _piece(3, "mental", "value"))))
    result = TelicObserverEgo().close(_intent(), (candidate,), pieces)
    assert result.state == "committed"
    assert result.evidence_fact_ids == (1, 2, 3)


def test_piece_bound_to_one_future_cannot_fill_a_rival_future():
    candidates = tuple(sorted((_candidate("mental", "counseling"),
                               _candidate("art", "painting"))))
    pieces = tuple(sorted((_piece(1, "mental", "body"),
                           _piece(2, "mental", "intent"),
                           _piece(3, "mental", "value"),
                           _piece(4, "art", "body"))))
    result = TelicObserverEgo().close(_intent(), candidates, pieces)
    assert result.candidate_id == "mental"


def test_candidate_prepared_after_query_is_oracle_and_abstains():
    result = TelicObserverEgo().close(
        _intent(), (_candidate("late", "answer", prepared=10),),
        tuple(sorted((_piece(1, "late", "body"), _piece(2, "late", "intent"),
                      _piece(3, "late", "value")))))
    assert result.state == "abstain"


def test_missing_slot_keeps_goal_open_instead_of_average_completion():
    result = TelicObserverEgo().close(
        _intent(), (_candidate("x", "answer"),),
        tuple(sorted((_piece(1, "x", "body", 1), _piece(2, "x", "value", 1)))))
    assert result.state == "abstain"
    assert result.missing_slots == ("intent",)


def test_hard_repulsion_reaches_goal_before_commitment():
    pieces = tuple(sorted((_piece(1, "x", "body"), _piece(2, "x", "intent"),
                           _piece(3, "x", "value"),
                           _piece(9, "x", "value", hard_negative=True))))
    result = TelicObserverEgo().close(_intent(), (_candidate("x", "answer"),), pieces)
    assert result.state == "abstain"
    assert 9 in result.evidence_fact_ids


def test_three_complete_futures_require_margin_or_remain_contested():
    candidates = tuple(sorted((_candidate("a", "A"), _candidate("b", "B"),
                               _candidate("c", "C", probability=.2))))
    pieces = tuple(sorted(_piece(fid, cid, slot) for fid, (cid, slot) in enumerate((
        ("a", "body"), ("a", "intent"), ("a", "value"),
        ("b", "body"), ("b", "intent"), ("b", "value"),
        ("c", "body"), ("c", "intent"), ("c", "value")), 1)))
    assert TelicObserverEgo().close(_intent(), candidates, pieces).state == "contested"
