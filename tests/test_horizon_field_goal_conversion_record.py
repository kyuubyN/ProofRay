# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from fractions import Fraction

from horizon_memory.field_goal_conversion_record import (
    build_field_goal_conversion_index,
)


def test_missed_of_attempts_derives_made_by_exact_identity() -> None:
    source = "Mike Vanderjagt missed two of his three field goals during the game."
    index = build_field_goal_conversion_index(source)
    assert index.resolve(actor="Vanderjagt", scope="whole_game", metric="missed").value == 2
    assert index.resolve(actor="Vanderjagt", scope="whole_game", metric="attempts").value == 3
    assert index.resolve(actor="Vanderjagt", scope="whole_game", metric="made").value == 1
    assert index.verify(source)


def test_complete_yard_list_authorizes_span_algebra() -> None:
    source = "Mason Crosby converted 3 of 3 field goals (37, 19, 34) in the win."
    index = build_field_goal_conversion_index(source)
    assert index.resolve(
        actor="Crosby", scope="whole_game", metric="range_count", lower=30, upper=40,
    ).value == 2
    assert index.resolve(actor="Crosby", scope="whole_game", metric="yard_sum").value == 90
    assert index.resolve(actor="Crosby", scope="whole_game", metric="yard_max").value == 37
    assert index.resolve(actor="Crosby", scope="whole_game", metric="yard_average").value == Fraction(30)


def test_bad_arithmetic_and_bad_yard_cardinality_fail_closed() -> None:
    for source in (
        "Folk converted 4 of 3 field goals in the game.",
        "Folk converted 3 of 3 field goals (31, 42) in the game.",
    ):
        index = build_field_goal_conversion_index(source)
        assert index.records and not index.records[0].authorized
        assert index.resolve(actor="Folk", scope="whole_game", metric="made").state == "unsupported"


def test_local_conversion_record_cannot_answer_whole_game() -> None:
    index = build_field_goal_conversion_index("John Hall was 1/2 on field goals with no PATs.")
    assert index.resolve(actor="Hall", scope="whole_game", metric="missed").state == "unsupported"


def test_direct_scoped_miss_authority_does_not_invent_attempts() -> None:
    source = "Janikowski missed three field goals in the game."
    index = build_field_goal_conversion_index(source)
    assert index.resolve(actor="Janikowski", scope="whole_game", metric="missed").value == 3
    assert index.resolve(actor="Janikowski", scope="whole_game", metric="attempts").state == "unsupported"


def test_source_tamper_invalidates_record() -> None:
    source = "Novak missed two field goals in the game."
    index = build_field_goal_conversion_index(source)
    assert not index.verify(source.replace("two", "three"))
