# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.event_year_alignment import (
    compile_closed_event_year_interval,
    compile_event_year_interval,
)


def test_two_unique_event_clauses_build_verified_interval() -> None:
    passage = (
        "The Triennial Act became law in 1694 after a long debate. "
        "Parliament enacted the Septennial Act in 1715 after a separate campaign."
    )
    question = "How many years passed between the Triennial Act becoming law and Parliament enacting the Septennial Act?"
    proof = compile_event_year_interval(question, passage)
    assert proof is not None and proof.result == 21 and proof.verify(question, passage)


def test_ambiguous_runner_up_and_same_clause_fail_closed() -> None:
    ambiguous = (
        "The northern treaty was signed in 1694. The southern treaty was signed in 1700. "
        "The Septennial Act became law in 1715."
    )
    question = "How many years passed between the treaty being signed and the Septennial Act becoming law?"
    assert compile_event_year_interval(question, ambiguous) is None
    same = "The Triennial Act passed in 1694 and the Septennial Act passed in 1715."
    assert compile_event_year_interval(question, same) is None


def test_requires_three_literal_anchors_and_one_year_per_clause() -> None:
    passage = "War began in 1694. Parliament enacted the Septennial Act in 1715 and revised it in 1716."
    question = "How many years passed between war and Parliament enacting the Septennial Act?"
    assert compile_event_year_interval(question, passage) is None


def test_source_or_question_tamper_invalidates_proof() -> None:
    passage = "The Triennial Act became law in 1694. Parliament enacted the Septennial Act in 1715."
    question = "How many years passed between the Triennial Act becoming law and Parliament enacting the Septennial Act?"
    proof = compile_event_year_interval(question, passage)
    assert proof is not None
    assert not proof.verify(question, passage.replace("1715", "1716"))


def test_question_with_explicit_year_belongs_to_question_grounded_compiler() -> None:
    passage = "The Triennial Act became law in 1694. Parliament enacted the Septennial Act in 1715."
    question = "How many years after the 1694 Act did Parliament enact the Septennial Act?"
    assert compile_event_year_interval(question, passage) is None


def test_closed_temporal_universe_requires_exactly_the_aligned_years() -> None:
    question = "How many years passed between the Triennial Act becoming law and Parliament enacting the Septennial Act?"
    closed = "The Triennial Act became law in 1694. Parliament enacted the Septennial Act in 1715."
    assert compile_closed_event_year_interval(question, closed) is not None
    third = closed + " A preliminary debate occurred in 1690."
    assert compile_closed_event_year_interval(question, third) is None
