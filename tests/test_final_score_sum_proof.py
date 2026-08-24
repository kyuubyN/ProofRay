from horizon_memory.final_score_sum_proof import (
    compile_final_score_margin, compile_final_score_sum,
)


def test_unique_explicit_final_score_sums_and_reopens():
    question = "How many total points were scored?"
    passage = "The final score was 27-23 after a close game."
    proof = compile_final_score_sum(question, passage)
    assert proof is not None and proof.result == 50
    assert proof.verify(question, passage)
    assert not proof.verify(question, passage.replace("23", "24"))


def test_supported_question_paraphrases_are_totally_consumed():
    passage = "They ended with the final score of 31–17."
    for question in (
        "How many points were scored in the game?",
        "How many points in total were scored?",
        "How many points total were scored in the game?",
        "How many points were scored total?",
        "How many total points were scored by the end of the game?",
        "How many total points were scored by both teams?",
        "How many total points did both teams score in the game?",
        "How many points did both teams score in total?",
    ):
        proof = compile_final_score_sum(question, passage)
        assert proof is not None and proof.result == 48


def test_multiple_terminal_scores_or_unmarked_score_abstain():
    question = "How many total points were scored?"
    assert compile_final_score_sum(
        question, "The final score was 20-10; another final score was 14-7.") is None
    assert compile_final_score_sum(question, "The score was 20-10.") is None


def test_non_total_question_does_not_compile():
    assert compile_final_score_sum(
        "How many points did the home team score?", "The final score was 20-10.") is None


def test_terminal_score_margin_uses_absolute_difference_and_reopens():
    passage = "The Tigers beat the Bears; the final score was 27-23 after a close game."
    for question in (
        "How many points did the Tigers win by?",
        "How many points did they lose by?",
        "How many points did the Tigers beat the Bears by?",
        "How many points difference was there between the winning and losing team?",
    ):
        proof = compile_final_score_margin(question, passage)
        assert proof is not None and proof.result == 4
        assert proof.verify(question, passage)


def test_period_specific_margin_does_not_use_final_score():
    assert compile_final_score_margin(
        "How many points did they win by at halftime?", "The final score was 27-23.") is None


def test_named_subject_must_bind_to_terminal_score_frame():
    passage = (
        "PSV were beaten 0-1. A later Ajax match had a final score of 1-1."
    )
    assert compile_final_score_margin(
        "How many points did the PSV lose by?", passage) is None
