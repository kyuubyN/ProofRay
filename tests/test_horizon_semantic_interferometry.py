# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.semantic_interferometry import (
    SemanticInterferometer, SemanticMode, SurfacePerformance,
)
from horizon_memory.unified_causal_field import HorizonUnifiedCausalField


def _channels(*values):
    return tuple(sorted(values))


def _modes():
    return tuple(sorted((
        SemanticMode("team", "buy", _channels(
            "exchange:payment", "transition:ownership", "role:object"), 10, 1),
        SemanticMode("team", "deploy", _channels(
            "environment:production", "transition:release", "role:object"), 20, 1),
    )))


def _resolve(uses):
    field = HorizonUnifiedCausalField()
    field.begin_breath("team", 1)
    for edge in SemanticInterferometer(_modes(), min_coverage=2 / 3).project(
            "team", "cop", tuple(sorted(uses)), 5):
        field.inhale(edge)
    field.exhale("team", 99)
    return field.resolve("team")


def test_unknown_jargon_resolves_from_independent_relational_performances():
    result = _resolve((
        SurfacePerformance("team", "cop", _channels(
            "exchange:payment", "transition:ownership", "role:object"), 101, 2),
        SurfacePerformance("team", "cop", _channels(
            "exchange:payment", "transition:ownership", "role:object"), 102, 3),
        SurfacePerformance("team", "cop", _channels(
            "environment:production", "role:object"), 103, 4),
    ))
    assert result.state == "resolved"
    assert result.canonical == "buy"
    assert set(result.evidence_fact_ids).issubset({10, 101, 102})


def test_one_context_cannot_manufacture_a_semantic_identity():
    result = _resolve((SurfacePerformance(
        "team", "cop", _channels(
            "exchange:payment", "transition:ownership", "role:object"), 101, 2),))
    assert result.state == "abstain"


def test_ironic_phase_uses_same_field_as_a_hard_negative():
    result = _resolve((
        SurfacePerformance("team", "cop", _channels(
            "exchange:payment", "transition:ownership", "role:object"), 101, 2),
        SurfacePerformance("team", "cop", _channels(
            "exchange:payment", "transition:ownership", "role:object"), 102, 3,
                           pragmatic="ironic"),
    ))
    assert result.state == "abstain"
    assert 102 in result.evidence_fact_ids


def test_future_performance_does_not_leak_backwards():
    uses = tuple(sorted((
        SurfacePerformance("team", "cop", _channels(
            "exchange:payment", "transition:ownership", "role:object"), 101, 2),
        SurfacePerformance("team", "cop", _channels(
            "exchange:payment", "transition:ownership", "role:object"), 102, 9),
    )))
    edges = SemanticInterferometer(_modes(), min_coverage=2 / 3).project(
        "team", "cop", uses, 5)
    assert edges == ()
