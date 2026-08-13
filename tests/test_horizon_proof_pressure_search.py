# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.proof_pressure_search import HorizonSearchEngine
from horizon_memory.raw_causal_channels import RawCausalDocument


def _engine():
    return HorizonSearchEngine((
        RawCausalDocument(1, "Mina planned the expedition", 0, 0, "Mina"),
        RawCausalDocument(2, "The launch happened on Tuesday", 0, 1, "Jon"),
        RawCausalDocument(3, "Mina cancelled the expedition", 0, 2, "Mina"),
        RawCausalDocument(4, "Unrelated cooking note", 1, 0, "Kai"),
    ), cavity_radius=1, core_width=1)


def test_compiler_declares_typed_hssd_obligations_without_a_model():
    obligations = _engine().compile_obligations("When did Mina launch the expedition?")
    keys = {item.key for item in obligations}
    assert "answer:time" in keys
    assert "lexical:mina" in keys
    assert "lexical:launch" in keys


def test_search_protects_direct_core_then_reduces_residual():
    result = _engine().search(
        "When did Mina launch the expedition?", max_results=4, exploration_reserve=2)
    assert result.fact_ids[0] in (1, 2)
    assert any(admission.reason == "residual_reduction" for admission in result.admissions)
    assert len(result.fact_ids) == len(set(result.fact_ids))
    assert result.bytes_selected == sum(
        len(_engine().by_id[fact_id].text.encode("utf-8")) for fact_id in result.fact_ids)


def test_hard_exclusion_dominates_core_and_all_surfaces():
    result = _engine().search("When was the launch?", hard_exclusions=(2,),
                              max_results=4, exploration_reserve=4)
    assert 2 not in result.fact_ids
    assert result.excluded == (2,)


def test_no_padding_without_explicit_exploration():
    result = _engine().search("cooking", max_results=4)
    assert result.fact_ids == (4,)
    assert all(item.reason != "explicit_exploration" for item in result.admissions)


def test_byte_budget_is_a_hard_noncompensable_gate():
    result = _engine().search("expedition", max_results=4, max_bytes=30,
                              exploration_reserve=4)
    assert result.bytes_selected <= 30
    assert all(item.byte_cost <= 30 for item in result.admissions)
