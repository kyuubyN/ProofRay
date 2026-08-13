# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.racial_filter_count_proof import compile_racial_filter_count


PASSAGE = (
    "The racial makeup was 81.4% white, 13.4% black or African American, 1.4% Asian, "
    "0.4% American Indian, 0.7% from other races, and 2.7% from two or more races."
)


def test_closed_partition_filter_count_is_recomputable() -> None:
    question = "How many racial groups each made up less than 1% of the population?"
    proof = compile_racial_filter_count(question, PASSAGE)
    assert proof is not None and proof.result == 2 and proof.verify(question, PASSAGE)
    assert proof.selected_categories == ("native_american", "other")


def test_inclusive_and_open_intervals_are_distinct() -> None:
    assert compile_racial_filter_count(
        "How many races made up no more than 1.4% of the population?", PASSAGE,
    ).result == 3
    assert compile_racial_filter_count(
        "How many races made up more than 0.4% but less than 2.7% of the population?", PASSAGE,
    ).result == 2


def test_incomplete_or_duplicate_partition_abstains() -> None:
    assert compile_racial_filter_count(
        "How many races made up less than 2% of the population?",
        "The racial makeup was 60% white and 40% Asian.",
    ) is None
    assert compile_racial_filter_count(
        "How many races made up less than 2% of the population?",
        "The racial makeup was 50% white, 40% black, 5% Asian, 3% Asian, and 2% other races.",
    ) is None


def test_non_count_and_source_tamper_fail() -> None:
    assert compile_racial_filter_count("Which racial groups made up less than 2%?", PASSAGE) is None
    question = "How many races made up more than 10% of the population?"
    proof = compile_racial_filter_count(question, PASSAGE)
    assert proof is not None and not proof.verify(question, PASSAGE.replace("13.4", "12.4"))
