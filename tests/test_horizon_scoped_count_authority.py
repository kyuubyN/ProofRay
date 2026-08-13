# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.scoped_count_authority import (
    build_scoped_field_goal_authorities,
)


def test_quarter_aggregate_cannot_answer_whole_game() -> None:
    source = "Gostkowski kicked 2 field goals in the second quarter."
    index = build_scoped_field_goal_authorities(source)
    assert index.resolve(actor="Gostkowski", scope="quarter:2").count == 2
    assert index.resolve(actor="Gostkowski", scope="whole_game").state == "unsupported"
    assert index.verify(source)


def test_explicit_whole_game_aggregate_closes() -> None:
    source = "Stephen Gostkowski kicked three field goals in the game."
    result = build_scoped_field_goal_authorities(source).resolve(
        actor="Gostkowski", scope="whole_game")
    assert result.state == "closed"
    assert result.count == 3
    assert result.authorities[0].kind == "aggregate"


def test_incremental_and_bare_counts_are_not_authorities() -> None:
    source = (
        "Houston kicked two more field goals in the game. "
        "Gostkowski kicked two field goals."
    )
    index = build_scoped_field_goal_authorities(source)
    assert len(index.evidence) == 2
    assert not any(item.authorized for item in index.evidence)
    assert index.resolve(actor="Houston", scope="whole_game").state == "unsupported"
    assert index.resolve(actor="Gostkowski", scope="whole_game").state == "unsupported"


def test_aggregate_and_exhaustive_enumeration_agree() -> None:
    source = (
        "In the game, Folk's only field goals were from 31 and 42 yards. "
        "Nick Folk kicked two field goals in the game."
    )
    result = build_scoped_field_goal_authorities(source).resolve(
        actor="Folk", scope="whole_game")
    assert result.state == "closed"
    assert result.count == 2
    assert {item.kind for item in result.authorities} == {
        "aggregate", "exhaustive_enumeration",
    }


def test_disagreement_is_conflict_and_source_tamper_invalidates_proof() -> None:
    source = (
        "In the game, Folk's only field goals were from 31 and 42 yards. "
        "Nick Folk kicked three field goals in the game."
    )
    index = build_scoped_field_goal_authorities(source)
    result = index.resolve(actor="Folk", scope="whole_game")
    assert result.state == "conflict"
    assert result.count is None
    assert not index.verify(source.replace("42", "43"))


def test_purpose_phrase_does_not_launder_local_count_into_game_scope() -> None:
    source = "Folk kicked two field goals to keep New York in the game."
    index = build_scoped_field_goal_authorities(source)
    assert index.resolve(actor="Folk", scope="whole_game").state == "unsupported"
