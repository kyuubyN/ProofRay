# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.terminal_state_facts import build_terminal_state_index


def test_terminal_frame_emits_multiple_typed_facts() -> None:
    source = "Turner finished the game with 24 carries for 117 rushing yards and four touchdowns."
    index = build_terminal_state_index(source)
    assert index.resolve(actor="Turner", scope="whole_game", metric="carries").value == 24
    assert index.resolve(actor="Turner", scope="whole_game", metric="rushing_yards").value == 117
    assert index.resolve(actor="Turner", scope="whole_game", metric="touchdowns").value == 4
    assert index.verify(source)


def test_catches_and_receptions_share_closed_lexical_unit() -> None:
    source = "White finished the game with four receptions for 70 receiving yards."
    index = build_terminal_state_index(source)
    assert index.resolve(actor="White", scope="whole_game", metric="receptions").value == 4
    assert index.resolve(actor="White", scope="whole_game", metric="receiving_yards").value == 70


def test_season_qualified_tuple_is_not_laundered_into_game_scope() -> None:
    source = "Antonio Brown finished the game with 122 catches on the season."
    index = build_terminal_state_index(source)
    assert index.facts and not index.facts[0].authorized
    assert index.resolve(actor="Brown", scope="whole_game", metric="receptions").state == "unsupported"


def test_bare_yards_do_not_answer_typed_yards() -> None:
    source = "Romo finished the game with 218 yards."
    index = build_terminal_state_index(source)
    assert index.resolve(actor="Romo", scope="whole_game", metric="yards").value == 218
    assert index.resolve(actor="Romo", scope="whole_game", metric="passing_yards").state == "unsupported"


def test_pronoun_frame_is_rejected_and_conflict_fails_closed() -> None:
    assert not build_terminal_state_index("He finished the game with 92 receiving yards.").facts
    source = "Green finished the game with 12 receptions. Green ended the day with 10 catches."
    result = build_terminal_state_index(source).resolve(
        actor="Green", scope="whole_game", metric="receptions")
    assert result.state == "conflict" and result.value is None


def test_source_tamper_invalidates_terminal_tuple() -> None:
    source = "Green finished the game with 12 receptions."
    index = build_terminal_state_index(source)
    assert not index.verify(source.replace("12", "13"))


def test_decimal_and_incidental_late_number_are_not_misparsed() -> None:
    assert not build_terminal_state_index(
        "Ratliff finished the game with a career-high 3.5 sacks."
    ).facts
    index = build_terminal_state_index(
        "White finished the game with four receptions for 70 yards and is 27 yards shy."
    )
    assert [fact.value for fact in index.facts if fact.metric == "yards"] == [70]


def test_end_of_game_event_is_not_a_terminal_stat_list() -> None:
    source = "Seattle ended the game with Wilson returning an interception 61 yards for a touchdown."
    assert not build_terminal_state_index(source).facts
