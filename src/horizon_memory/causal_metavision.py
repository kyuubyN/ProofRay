# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ex-ante readiness and observer-relative opportunity windows.

This module makes the useful part of apparent "luck" measurable.  It never treats
luck as evidence: it only audits whether a previously prepared observer aperture was
at the coordinate, time and channel through which evidence could be received.
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations


@dataclass(frozen=True, order=True)
class CausalOpportunity:
    fact_id: int
    path_id: str
    channel: str
    arrives_at: float
    expires_at: float
    coordinate: float

    def __post_init__(self) -> None:
        if self.fact_id < 0 or not self.path_id or not self.channel \
                or self.arrives_at < 0 or self.expires_at < self.arrives_at:
            raise ValueError("invalid causal opportunity")


@dataclass(frozen=True, order=True)
class PreparedAperture:
    aperture_id: str
    prepared_at: float
    expires_at: float
    coordinate: float
    radius: float
    channels: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.aperture_id or self.prepared_at < 0 or self.expires_at <= self.prepared_at \
                or self.radius < 0 or not self.channels \
                or self.channels != tuple(sorted(set(self.channels))):
            raise ValueError("invalid prepared aperture")


@dataclass(frozen=True)
class ObservationDemand:
    observer_id: str
    issued_at: float
    coordinate: float
    radius: float
    required_channels: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.observer_id or self.issued_at < 0 or self.radius < 0 \
                or not self.required_channels \
                or self.required_channels != tuple(sorted(set(self.required_channels))):
            raise ValueError("invalid observation demand")


@dataclass(frozen=True)
class MetavisionResult:
    visible_fact_ids: tuple[int, ...]
    unavailable_fact_ids: tuple[int, ...]
    displaced_fact_ids: tuple[int, ...]
    unprepared_fact_ids: tuple[int, ...]
    used_aperture_ids: tuple[str, ...]
    opportunity_fact_ids: tuple[int, ...]
    readiness_alignment: float


class CausalMetavision:
    """Audit whether evidence and an ex-ante observer were ready to meet.

    There are three independent gates:

    * time: the propagation opportunity has arrived and has not expired;
    * place: opportunity and demand lie inside the requested observer section;
    * preparation: an aperture covering the place/channel existed *before* demand.

    Multiple apertures form the metaview.  Their union can improve coverage, but cannot
    increase a fact's evidential weight or turn an unavailable fact into a visible one.
    ``readiness_alignment`` therefore measures preparation coverage, not truth.
    """

    @staticmethod
    def observe(opportunities: tuple[CausalOpportunity, ...],
                apertures: tuple[PreparedAperture, ...],
                demand: ObservationDemand) -> MetavisionResult:
        if opportunities != tuple(sorted(set(opportunities))):
            raise ValueError("opportunities must be unique and canonically sorted")
        if apertures != tuple(sorted(set(apertures))):
            raise ValueError("apertures must be unique and canonically sorted")

        required = set(demand.required_channels)
        by_fact: dict[int, list[CausalOpportunity]] = {}
        for opportunity in opportunities:
            if opportunity.channel in required:
                by_fact.setdefault(opportunity.fact_id, []).append(opportunity)

        visible, unavailable, displaced, unprepared, used = set(), set(), set(), set(), set()
        potential = set()
        for fact_id, paths in sorted(by_fact.items()):
            timely = tuple(path for path in paths
                           if path.arrives_at <= demand.issued_at <= path.expires_at)
            if not timely:
                unavailable.add(fact_id)
                continue
            placed = tuple(path for path in timely
                           if abs(path.coordinate - demand.coordinate) <= demand.radius)
            if not placed:
                displaced.add(fact_id)
                continue
            potential.add(fact_id)
            witnesses = []
            for aperture in apertures:
                # Strict inequality is the anti-oracle boundary: preparation at or after
                # query issue is reaction, not readiness.
                if not aperture.prepared_at < demand.issued_at <= aperture.expires_at:
                    continue
                if not required.intersection(aperture.channels):
                    continue
                if abs(aperture.coordinate - demand.coordinate) > aperture.radius:
                    continue
                if any(path.channel in aperture.channels and
                       abs(path.coordinate - aperture.coordinate) <= aperture.radius
                       for path in placed):
                    witnesses.append(aperture.aperture_id)
            if witnesses:
                visible.add(fact_id)
                used.update(witnesses)
            else:
                unprepared.add(fact_id)

        alignment = len(visible) / len(potential) if potential else 0.0
        return MetavisionResult(
            tuple(sorted(visible)), tuple(sorted(unavailable)), tuple(sorted(displaced)),
            tuple(sorted(unprepared)), tuple(sorted(used)), tuple(sorted(potential)), alignment)


@dataclass(frozen=True, order=True)
class LandingSite:
    site_id: str
    probability: float
    impact: float
    setup_cost: int
    channels: tuple[str, ...]
    metadata_fact_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.site_id or not 0 <= self.probability <= 1 or self.impact < 0 \
                or self.setup_cost < 1 or not self.channels \
                or self.channels != tuple(sorted(set(self.channels))) \
                or not self.metadata_fact_ids \
                or self.metadata_fact_ids != tuple(sorted(set(self.metadata_fact_ids))):
            raise ValueError("invalid forecast landing site")


