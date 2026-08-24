from horizon_memory.field_goal_extremum_proof import compile_field_goal_extremum


PASSAGE = (
    "The kicker got a 31-yard field goal. Another kicker nailed a 40-yard field goal. "
    "The team then made a 23-yard and a 48-yard field goal. A final 52-yard field goal "
    "attempt was blocked."
)


def test_closed_enumeration_computes_extrema_and_reopens():
    longest = compile_field_goal_extremum(
        "How many yards was the longest field goal?", PASSAGE)
    shortest = compile_field_goal_extremum(
        "How many yards was the shortest field goal of the game?", PASSAGE)
    assert longest is not None and longest.result == 48
    assert shortest is not None and shortest.result == 23
    assert tuple(item.value for item in longest.observations) == (31, 40, 23, 48)
    assert longest.rejected_mentions
    assert longest.verify("How many yards was the longest field goal?", PASSAGE)
    assert not longest.verify("How many yards was the longest field goal?", PASSAGE + "x")


def test_from_yards_out_form_is_observed():
    passage = ("The kicker made a 32-yard field goal. He later hit his third field goal "
               "of the day from 25 yards out.")
    proof = compile_field_goal_extremum(
        "How many yards was the shortest field goal?", passage)
    assert proof is not None and proof.result == 25


def test_unmeasured_success_and_scoped_query_abstain():
    assert compile_field_goal_extremum(
        "How many yards was the longest field goal?",
        "The kicker made a 31-yard field goal and added another field goal later.") is None
    assert compile_field_goal_extremum(
        "How many yards was the longest first half field goal?", PASSAGE) is None


def test_attempt_is_not_laundered_into_successful_extremum():
    proof = compile_field_goal_extremum(
        "How many yards was the longest field goal?",
        "A 31-yard field goal was good. A 55-yard field goal attempt was no good.")
    assert proof is not None and proof.result == 31


def test_quarter_and_half_scope_are_computed_from_explicit_frames():
    passage = (
        "In the first quarter, A made a 31-yard field goal. In the second quarter, "
        "B made a 44-yard field goal. In the third quarter, A made a 22-yard field "
        "goal. In the fourth quarter, B made a 49-yard field goal."
    )
    first_half = compile_field_goal_extremum(
        "How many yards was the longest first half field goal?", passage)
    second_quarter = compile_field_goal_extremum(
        "How many yards was the shortest field goal in the second quarter?", passage)
    second_half = compile_field_goal_extremum(
        "How many yards was the shortest field goal of the second half?", passage)
    assert first_half is not None and first_half.result == 44
    assert second_quarter is not None and second_quarter.result == 44
    assert second_half is not None and second_half.result == 22


def test_shared_unit_coordination_contributes_both_distances():
    passage = (
        "In the second quarter, A hit a 52 and a 43-yard field goal. "
        "In the third quarter, B made a 32-yard field goal."
    )
    proof = compile_field_goal_extremum(
        "How many yards was the longest field goal of the first half?", passage)
    assert proof is not None and proof.result == 52
    assert tuple(item.value for item in proof.observations) == (52, 43, 32)


def test_three_way_shared_unit_and_common_abbreviations_are_enumerated():
    passage = (
        "The kicker hit a 24, 54, and a 31-yard field goal. Later he made a "
        "50-yard FG and another kicker nailed a 53-yarder."
    )
    longest = compile_field_goal_extremum(
        "How many yards was the longest field goal?", passage)
    shortest = compile_field_goal_extremum(
        "How many yards was the shortest field goal?", passage)
    assert longest is not None and longest.result == 54
    assert shortest is not None and shortest.result == 24
    assert tuple(item.value for item in longest.observations) == (24, 54, 31, 50, 53)


def test_missed_or_declined_attempt_never_enters_extremum():
    passage = (
        "A made a 49-yard field goal. B missed a 55-yard field goal. Instead of "
        "kicking a 51-yard field goal, the team ran a play."
    )
    proof = compile_field_goal_extremum(
        "How many yards was the longest field goal?", passage)
    assert proof is not None and proof.result == 49
