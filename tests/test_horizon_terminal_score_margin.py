# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.terminal_score_margin import compile_terminal_score_margin


def test_explicit_defeat_score_authorizes_winner_margin() -> None:
    passage = "The Dallas Cowboys defeated the Green Bay Packers, 27-16."
    question = "How many points did the Cowboys win by?"
    proof = compile_terminal_score_margin(question, passage)
    assert proof is not None and proof.result == 11 and proof.verify(question, passage)


def test_explicit_loss_maps_scores_to_roles() -> None:
    passage = "San Diego lost 22-10 to the Denver Broncos."
    proof = compile_terminal_score_margin("How many points did San Diego lose by?", passage)
    assert proof is not None and proof.result == 12


def test_intermediate_or_ambiguous_scores_abstain() -> None:
    assert compile_terminal_score_margin(
        "How many points did Dallas win by?", "Dallas led 14-7 at halftime.",
    ) is None
    passage = "Dallas beat Green Bay 27-16. Dallas beat New York 20-10."
    assert compile_terminal_score_margin("How many points did Dallas win by?", passage) is None


def test_role_mismatch_and_source_tamper_fail() -> None:
    passage = "Dallas beat Green Bay 27-16."
    assert compile_terminal_score_margin("How many points did Green Bay win by?", passage) is None
    proof = compile_terminal_score_margin("How many points did Dallas win by?", passage)
    assert proof is not None and not proof.verify("How many points did Dallas win by?", passage.replace("27", "28"))


def test_retrospective_score_is_not_current_terminal_authority() -> None:
    passage = (
        "This meeting marked the anniversary of their 1983 game, in which Green Bay beat "
        "Washington 48-47 in the highest-scoring game in history."
    )
    assert compile_terminal_score_margin("How many points did Green Bay win by?", passage) is None
