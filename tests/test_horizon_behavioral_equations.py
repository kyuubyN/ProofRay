# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.behavioral_equations import BehavioralEquationIndex
from horizon_memory.raw_causal_channels import RawCausalDocument


def _index():
    return BehavioralEquationIndex((
        RawCausalDocument(1, "I felt tiny and in awe of the universe", 0, 0, "Melanie"),
        RawCausalDocument(2, "That sounds great", 0, 1, "Caroline"),
        RawCausalDocument(3, "Contemporary is my top pick", 1, 0, "Jon"),
        RawCausalDocument(4, "I am a fan of classical music", 1, 1, "Gina"),
    ))


def test_agent_and_emotion_bind_on_the_same_fact():
    ranked = _index().rank("How did Melanie feel?")
    assert ranked[0].fact_id == 1
    assert ranked[0].agent_binding == ranked[0].role_binding == 1


def test_another_speakers_matching_role_cannot_be_borrowed():
    ranked = _index().rank("What is Caroline's favorite?")
    by_id = {item.fact_id: item for item in ranked}
    assert by_id[3].role_binding == 1
    assert by_id[3].agent_binding == 0
    assert by_id[3].amplitude < 1


def test_counterfactual_projection_requires_a_disposition_role():
    ranked = _index().rank("Would Gina likely enjoy a new song?")
    by_id = {item.fact_id: item for item in ranked}
    assert by_id[4].possibility == 1
    assert by_id[2].possibility == 0


def test_collective_and_counterfactual_are_explicit_equation_flags():
    index = _index()
    assert index.equation("What activities has Melanie done with all her family?").collective
    assert index.equation("Would Jon probably enjoy it?").counterfactual


def test_factid_is_preserved_as_its_own_behavior_witness():
    result = _index().rank("How did Melanie feel?")[0]
    assert result.witness_fact_ids == (result.fact_id,)


def test_purpose_question_without_same_fact_domain_cause_abstains():
    index = BehavioralEquationIndex((
        RawCausalDocument(1, "I made colorful pottery", 0, 0, "Caroline"),
        RawCausalDocument(2, "I did it because hiking is calming", 0, 1, "Caroline"),
    ))
    result = index.close("Why did Caroline use patterns in her pottery?")
    assert result.state == "abstain"
    assert "domain" in result.missing_slots or "role" in result.missing_slots


def test_explicit_same_fact_purpose_can_close_with_provenance():
    index = BehavioralEquationIndex((
        RawCausalDocument(1, "I started a dance studio because I love dancing", 0, 0, "Jon"),
        RawCausalDocument(2, "I started a fashion shop because I love clothes", 0, 1, "Gina"),
    ))
    result = index.close("Why did Jon start a dance studio?")
    assert result.state == "committed"
    assert result.evidence_fact_ids == (1,)


def test_counterfactual_disposition_never_masquerades_as_entailment():
    result = _index().close("Would Gina likely enjoy a new classical song?")
    assert result.state == "abstain"
    assert result.missing_slots == ("external_possibility_model",)
