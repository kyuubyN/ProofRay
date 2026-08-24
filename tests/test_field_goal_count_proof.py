from horizon_memory.field_goal_count_proof import compile_field_goal_count


PASSAGE = (
    "In the first quarter, A made a 31-yard field goal. In the second quarter, "
    "B made a 44-yard field goal. In the third quarter, A made a 22-yard field "
    "goal. In the fourth quarter, B made a 49-yard field goal."
)


def test_counts_complete_game_and_scoped_enumerations():
    whole = compile_field_goal_count("How many field goals were made?", PASSAGE)
    half = compile_field_goal_count(
        "How many field goals were made in the first half?", PASSAGE)
    quarter = compile_field_goal_count(
        "How many field goals were kicked in the fourth quarter?", PASSAGE)
    assert whole is not None and whole.result == 4
    assert half is not None and half.result == 2
    assert quarter is not None and quarter.result == 1
    assert whole.verify("How many field goals were made?", PASSAGE)


def test_bare_kicker_yard_goal_is_part_of_closed_count():
    passage = ("The kicker nailed a 45-yard goal. Later another kicker made a "
               "21-yard field goal.")
    proof = compile_field_goal_count("How many field goals were made?", passage)
    assert proof is not None and proof.result == 2


def test_unmeasured_success_or_player_specific_question_abstains():
    assert compile_field_goal_count(
        "How many field goals were made?",
        "A made a 31-yard field goal and later made another field goal.") is None
    assert compile_field_goal_count(
        "How many field goals did A make?", PASSAGE) is None


def test_after_the_break_opens_second_half_scope():
    passage = (
        "In the first quarter, A kicked a field goal from 45 yards out. "
        "After the break, A kicked another field goal from 42 yards out."
    )
    proof = compile_field_goal_count(
        "How many field goals were kicked in the first half?", passage)
    assert proof is not None and proof.result == 1


def test_only_score_of_third_quarter_changes_scope():
    passage = (
        "In the first quarter, A made a 51-yard field goal. In the second quarter, "
        "A made a 49-yard and a 30-yard field goal. The only score of the third "
        "quarter was a 54-yard field goal."
    )
    proof = compile_field_goal_count(
        "How many field goals were scored during the first half?", passage)
    assert proof is not None and proof.result == 3


def test_pair_dash_coordination_and_abbreviated_scope_are_counted():
    passage = (
        "In the first quarter, A made 23- and 27-yard field goals. In the 2nd, "
        "B got a pair of 32-yard field goals. In the 3rd, B made a 40-yard FG."
    )
    first = compile_field_goal_count(
        "How many field goals were made in the first quarter?", passage)
    second = compile_field_goal_count(
        "How many field goals were made in the second quarter?", passage)
    assert first is not None and first.result == 2
    assert second is not None and second.result == 2


def test_trailing_quarter_after_event_and_overtime_boundary():
    passage = (
        "A made a 26-yard field goal in the first quarter. In the fourth quarter, "
        "A made a 21-yard field goal. In overtime, A made a 22-yard field goal."
    )
    first = compile_field_goal_count(
        "How many field goals were made in the first quarter?", passage)
    fourth = compile_field_goal_count(
        "How many field goals were made in the fourth quarter?", passage)
    assert first is not None and first.result == 1
    assert fourth is not None and fourth.result == 1


def test_timeout_invalidated_kick_is_not_counted():
    passage = (
        "A kicked a 51-yard field goal. They got the kick, but the coach called "
        "timeout. When they tried to kick again, a penalty shortened the field goal "
        "attempt, and A nailed a 36-yard field goal."
    )
    proof = compile_field_goal_count("How many field goals were made?", passage)
    assert proof is not None and proof.result == 1
