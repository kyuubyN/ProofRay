# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.closed_score_universe import compile_closed_score_margin


def test_one_score_and_explicit_outcome_close_margin() -> None:
    passage = "Dallas preserved the 27-16 win. With the win, Dallas improved to 2-0."
    question = "How many points did Dallas win by?"
    proof = compile_closed_score_margin(question, passage)
    assert proof is not None and proof.result == 11 and proof.verify(question, passage)


def test_loss_role_can_be_separate_terminal_sentence() -> None:
    passage = "San Diego lost 22-10. With the loss, San Diego fell to 2-3."
    proof = compile_closed_score_margin("How many points did San Diego lose by?", passage)
    assert proof is not None and proof.result == 12


def test_multiple_or_partial_scores_abstain() -> None:
    assert compile_closed_score_margin(
        "How many points did Dallas win by?", "Dallas won 27-16 after leading 14-7.",
    ) is None
    assert compile_closed_score_margin(
        "How many points did Dallas win by?", "Dallas led 27-16 at halftime and won.",
    ) is None
    assert compile_closed_score_margin(
        "How many points did Dallas win by?", "Dallas won 27-16 after the game was tied 7-7.",
    ) is None


def test_record_retrospective_role_conflict_and_tamper_fail() -> None:
    assert compile_closed_score_margin(
        "How many points did Dallas win by?", "Dallas had a 3-1 record and celebrated the win.",
    ) is None
    assert compile_closed_score_margin(
        "How many points did Dallas win by?", "Dallas won 27-16, but with the loss Dallas fell to 2-1.",
    ) is None
    assert compile_closed_score_margin(
        "How many points did Dallas win?", "Dallas won 27-16.",
    ) is None
    passage = "Dallas won 27-16."
    proof = compile_closed_score_margin("How many points did Dallas win by?", passage)
    assert proof is not None and not proof.verify("How many points did Dallas win by?", passage.replace("27", "28"))
