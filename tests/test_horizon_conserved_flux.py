# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.conserved_flux import (
    ConservedFluxSelector, CoreHaloFluxSelector,
)


def test_direct_light_and_relational_gravity_both_reach_the_boundary():
    result = ConservedFluxSelector(("direct", "direct", "bridge")).select((
        ("bridge", (10, 11, 12)), ("direct", (1, 2, 3, 4)),
    ), 6)
    assert result.fact_ids == (1, 2, 10, 3, 4, 11)
    assert dict(result.admissions) == {"bridge": 2, "direct": 4}


def test_shared_fact_keeps_one_identity_at_first_arrival():
    result = ConservedFluxSelector(("direct", "bridge")).select((
        ("bridge", (1, 3)), ("direct", (1, 2)),
    ), 3)
    assert result.fact_ids == (1, 3, 2)


def test_only_a_declared_hard_boundary_can_remove_direct_flux():
    result = ConservedFluxSelector(("direct", "bridge")).select((
        ("bridge", (3, 4)), ("direct", (1, 2)),
    ), 3, hard_exclusions=(1,))
    assert 1 not in result.fact_ids
    assert result.excluded == (1,)


def test_exhausted_mode_transfers_unused_flux_without_padding():
    result = ConservedFluxSelector(("direct", "bridge")).select((
        ("bridge", (9,)), ("direct", (1, 2, 3, 4)),
    ), 5)
    assert result.fact_ids == (1, 9, 2, 3, 4)


def test_core_halo_preserves_ballistic_head_then_interleaves_resonance():
    selector = CoreHaloFluxSelector("direct", 1, ("cavity", "direct"))
    result = selector.select((("cavity", (9, 2, 3)), ("direct", (1, 2, 4))), 4)
    assert result.fact_ids == (1, 9, 2, 3)
    assert len(set(result.fact_ids)) == 4


def test_hard_repulsion_applies_before_core_protection():
    selector = CoreHaloFluxSelector("direct", 1, ("cavity", "direct"))
    result = selector.select((("cavity", (1, 3)), ("direct", (1, 2))), 2,
                             hard_exclusions=(1,))
    assert result.fact_ids == (2, 3)
