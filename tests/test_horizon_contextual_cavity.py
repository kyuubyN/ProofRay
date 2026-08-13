# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.contextual_cavity import ContextualCavityIndex
from horizon_memory.raw_causal_channels import RawCausalDocument


def _docs():
    return (
        RawCausalDocument(1, "What happened at the poetry reading?", 0, 0),
        RawCausalDocument(2, "It celebrated transgender writers and their stories.", 0, 1),
        RawCausalDocument(3, "Unrelated cooking note", 0, 2),
        RawCausalDocument(4, "transgender writers", 1, 0),
    )


def test_neighbor_meaning_resonates_into_center_while_center_keeps_identity():
    result = ContextualCavityIndex(_docs()).rank("What celebrated transgender writers?")
    by_id = {item.fact_id: item for item in result}
    assert by_id[1].incoming > 0
    assert by_id[1].witness_fact_ids == (2,)
    assert by_id[1].fact_id == 1


def test_cavity_never_crosses_session_boundary():
    result = ContextualCavityIndex(_docs()).rank("transgender writers")
    by_id = {item.fact_id: item for item in result}
    assert 4 not in by_id[3].witness_fact_ids


def test_repeated_neighbors_cannot_sum_to_manufacture_mass():
    docs = (
        RawCausalDocument(1, "anchor", 0, 0),
        RawCausalDocument(2, "violet signal", 0, 1),
        RawCausalDocument(3, "violet signal", 0, 2),
    )
    result = ContextualCavityIndex(docs, radius=2, decay=1).rank("violet signal")
    center = {item.fact_id: item for item in result}[1]
    assert center.incoming == 1.0
    assert center.witness_fact_ids == (2, 3)


def test_direction_can_disable_future_to_past_propagation():
    result = ContextualCavityIndex(_docs(), forward_weight=0).rank("transgender writers")
    assert {item.fact_id: item for item in result}[1].incoming == 0


def test_radius_and_decay_bound_distant_influence():
    result = ContextualCavityIndex(_docs(), radius=2, decay=.25).rank("cooking")
    center = {item.fact_id: item for item in result}[1]
    assert 0 < center.incoming <= .25


def test_speaker_is_a_body_coordinate_not_text_frequency():
    docs = (
        RawCausalDocument(1, "I prefer contemporary dance", 0, 0, "Gina"),
        RawCausalDocument(2, "Gina, that sounds great", 0, 1, "Jon"),
    )
    ranked = ContextualCavityIndex(
        docs, forward_weight=0, backward_weight=0).rank(
            "What dance does Gina prefer?", speaker_weight=1)
    assert ranked[0].fact_id == 1


def test_session_scale_transfers_distant_ambient_signal_without_crossing_sessions():
    docs = (
        RawCausalDocument(1, "target", 0, 0),
        RawCausalDocument(2, "filler", 0, 1),
        RawCausalDocument(3, "filler", 0, 2),
        RawCausalDocument(4, "meteor shower", 0, 3),
        RawCausalDocument(5, "meteor shower", 1, 0),
    )
    result = ContextualCavityIndex(docs, radius=1).rank(
        "meteor shower", session_weight=.2)
    by_id = {item.fact_id: item for item in result}
    assert by_id[1].incoming > 0
    assert 4 in by_id[1].witness_fact_ids
    assert 5 not in by_id[1].witness_fact_ids


def test_local_and_session_views_use_max_not_duplicate_sum():
    docs = (RawCausalDocument(1, "center", 0, 0),
            RawCausalDocument(2, "violet", 0, 1))
    result = ContextualCavityIndex(docs).rank("violet", session_weight=1)
    assert {item.fact_id: item for item in result}[1].incoming == 1.0


def test_pragmatic_socket_is_an_independent_body_cavity_layer():
    docs = (RawCausalDocument(1, "I felt tiny and in awe", 0, 0, "Melanie"),
            RawCausalDocument(2, "ordinary meteor note", 0, 1, "Caroline"))
    ranked = ContextualCavityIndex(
        docs, forward_weight=0, backward_weight=0).rank(
            "How did Melanie feel?", speaker_weight=1, role_weight=1)
    assert ranked[0].fact_id == 1
