# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.homogeneous_difference_proof import (
    compile_homogeneous_difference,
)


def test_explicit_homogeneous_directional_difference() -> None:
    question = "How many more yards was the 44 yards goal compared to the 27 yards goal?"
    proof = compile_homogeneous_difference(question)
    assert proof is not None and proof.result == 17 and proof.verify(question)


def test_direction_mismatch_and_non_difference_semantics_abstain() -> None:
    for question in (
        "How many more yards was the 27 yards goal compared to the 44 yards goal?",
        "How many yards were accumulated between 30-yards and 50-yards?",
        "How many total yards were the 44 yards and 27 yards goals?",
    ):
        assert compile_homogeneous_difference(question) is None


def test_requires_two_explicit_same_unit_operands_and_tamper_fails() -> None:
    assert compile_homogeneous_difference("How many more yards was the 44 yard goal compared to it?") is None
    question = "How many more yards was the 44 yard goal compared to the 41 yard goal?"
    proof = compile_homogeneous_difference(question)
    assert proof is not None and not proof.verify(question.replace("41", "40"))
