# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.carrier_percentage_complement import (
    compile_carrier_percentage_complement,
)


def test_population_carrier_preserves_category_complement() -> None:
    question = "How many percent of people were not white?"
    passage = "The racial makeup was 81.4% white, 13.4% black, and 1.4% Asian."
    proof = compile_carrier_percentage_complement(question, passage)
    assert proof is not None and proof.result == "18.6" and proof.verify(question, passage)


def test_carrier_does_not_replace_numeric_category_anchors() -> None:
    question = "How many percent of the county population were not from 18 to 24?"
    passage = "There were 24.9% under 18, 7.2% from 18 to 24, and 16.3% who were 65 or older."
    proof = compile_carrier_percentage_complement(question, passage)
    assert proof is not None and proof.result == "92.8"


def test_carrier_arithmetic_and_predicate_union_abstain() -> None:
    passage = "There were 81.4% white and 13.4% black residents."
    assert compile_carrier_percentage_complement(
        "How many percent of either group were not white?", passage,
    ) is None
    assert compile_carrier_percentage_complement(
        "How many percent of people were not white or black?", passage,
    ) is None
