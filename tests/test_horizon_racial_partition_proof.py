# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.racial_partition_proof import compile_racial_partition


def test_racial_frame_binds_category_and_population() -> None:
    passage = "The racial makeup of the county was 81.4% white, 13.4% black or African American, and 1.4% Asian."
    proof = compile_racial_partition("How many percent of people were not white?", passage)
    assert proof is not None and proof.result == "18.6" and proof.verify(
        "How many percent of people were not white?", passage)


def test_closed_aliases_map_to_same_racial_property() -> None:
    passage = "The racial makeup was 9.26% Black or African American, and 0.4% American Indian."
    assert compile_racial_partition(
        "How many percent of the population were not African American?", passage,
    ).result == "90.74"
    assert compile_racial_partition(
        "How many percent of the population were not Native American?", passage,
    ).result == "99.6"


def test_non_hispanic_and_unframed_facts_do_not_launder_scope() -> None:
    passage = "The racial makeup was 5% white. Non-Hispanic Whites were 50.3% of people over 55."
    assert compile_racial_partition("How many percent of people were not white?", passage).result == "95"
    assert compile_racial_partition(
        "How many percent of people were not non-Hispanic Whites?", passage,
    ) is None


def test_ambiguous_duplicate_category_and_tamper_fail() -> None:
    question = "How many percent of people were not Asian?"
    conflict = "The racial makeup was 3% Asian and 4% Asian."
    assert compile_racial_partition(question, conflict) is None
    passage = "The racial makeup was 3% Asian."
    proof = compile_racial_partition(question, passage)
    assert proof is not None and not proof.verify(question, passage.replace("3%", "4%"))
