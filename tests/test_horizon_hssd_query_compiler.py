# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from horizon_memory.hssd_query_compiler import (
    HSSDEvidenceObservation,
    StructuralHSSDQueryCompiler,
)


@pytest.mark.parametrize(("question", "operation"), (
    ("When did Mina launch the expedition?", "lookup_time"),
    ("What date did Mina launch the expedition?", "lookup_time"),
    ("Who repaired the telescope?", "lookup_person"),
    ("Where did Mina store the telescope?", "lookup_place"),
    ("How many telescopes did Mina repair?", "count_distinct"),
    ("What was the total in dollars?", "sum"),
    ("How long did the expedition last?", "duration"),
    ("What was the time between launch and landing?", "interval"),
    ("Why did the telescope fail?", "explain_cause"),
    ("Did Mina repair the telescope?", "exists"),
))
def test_structural_operator_is_independent_of_domain_vocabulary(question, operation):
    assert StructuralHSSDQueryCompiler().compile(question).operation == operation


def test_address_terms_are_not_misrepresented_as_proof_obligations():
    plan = StructuralHSSDQueryCompiler().compile("When did Mina launch the expedition?")
    assert "launch" in plan.address_atoms.lexical
    assert "expedition" in plan.address_atoms.lexical
    assert all(not item.key.startswith("lexical:") for item in plan.obligations)
    assert {item.key for item in plan.obligations} >= {
        "proof:identity", "support:selector", "slot:clock"}


def test_verified_typed_time_evidence_closes_execution_without_repeating_every_query_word():
    compiler = StructuralHSSDQueryCompiler()
    plan = compiler.compile("When did Mina launch the expedition?")
    closure = compiler.assess(plan, (HSSDEvidenceObservation(
        7, lexical=("launch",), entities=("Mina",), clocks=("event_time",),
        proof_verified=True),))
    assert closure.retrieval_closed
    assert closure.execution_ready
    assert closure.residual == ()


def test_count_requires_closed_world_and_cannot_be_closed_by_relevance():
    compiler = StructuralHSSDQueryCompiler()
    plan = compiler.compile("How many telescopes did Mina repair?")
    incomplete = compiler.assess(plan, (HSSDEvidenceObservation(
        8, lexical=("telescope", "repair"), entities=("Mina",),
        distinct_keys=("t1", "t2"), proof_verified=True),))
    assert incomplete.retrieval_closed
    assert not incomplete.execution_ready
    assert "proof:complete" in incomplete.residual
    ready = compiler.assess(plan, (HSSDEvidenceObservation(
        8, lexical=("telescope", "repair"), entities=("Mina",),
        distinct_keys=("t1", "t2"), proof_verified=True, complete=True),))
    assert ready.execution_ready


def test_conflict_is_noncompensable_even_with_all_positive_charges():
    compiler = StructuralHSSDQueryCompiler()
    plan = compiler.compile("Who repaired the telescope?")
    closure = compiler.assess(plan, (HSSDEvidenceObservation(
        9, lexical=("repair", "telescope"), roles=("person",),
        proof_verified=True, conflict=True),))
    assert closure.state == "conflict"
    assert not closure.execution_ready


def test_unsupported_or_conflicting_operator_abstains():
    compiler = StructuralHSSDQueryCompiler()
    assert compiler.compile("Describe it.").state == "abstain"
    assert compiler.compile("Who and where was it?").state == "abstain"


def test_operator_lattice_preserves_count_sum_ambiguity_without_changing_legacy_api():
    compiler = StructuralHSSDQueryCompiler()
    question = "How many hours in total did I spend driving?"
    assert compiler.compile(question).state == "abstain"
    lattice = compiler.compile_lattice(question)
    assert lattice.state == "ambiguous"
    assert tuple(plan.operation for plan in lattice.plans) == ("count_distinct", "sum")
    assert all(plan.state == "compiled" for plan in lattice.plans)


@pytest.mark.parametrize("question", (
    "Can you remind me of the website you recommended?",
    "How often do I attend yoga classes?",
    "Any tips for keeping my kitchen clean?",
))
def test_operator_lattice_retains_prior_content_lookup_gauges(question):
    lattice = StructuralHSSDQueryCompiler().compile_lattice(question)
    assert lattice.state == "compiled"
    assert tuple(plan.operation for plan in lattice.plans) == ("lookup",)


def test_how_much_lattice_does_not_guess_between_stored_scalar_and_sum():
    lattice = StructuralHSSDQueryCompiler().compile_lattice(
        "How much did I spend on the workshops?")
    assert lattice.state == "ambiguous"
    assert tuple(plan.operation for plan in lattice.plans) == ("lookup", "sum")
