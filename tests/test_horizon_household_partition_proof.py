# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.household_partition_proof import compile_household_partition


def test_children_partition_is_bound_to_household_universe() -> None:
    passage = "There were 810,388 households out of which 28.61% had children under the age of 18 living with them."
    question = "How many in percent of households didn't have children under the age of 18 living with them?"
    proof = compile_household_partition(question, passage)
    assert proof is not None and proof.result == "71.39" and proof.verify(question, passage)


def test_non_families_partition_entails_families() -> None:
    passage = "There were 23,686 households of which 34.5% were non-families."
    question = "How many in percent of households were families?"
    proof = compile_household_partition(question, passage)
    assert proof is not None and proof.result == "65.5"


def test_people_scope_and_invalid_rewrites_abstain() -> None:
    passage = "There were 100 households and 34.5% of the population were non-families."
    assert compile_household_partition("How many in percent of people were families?", passage) is None
    assert compile_household_partition(
        "How many in percent of households had a female householder with a husband present?", passage,
    ) is None


def test_conflict_and_source_tamper_fail_closed() -> None:
    question = "How many in percent of households were families?"
    conflict = "There were 100 households; 30% were non-families. There were 50 households; 40% were non-families."
    assert compile_household_partition(question, conflict) is None
    passage = "There were 100 households; 30% were non-families."
    proof = compile_household_partition(question, passage)
    assert proof is not None and not proof.verify(question, passage.replace("30%", "31%"))
