# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.percentage_lookup_proof import compile_percentage_lookup


def test_direct_predicate_lookup_is_verified() -> None:
    question = "How many percent were illiterate?"
    passage = "At the census, 16.79% were illiterate."
    proof = compile_percentage_lookup(question, passage)
    assert proof is not None and proof.result == "16.79" and proof.verify(question, passage)


def test_subject_object_lookup_preserves_literal_anchors() -> None:
    question = "How many percent of farms in Italy are located in northern Italy?"
    passage = "About 37% of farms in Italy are located in northern Italy."
    # Approximate facts are rejected by the shared D13 fact compiler.
    assert compile_percentage_lookup(question, passage) is None
    exact = passage.replace("About ", "")
    assert compile_percentage_lookup(question, exact).result == "37"


def test_derived_or_ambiguous_queries_abstain() -> None:
    passage = "There were 24.9% under 18 and 75.1% over 18."
    assert compile_percentage_lookup("How many percent were not under 18?", passage) is None
    assert compile_percentage_lookup("How many percent were higher than 20 percent?", passage) is None
