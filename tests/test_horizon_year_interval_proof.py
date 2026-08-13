# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.year_interval_proof import compile_year_interval


def test_explicit_year_difference_builds_verified_proof() -> None:
    question = "How many years passed between the Triennial Act in 1694 and the Septennial Act in 1715?"
    proof = compile_year_interval(question)
    assert proof is not None and proof.result == 21
    assert [item.value for item in proof.operands] == [1694, 1715]
    assert proof.verify(question)


def test_directional_or_duration_language_is_not_silently_equated() -> None:
    assert compile_year_interval("How many years before 2010 was the earlier event in 1998?") is None
    assert compile_year_interval("How many years did the peace from 1435 last until 1439?") is None


def test_requires_exactly_two_standalone_full_years() -> None:
    for question in (
        "How many years after 1998 was the event?",
        "How many years between 1998, 2001, and 2010?",
        "How many years between 1998 and 04?",
        "How many years between the 1990s and 2010?",
        "How many years between 2004-05 and 2010?",
    ):
        assert compile_year_interval(question) is None


def test_rejects_non_elapsed_semantics_and_tamper() -> None:
    assert compile_year_interval("Which event occurred in 1998 rather than 2010?") is None
    question = "How many years passed between 1998 and 2010?"
    proof = compile_year_interval(question)
    assert proof is not None and not proof.verify(question.replace("2010", "2011"))
