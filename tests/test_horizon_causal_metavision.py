# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.causal_metavision import (
    CausalMetavision, CausalOpportunity, CompletionCapsule, LandingSite,
    LandingSignal, ObservationDemand, PreparedAperture, StructuralFluxSelector,
    StructuralMetavision,
)


def _demand(**changes):
    values = dict(observer_id="o", issued_at=10.0, coordinate=5.0, radius=1.0,
                  required_channels=("causal",))
    values.update(changes)
    return ObservationDemand(**values)


def _opportunity(fact_id=1, **changes):
    values = dict(fact_id=fact_id, path_id=f"p{fact_id}", channel="causal",
                  arrives_at=8.0, expires_at=12.0, coordinate=5.0)
    values.update(changes)
    return CausalOpportunity(**values)


def _aperture(aperture_id="a", **changes):
    values = dict(aperture_id=aperture_id, prepared_at=7.0, expires_at=12.0,
                  coordinate=5.0, radius=1.0, channels=("causal",))
    values.update(changes)
    return PreparedAperture(**values)


def test_right_place_right_time_and_prior_preparation_make_fact_visible():
    result = CausalMetavision.observe((_opportunity(),), (_aperture(),), _demand())
    assert result.visible_fact_ids == (1,)
    assert result.readiness_alignment == 1.0


def test_preparation_at_query_time_is_reaction_and_cannot_claim_luck():
    result = CausalMetavision.observe(
        (_opportunity(),), (_aperture(prepared_at=10.0),), _demand())
    assert result.visible_fact_ids == ()
    assert result.unprepared_fact_ids == (1,)


def test_retarded_or_expired_information_is_temporally_unavailable():
    result = CausalMetavision.observe(tuple(sorted((
        _opportunity(1, arrives_at=11.0, expires_at=20.0),
        _opportunity(2, arrives_at=1.0, expires_at=9.0),
    ))), (_aperture(),), _demand())
    assert result.unavailable_fact_ids == (1, 2)


def test_wrong_place_is_distinct_from_missing_preparation():
    result = CausalMetavision.observe(
        (_opportunity(coordinate=20.0),), (_aperture(),), _demand())
    assert result.displaced_fact_ids == (1,)
    assert result.unprepared_fact_ids == ()


def test_metavision_is_union_of_prepared_views_without_duplicate_mass():
    opportunities = tuple(sorted((
        _opportunity(1, coordinate=4.5), _opportunity(1, path_id="p1b", coordinate=5.5),
        _opportunity(2, coordinate=5.5),
    )))
    apertures = tuple(sorted((
        _aperture("left", coordinate=4.5, radius=0.6),
        _aperture("right", coordinate=5.5, radius=0.6),
    )))
    result = CausalMetavision.observe(opportunities, apertures, _demand())
    assert result.visible_fact_ids == (1, 2)
    assert result.used_aperture_ids == ("left", "right")


def test_order_is_gauge_and_does_not_change_the_metavision_result():
    opportunities = (_opportunity(2), _opportunity(1))
    apertures = (_aperture("b"), _aperture("a"))
    canonical = CausalMetavision.observe(
        tuple(sorted(opportunities)), tuple(sorted(apertures)), _demand())
    reordered = CausalMetavision.observe(
        tuple(sorted(reversed(opportunities))), tuple(sorted(reversed(apertures))), _demand())
    assert reordered == canonical


def _site(site_id, probability, impact=1.0, cost=2, channels=("causal",), fact_id=10):
    return LandingSite(site_id, probability, impact, cost, channels, (fact_id,))


def _capsule():
    return CompletionCapsule("finish", 3, ("causal",), (100, 101))


def test_structural_metavision_prepares_three_landings_around_one_capsule():
    sites = tuple(sorted((_site("a", .45), _site("b", .30), _site("c", .20))))
    plan = StructuralMetavision.prepare(sites, _capsule(), budget=9, max_sites=3)
    assert plan.site_ids == ("a", "b", "c")
    assert plan.covered_probability == .95
    assert plan.proof_fact_ids == (100, 101)  # conserved once, not once per branch
    assert plan.used_cost == 9


def test_impact_can_prepare_a_less_probable_but_more_consequential_landing():
    sites = tuple(sorted((
        _site("likely", .60, impact=1),
        _site("critical", .25, impact=5),
        _site("minor", .15, impact=1),
    )))
    plan = StructuralMetavision.prepare(sites, _capsule(), budget=5, max_sites=1)
    assert plan.site_ids == ("critical",)


def test_incompatible_branch_cannot_activate_the_common_completion_capsule():
    sites = tuple(sorted((
        _site("causal", .4), _site("visual", .6, channels=("visual",)),
    )))
    plan = StructuralMetavision.prepare(sites, _capsule(), budget=9)
    assert plan.site_ids == ("causal",)


def test_metadata_support_is_preserved_as_provenance_not_as_proof_mass():
    sites = tuple(sorted((
        _site("a", .5, fact_id=7), _site("b", .4, fact_id=8),
    )))
    plan = StructuralMetavision.prepare(sites, _capsule(), budget=7)
    assert plan.metadata_fact_ids == (7, 8)
    assert plan.proof_fact_ids == (100, 101)


def test_structural_flux_reinforces_an_intersection_but_keeps_one_fact_identity():
    rankings = tuple(sorted((
        ("causal", (8, 7)), ("lexical", (9, 7)), ("relational", (10, 7)),
    )))
    signals = tuple(sorted((
        LandingSignal("causal", .8, 1), LandingSignal("lexical", .8, 1),
        LandingSignal("relational", .8, 1),
    )))
    result = StructuralFluxSelector.select(
        rankings, signals, rank_constant=1, limit=4)
    assert result.fact_ids[0] == 7
    assert result.fact_ids.count(7) == 1
    assert dict(result.witnesses)[7] == ("causal", "lexical", "relational")


def test_structural_flux_uses_probability_times_impact_not_probability_alone():
    rankings = (("critical", (2,)), ("likely", (1,)))
    signals = tuple(sorted((
        LandingSignal("critical", .3, 4), LandingSignal("likely", .8, 1),
    )))
    result = StructuralFluxSelector.select(
        rankings, signals, rank_constant=1, limit=2)
    assert result.fact_ids == (2, 1)


def test_structural_flux_applies_hard_repulsion_before_route_mixing():
    rankings = (("a", (1, 2)), ("b", (1, 3)))
    signals = (LandingSignal("a", .5, 1), LandingSignal("b", .5, 1))
    result = StructuralFluxSelector.select(
        rankings, signals, rank_constant=10, limit=3, hard_exclusions=(1,))
    assert 1 not in result.fact_ids
    assert result.excluded == (1,)
