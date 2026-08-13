# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.executable_behavior import ObservableBehaviorMachine
from horizon_memory.raw_causal_channels import RawCausalDocument


def _machine():
    return ObservableBehaviorMachine((
        RawCausalDocument(1, "I started a dance studio because I love dancing", 0, 0, "Jon"),
        RawCausalDocument(2, "I made colorful pottery", 0, 1, "Caroline"),
        RawCausalDocument(3, "Seven years now, painting has been calming", 1, 0, "Melanie"),
        RawCausalDocument(4, "Contemporary dance is my top pick", 1, 1, "Jon"),
    ))


def test_explicit_cause_executes_and_returns_exact_provenance():
    result = _machine().execute("Why did Jon start a dance studio?")
    assert result.state == "committed"
    assert result.fact_ids == (1,)
    assert result.value_spans == ("I love dancing",)


def test_missing_cause_abstains_instead_of_answering_by_topic():
    result = _machine().execute("Why did Caroline make colorful pottery?")
    assert result.state == "abstain"


def test_duration_requires_number_unit_and_target_in_one_transition():
    result = _machine().execute("How long has Melanie been painting?")
    assert result.state == "committed"
    assert result.fact_ids == (3,)
    assert result.value_spans == ("seven years",)


def test_counterfactual_requires_authorized_possibility_edge():
    result = _machine().execute("Would Melanie likely enjoy sculpture?")
    assert result.state == "unsupported"
    assert result.missing_slots == ("authorized_possibility_edge",)


def test_open_world_collective_requires_completeness_certificate():
    result = _machine().execute("What activities has Melanie done with all her family?")
    assert result.state == "unsupported"
    assert result.missing_slots == ("closed_world_certificate",)


def test_lookup_state_is_not_confused_with_causal_execution():
    result = _machine().execute("What is Jon's favorite dance?")
    assert result.operator == "LOOKUP_STATE"
    assert result.fact_ids == (4,)


def test_structural_cause_candidates_preserve_paraphrase_reachability():
    machine = _machine()
    ranked = machine.rank_structural_candidates(
        "Why did Jon create a place where people can learn movement?")
    assert ranked == (1,)


def test_structural_duration_candidates_exclude_number_without_time_unit():
    machine = ObservableBehaviorMachine((
        RawCausalDocument(1, "I painted three canvases", 0, 0, "Melanie"),
        RawCausalDocument(2, "Seven years now, painting has been calming", 1, 0, "Melanie"),
    ))
    assert machine.rank_structural_candidates(
        "How long has Melanie been painting?") == (2,)
