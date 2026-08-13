# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.observer_perspective_compiler import (
    CertifiedPropagation, ObserverPerspectiveCompiler,
)
from horizon_memory.raw_causal_channels import RawCausalDocument


def _top_fact(compilation):
    members = dict(compilation.orbit_members)
    edge = max(compilation.projection.edges, key=lambda item: item.amplitude)
    return members[edge.canonical.split(":", 1)[1]][0]


def test_latest_is_an_observer_coordinate_not_a_global_recency_weight():
    compiler = ObserverPerspectiveCompiler((
        RawCausalDocument(1, "Maya works at the old observatory.", 0, 0),
        RawCausalDocument(2, "Maya works at the coastal laboratory.", 10, 0),
    ), "scope")
    result = compiler.compile("Where does Maya work currently?")
    assert result.observer.target_time == 10
    assert _top_fact(result) == 2


def test_explicit_temporal_mode_moves_perspective_to_distant_event():
    compiler = ObserverPerspectiveCompiler((
        RawCausalDocument(1, "On Monday Maya visited the observatory.", 1, 0),
        RawCausalDocument(2, "On Tuesday Maya visited the laboratory.", 9, 0),
    ), "scope")
    result = compiler.compile("Where did Maya visit on Monday?")
    assert result.observer.target_time == 1
    assert _top_fact(result) == 1


def test_duplicate_reports_share_one_massive_orbit_but_keep_provenance_members():
    compiler = ObserverPerspectiveCompiler((
        RawCausalDocument(1, "Maya bought seven sensors.", 1, 0),
        RawCausalDocument(2, "Maya bought seven sensors.", 2, 0),
        RawCausalDocument(3, "Liam deployed the server.", 3, 0),
    ), "scope")
    result = compiler.compile("What did Maya buy?")
    matching = [members for _, members in result.orbit_members if set(members) == {1, 2}]
    assert matching == [(1, 2)]
    assert result.projection.projected_orbits == 1
    assert result.projection.cancelled_orbits == 1


def test_declared_number_conflict_is_repulsive_but_missing_number_is_unknown():
    compiler = ObserverPerspectiveCompiler((
        RawCausalDocument(1, "The team ordered 7 sensors.", 1, 0),
        RawCausalDocument(2, "The team ordered sensors.", 1, 1),
        RawCausalDocument(3, "The team ordered 9 sensors.", 1, 2),
    ), "scope")
    result = compiler.compile("Did the team order 7 sensors?")
    members = dict(result.orbit_members)
    by_fact = {fid: edge for edge in result.projection.edges
               for fid in members[edge.canonical.split(":", 1)[1]]}
    assert by_fact[1].amplitude > 0
    assert by_fact[2].amplitude > 0
    assert by_fact[3].amplitude < 0
    assert by_fact[3].hard_negative is True


def test_lexically_dark_event_needs_a_certified_relational_path_to_gain_gravity():
    compiler = ObserverPerspectiveCompiler((
        RawCausalDocument(1, "The cobalt bird reached the hidden garden.", 1, 0),
        RawCausalDocument(2, "Maya discussed an unrelated recipe.", 1, 1),
    ), "scope")
    without_bridge = compiler.compile("Where did the blue messenger arrive?")
    with_bridge = compiler.compile("Where did the blue messenger arrive?", (
        CertifiedPropagation(1, "relational_bridge", 1.0, 2.0),
    ))
    members = dict(with_bridge.orbit_members)
    bridged = next(edge for edge in with_bridge.projection.edges
                   if 1 in members[edge.canonical.split(":", 1)[1]])
    assert without_bridge.projection.projected_orbits == 0
    assert bridged.amplitude > 0