@dataclass(frozen=True)
class CompletionCapsule:
    capsule_id: str
    cost: int
    channels: tuple[str, ...]
    proof_fact_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if not self.capsule_id or self.cost < 1 or not self.channels \
                or self.channels != tuple(sorted(set(self.channels))) \
                or not self.proof_fact_ids \
                or self.proof_fact_ids != tuple(sorted(set(self.proof_fact_ids))):
            raise ValueError("invalid completion capsule")


@dataclass(frozen=True)
class StructuralMetavisionPlan:
    site_ids: tuple[str, ...]
    capsule_id: str
    proof_fact_ids: tuple[int, ...]
    used_cost: int
    covered_probability: float
    expected_impact: float
    metadata_fact_ids: tuple[int, ...]


class StructuralMetavision:
    """Prepare a small landing portfolio around one conserved completion invariant.

    Site probabilities are metadata-derived forecasts, not truth scores.  The capsule is
    paid once and its proof FactIds are conserved across every chosen branch.  Branches
    only supply reception/routing, so preparing three sites never triples evidence mass.
    """

    @staticmethod
    def prepare(sites: tuple[LandingSite, ...], capsule: CompletionCapsule, *,
                budget: int, max_sites: int = 3) -> StructuralMetavisionPlan:
        if sites != tuple(sorted(set(sites))):
            raise ValueError("landing sites must be unique and canonically sorted")
        if len({site.site_id for site in sites}) != len(sites):
            raise ValueError("landing site IDs must be unique")
        if budget < capsule.cost or max_sites < 1:
            raise ValueError("budget must fund the capsule and at least one allowed site")
        compatible = tuple(site for site in sites
                           if set(site.channels).intersection(capsule.channels))
        choices = []
        for width in range(1, min(max_sites, len(compatible)) + 1):
            for selected in combinations(compatible, width):
                cost = capsule.cost + sum(site.setup_cost for site in selected)
                if cost > budget:
                    continue
                probability = min(1.0, sum(site.probability for site in selected))
                expected_impact = sum(site.probability * site.impact for site in selected)
                # Impact is primary; coverage breaks equal-impact ties; lower cost and a
                # canonical ID order make the plan deterministic.
                site_ids = tuple(site.site_id for site in selected)
                choices.append((expected_impact, probability, -cost,
                                tuple(reversed(site_ids)), selected))
        if not choices:
            return StructuralMetavisionPlan((), capsule.capsule_id,
                                            capsule.proof_fact_ids, capsule.cost,
                                            0.0, 0.0, ())
        selected = max(choices)[-1]
        cost = capsule.cost + sum(site.setup_cost for site in selected)
        return StructuralMetavisionPlan(
            tuple(site.site_id for site in selected), capsule.capsule_id,
            capsule.proof_fact_ids, cost,
            min(1.0, sum(site.probability for site in selected)),
            sum(site.probability * site.impact for site in selected),
            tuple(sorted({fact_id for site in selected for fact_id in site.metadata_fact_ids})))


@dataclass(frozen=True, order=True)
class LandingSignal:
    site_id: str
    probability: float
    impact: float

    def __post_init__(self) -> None:
        if not self.site_id or not 0 <= self.probability <= 1 or self.impact < 0:
            raise ValueError("invalid landing signal")


@dataclass(frozen=True)
class StructuralFlux:
    fact_ids: tuple[int, ...]
    scores: tuple[float, ...]
    witnesses: tuple[tuple[int, tuple[str, ...]], ...]
    excluded: tuple[int, ...]


class StructuralFluxSelector:
    """Mix prepared receivers while conserving one identity per completion candidate.

    A candidate visible from several landing sites accumulates independent route support,
    but remains one FactId.  Site weights come from ex-ante metadata calibration.  This is
    a routing score only; it never becomes evidential confidence or answer correctness.
    """

    @staticmethod
    def select(rankings: tuple[tuple[str, tuple[int, ...]], ...],
               signals: tuple[LandingSignal, ...], *, rank_constant: float,
               limit: int, hard_exclusions: tuple[int, ...] = ()) -> StructuralFlux:
        if rankings != tuple(sorted(rankings)) or len({name for name, _ in rankings}) != len(rankings):
            raise ValueError("rankings must have unique, canonically sorted site IDs")
        if signals != tuple(sorted(set(signals))) or len({item.site_id for item in signals}) != len(signals):
            raise ValueError("signals must be unique and canonically sorted")
        if rank_constant <= 0 or limit < 1:
            raise ValueError("positive rank constant and limit are required")
        by_signal = {item.site_id: item for item in signals}
        if set(by_signal) != {name for name, _ in rankings}:
            raise ValueError("every prepared receiver requires exactly one signal")
        if any(len(values) != len(set(values)) for _, values in rankings):
            raise ValueError("receiver rankings must be FactId-deduplicated")

        excluded = set(hard_exclusions)
        scores: dict[int, float] = {}
        witnesses: dict[int, set[str]] = {}
        for site_id, facts in rankings:
            signal = by_signal[site_id]
            weight = signal.probability * signal.impact
            for rank, fact_id in enumerate(facts, 1):
                if fact_id in excluded:
                    continue
                scores[fact_id] = scores.get(fact_id, 0.0) + weight / (rank_constant + rank)
                witnesses.setdefault(fact_id, set()).add(site_id)
        ordered = tuple(sorted(scores, key=lambda fact_id: (-scores[fact_id], fact_id))[:limit])
        return StructuralFlux(
            ordered, tuple(scores[fact_id] for fact_id in ordered),
            tuple((fact_id, tuple(sorted(witnesses[fact_id]))) for fact_id in ordered),
            tuple(sorted(excluded)))
