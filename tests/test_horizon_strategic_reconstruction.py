# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from horizon_memory.strategic_reconstruction import (
    ConstantEquation, CounterfactualStrategist, PlausibleWorld, StrategicComponent,
    VerifiedTacticOutcome,
)


def _component(name, abilities, compute=1, context=100, provenance=True, gate=True):
    return StrategicComponent(name, tuple(sorted(abilities)), compute, context, provenance, gate)


def _outcome(parts, world, correct, wrong=0, false=0, missed=0):
    return VerifiedTacticOutcome(tuple(sorted(parts)), world, correct, wrong, false, missed, 5, (99,))


def _worlds():
    return tuple(sorted((PlausibleWorld("common", .8, 1, ("stable",)),
                         PlausibleWorld("rare", .2, 2, ("novel",)))))


def test_strategist_reconstructs_complementary_machine_not_favorite_component():
    equation = ConstantEquation("q", ("body", "time"), 3, 500)
    components = tuple(sorted((_component("body", ("body",)),
                               _component("clock", ("time",)),
                               _component("favorite", ("body",)))))
    outcomes = tuple(sorted((_outcome(("body", "clock"), "common", 9, 1),
                             _outcome(("body", "clock"), "rare", 8, 2))))
    result = CounterfactualStrategist().reconstruct(
        equation, components, _worlds(), outcomes, issued_at=10)
    assert result.component_ids == ("body", "clock")


def test_worst_plausible_world_defeats_flashy_average_tactic():
    equation = ConstantEquation("q", ("solve",), 3, 500)
    components = tuple(sorted((_component("flashy", ("solve",)),
                               _component("robust", ("solve",)))))
    outcomes = tuple(sorted((
        _outcome(("flashy",), "common", 10), _outcome(("flashy",), "rare", 0, 10),
        _outcome(("robust",), "common", 8, 2), _outcome(("robust",), "rare", 7, 3),
    )))
    result = CounterfactualStrategist().reconstruct(
        equation, components, _worlds(), outcomes, issued_at=10)
    assert result.component_ids == ("robust",)


def test_no_paradigm_is_privileged_when_verified_behavior_changes():
    equation = ConstantEquation("q", ("solve",), 2, 500)
    components = tuple(sorted((_component("old", ("solve",)),
                               _component("new", ("solve",)))))
    outcomes = tuple(sorted((
        _outcome(("old",), "common", 4, 6), _outcome(("old",), "rare", 4, 6),
        _outcome(("new",), "common", 9, 1), _outcome(("new",), "rare", 9, 1),
    )))
    result = CounterfactualStrategist().reconstruct(
        equation, components, _worlds(), outcomes, issued_at=10, incumbent=("old",))
    assert result.component_ids == ("new",)
    assert result.reconstruction_distance == 2


def test_unproven_tactic_or_missing_equation_causes_abstention():
    equation = ConstantEquation("q", ("body", "time"), 2, 500)
    components = (_component("body", ("body",)),)
    result = CounterfactualStrategist().reconstruct(
        equation, components, _worlds(), (), issued_at=10)
    assert result.state == "abstain"


def test_provenance_and_contradiction_are_constants_not_tradeable_scores():
    equation = ConstantEquation("q", ("solve",), 2, 500)
    components = tuple(sorted((_component("unsafe", ("solve",), provenance=False),
                               _component("safe", ("solve",)))))
    outcomes = tuple(sorted((
        _outcome(("unsafe",), "common", 10), _outcome(("unsafe",), "rare", 10),
        _outcome(("safe",), "common", 8, 2), _outcome(("safe",), "rare", 8, 2),
    )))
    result = CounterfactualStrategist().reconstruct(
        equation, components, _worlds(), outcomes, issued_at=10)
    assert result.component_ids == ("safe",)


def test_context_and_compute_bounds_block_overpowered_reconstruction():
    equation = ConstantEquation("q", ("solve",), 1, 200)
    components = (_component("huge", ("solve",), compute=2, context=300),)
    outcomes = tuple(sorted((_outcome(("huge",), "common", 10),
                             _outcome(("huge",), "rare", 10))))
    assert CounterfactualStrategist().reconstruct(
        equation, components, _worlds(), outcomes, issued_at=10).state == "abstain"


def test_current_evaluation_cannot_reconstruct_its_own_strategy():
    equation = ConstantEquation("q", ("solve",), 2, 500)
    components = (_component("new", ("solve",)),)
    outcomes = (VerifiedTacticOutcome(("new",), "common", 10, 0, 0, 0, 10, (99,)),)
    with pytest.raises(ValueError):
        CounterfactualStrategist().reconstruct(
            equation, components, _worlds(), outcomes, issued_at=10)
