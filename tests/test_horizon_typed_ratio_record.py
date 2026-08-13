# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.typed_ratio_record import build_typed_ratio_index


def test_attached_game_scope_authorizes_all_three_ratio_values() -> None:
    source = "Rivers completed 33 of 54 passes in the game."
    index = build_typed_ratio_index(source)
    assert index.resolve(actor="Rivers", unit="passes", scope="whole_game", metric="numerator").value == 33
    assert index.resolve(actor="Rivers", unit="passes", scope="whole_game", metric="denominator").value == 54
    assert index.resolve(actor="Rivers", unit="passes", scope="whole_game", metric="complement").value == 21
    assert index.verify(source)


def test_finished_game_frame_is_exact_scope() -> None:
    source = "Ryan finished the game having completed 21 of 41 passes for 158 yards."
    result = build_typed_ratio_index(source).resolve(
        actor="Ryan", unit="passes", scope="whole_game", metric="complement")
    assert result.state == "closed" and result.value == 20


def test_bare_ratio_and_negative_complement_fail_closed() -> None:
    for source in (
        "Rivers completed 18 of 23 passes for 209 yards.",
        "Rivers completed 24 of 23 passes in the game.",
    ):
        index = build_typed_ratio_index(source)
        assert index.records and not index.records[0].authorized
        assert index.resolve(
            actor="Rivers", unit="passes", scope="whole_game", metric="numerator",
        ).state == "unsupported"


def test_actor_unit_and_source_are_proof_boundaries() -> None:
    source = "On the day, Eli Manning completed 18 of 31 passes for nearly 150 yards."
    index = build_typed_ratio_index(source)
    assert index.resolve(actor="Manning", unit="passes", scope="whole_game", metric="denominator").value == 31
    assert index.resolve(actor="Brady", unit="passes", scope="whole_game", metric="denominator").state == "unsupported"
    assert index.resolve(actor="Manning", unit="field_goals", scope="whole_game", metric="denominator").state == "unsupported"
    assert not index.verify(source.replace("31", "32"))


def test_partial_and_terminal_records_keep_distinct_scopes() -> None:
    source = (
        "Cutler completed 15 of 17 passes for 117 yards in the first half, and would end "
        "the game with stats of 23 of 31 passes completed."
    )
    index = build_typed_ratio_index(source)
    assert index.resolve(
        actor="Cutler", unit="passes", scope="first_half", metric="numerator",
    ).value == 15
    assert index.resolve(
        actor="Cutler", unit="passes", scope="whole_game", metric="numerator",
    ).value == 23
    assert index.resolve(
        actor="Cutler", unit="passes", scope="whole_game", metric="complement",
    ).value == 8
