# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.magic_shells import (
    ObserverShellClosure, ShellCandidate,
)


def test_magic_numbers_are_emergent_closed_shell_boundaries():
    result = ObserverShellClosure().close((
        ShellCandidate(1, 1.0, 2), ShellCandidate(2, 1.0, 2),
        ShellCandidate(3, 0.7, 1), ShellCandidate(4, 0.2, 1),
    ), 5)
    assert result.magic_numbers == (2, 3)
    assert result.admitted_fact_ids == (1, 2, 3)
    assert result.residual_shell.fact_ids == (4,)


def test_budget_never_splits_a_degenerate_shell_for_a_lucky_member():
    result = ObserverShellClosure().close((
        ShellCandidate(1, 1.0, 2), ShellCandidate(2, 1.0, 2),
        ShellCandidate(3, 1.0, 2),
    ), 4)
    assert result.admitted_fact_ids == ()
    assert result.residual_shell.fact_ids == (1, 2, 3)


def test_lucky_trajectories_vary_but_do_not_create_consensus():
    shell = ObserverShellClosure().shells((
        ShellCandidate(1, 1.0, 1), ShellCandidate(2, 1.0, 1),
        ShellCandidate(3, 1.0, 1),
    ))[0]
    trajectories = {ObserverShellClosure.lucky_trajectory(shell, seed)[0]
                    for seed in range(12)}
    assert len(trajectories) > 1
    assert ObserverShellClosure.gauge_consensus(shell, tuple(range(12))) == ()


def test_unique_state_is_invariant_across_lucky_gauges():
    shell = ObserverShellClosure().shells((ShellCandidate(7, 1.0, 1),))[0]
    assert ObserverShellClosure.gauge_consensus(shell, tuple(range(12))) == (7,)


def test_hard_repulsion_removes_candidate_before_shell_formation():
    shells = ObserverShellClosure().shells((
        ShellCandidate(1, 1.0, 1), ShellCandidate(2, 1.0, 1, True),
    ))
    assert shells[0].fact_ids == (1,)


def test_decoherence_creates_explicit_shells_without_changing_extrema():
    candidates = (
        ShellCandidate(1, 1.0, 1), ShellCandidate(2, 0.91, 1),
        ShellCandidate(3, 0.49, 1), ShellCandidate(4, 0.0, 1),
    )
    quantized = ObserverShellClosure.quantize(candidates, 3)
    shells = ObserverShellClosure().shells(quantized)
    assert shells[0].fact_ids == (1, 2)
    assert shells[-1].fact_ids == (4,)
