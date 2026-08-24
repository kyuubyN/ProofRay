# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.percentage_complement_proof import compile_percentage_complement


def test_unique_category_percentage_builds_decimal_exact_proof() -> None:
    passage = "The population was 22.5% German people, 13.1% Irish people, and 9.8% Italian people."
    question = "How many percent were not German?"
    proof = compile_percentage_complement(question, passage)
    assert proof is not None and proof.result == "77.5" and proof.verify(question, passage)


def test_numeric_age_predicate_selects_exact_item() -> None:
    passage = "There were 24.90% under the age of 18, 7.20% from 18 to 24, and 16.30% who were 65 years of age or older."
    proof = compile_percentage_complement("How many in percent weren't 18 to 24?", passage)
    assert proof is not None and proof.result == "92.8"


def test_bare_numeric_conjunction_is_not_silently_rewritten_as_interval() -> None:
    passage = "There were 56.2% between the ages of 18 and 24."
    assert compile_percentage_complement(
        "How many in percent weren't 18 and 24?", passage) is None
    proof = compile_percentage_complement(
        "How many in percent weren't between 18 and 24?", passage)
    assert proof is not None and proof.result == "43.8"


def test_ambiguous_predicate_and_approximate_fact_abstain() -> None:
    ambiguous = "There were 0.4% American Indian residents, and 7.4% Americans."
    assert compile_percentage_complement("How many percent were not American?", ambiguous) is None
    approximate = "About 10% were members of the group."
    assert compile_percentage_complement("How many percent were not members?", approximate) is None


def test_non_complement_and_source_tamper_fail() -> None:
    passage = "There were 22.5% German people."
    assert compile_percentage_complement("Which group was 22.5 percent?", passage) is None
    question = "How many percent were not German?"
    proof = compile_percentage_complement(question, passage)
    assert proof is not None and not proof.verify(question, passage.replace("22.5", "23.5"))


def test_union_and_parenthetical_scope_leak_abstain() -> None:
    passage = "Around 55% were born in Australia with 8.7% born in China."
    assert compile_percentage_complement(
        "How many percent were not born in Australia or China?", passage,
    ) is None
    nested = "11.07% (3.31% male and 7.76% female) had someone living alone who was 65 or older."
    assert compile_percentage_complement(
        "How many percent were not someone living alone who was 65 or older?", nested,
    ) is None


def test_country_opinion_percentage_is_not_a_population_complement() -> None:
    passage = (
        "Surveyed countries with a positive view of China included Pakistan (78%), "
        "Bangladesh (77%), and Malaysia (74%)."
    )
    assert compile_percentage_complement(
        "How many percent were not Bangladesh?", passage) is None
