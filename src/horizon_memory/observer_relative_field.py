# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Observer-relative retarded causal projection into the unified Horizon field."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .unified_causal_field import ProofHyperedge


def _charge_map(charges: tuple[str, ...]) -> dict[str, str]:
    result = {}
    for charge in charges:
        if ":" not in charge:
            raise ValueError("charges must use key:value form")
        key, value = charge.split(":", 1)
        if not key or not value or key in result:
            raise ValueError("charges require unique non-empty keys")
        result[key] = value
    return result


@dataclass(frozen=True, order=True)
class MassiveCausalEvent:
    scope: str
    hypothesis: str
    orbit_id: str
    fact_id: int
    event_time: float
    recorded_at: float
    mass: float
    charges: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.scope or not self.hypothesis or not self.orbit_id or self.fact_id < 0:
            raise ValueError("causal event requires scope, hypothesis, orbit and FactId")
        if self.event_time < 0 or self.recorded_at < self.event_time or not 0 < self.mass <= 1:
            raise ValueError("invalid event clocks or informational mass")
        if self.charges != tuple(sorted(set(self.charges))):
            raise ValueError("event charges must be unique and sorted")
        _charge_map(self.charges)


@dataclass(frozen=True, order=True)
class PropagationPath:
    fact_id: int
    path_id: str
    length: float
    delay: float
    transport_cost: float
    phase: float = 0.0
    orientation: int = 1
    hard_boundary: bool = False

    def __post_init__(self) -> None:
        if self.fact_id < 0 or not self.path_id or self.length < 0 or self.delay < 0 \
                or self.transport_cost < 0 or self.orientation not in (-1, 1):
            raise ValueError("invalid causal propagation path")


@dataclass(frozen=True)
class ObserverSection:
    observer_id: str
    scope: str
    clock: float
    target_time: float
    ideal_path_length: float
    temporal_scale: float
    path_scale: float
    phase: float
    required_charges: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.observer_id or not self.scope or self.clock < 0 or self.target_time < 0 \
                or self.target_time > self.clock or self.ideal_path_length < 0 \
                or self.temporal_scale <= 0 or self.path_scale <= 0:
            raise ValueError("invalid observer section")
        if self.required_charges != tuple(sorted(set(self.required_charges))):
            raise ValueError("observer charges must be unique and sorted")
        _charge_map(self.required_charges)


@dataclass(frozen=True)
class RetardedProjection:
    edges: tuple[ProofHyperedge, ...]
    visible_events: int
    causally_hidden_events: int
    projected_orbits: int
    cancelled_orbits: int


class ObserverRelativeCausalField:
    """Evaluate what one observer can see, at the time and distance the query selects.

    `mass` is intrinsic authority/information weight, not occurrence frequency. Repeated
    reports sharing an orbit are coarse-grained into one amplitude. Propagation delay
    creates a causal cone. Temporal and graph distance use kernels centered on the
    observer's requested coordinates, so recency is only the special case target=now and
    ideal_path_length=0. Charge conflict and opposite phase repel through the same signed
    amplitude used for constructive evidence.
    """

    def __init__(self, events: tuple[MassiveCausalEvent, ...]):
        if events != tuple(sorted(set(events))):
            raise ValueError("massive causal events must be unique and canonically sorted")
        if len({event.fact_id for event in events}) != len(events):
            raise ValueError("FactIds must be unique")
        self._events = events
        self._by_id = {event.fact_id: event for event in events}

    @staticmethod
    def _charge_coupling(event: MassiveCausalEvent, observer: ObserverSection) \
            -> tuple[float, bool, tuple[str, ...]]:
        observed = _charge_map(event.charges)
        required = _charge_map(observer.required_charges)
        matched, conflicts = [], []
        for key, wanted in required.items():
            actual = observed.get(key)
            if actual is None:
                continue  # absence without completeness is unknown
            if actual == wanted:
                matched.append(f"{key}:{wanted}")
            else:
                conflicts.append(f"{key}:{actual}!={wanted}")
        coverage = len(matched) / max(1, len(required)) if required else 1.0
        coupling = 0.5 + 0.5 * coverage
        return (-coupling if conflicts else coupling), bool(conflicts), tuple(sorted(matched))

    def project(self, observer: ObserverSection,
                paths: tuple[PropagationPath, ...]) -> RetardedProjection:
        if paths != tuple(sorted(set(paths))):
            raise ValueError("propagation paths must be unique and canonically sorted")
        by_orbit: dict[tuple[str, str], list[tuple[float, MassiveCausalEvent,
                                                       PropagationPath, bool, tuple[str, ...]]]] = {}
        visible_ids, hidden_ids = set(), set()
        for path in paths:
            event = self._by_id.get(path.fact_id)
            if event is None or event.scope != observer.scope:
                continue
            # The observer sees the source only after its record and propagation delay arrive.
            if event.recorded_at + path.delay > observer.clock:
                hidden_ids.add(event.fact_id)
                continue
            visible_ids.add(event.fact_id)
            time_delta = (event.event_time - observer.target_time) / observer.temporal_scale
            path_delta = (path.length - observer.ideal_path_length) / observer.path_scale
            retarded_kernel = math.exp(-(time_delta * time_delta + path_delta * path_delta))
            geodesic = 1.0 / (1.0 + path.transport_cost)
            phase = math.cos(path.phase - observer.phase)
            charge, conflict, matched = self._charge_coupling(event, observer)
            amplitude = event.mass * retarded_kernel * geodesic * phase * path.orientation * charge
            by_orbit.setdefault((event.hypothesis, event.orbit_id), []).append(
                (amplitude, event, path, conflict, matched))

        edges, cancelled = [], 0
        for (hypothesis, orbit_id), projections in sorted(by_orbit.items()):
            # Many routes/reports cannot manufacture mass. Averaging makes inconsistent
            # transports cancel, while identical duplicates keep the same bounded weight.
            by_path = {}
            for item in projections:
                path_id = item[2].path_id
                previous = by_path.get(path_id)
                if previous is None or (abs(item[0]), item[1].mass, -item[1].fact_id) > \
                        (abs(previous[0]), previous[1].mass, -previous[1].fact_id):
                    by_path[path_id] = item
            independent_projections = tuple(by_path.values())
            amplitude = sum(item[0] for item in independent_projections) / \
                len(independent_projections)
            if abs(amplitude) < 1e-12:
                cancelled += 1
                continue
            representative = max((item[1] for item in independent_projections),
                                 key=lambda event: (event.mass, -event.fact_id))
            hard_negative = amplitude < 0 and any(
                item[2].hard_boundary or item[3] for item in independent_projections)
            matched = tuple(sorted({charge for item in independent_projections for charge in item[4]}))
            channels = tuple(sorted(set(matched) | {
                f"observer:{observer.observer_id}", f"orbit:{orbit_id}",
            }))
            edges.append(ProofHyperedge(
                scope=observer.scope, canonical=hypothesis, channels=channels,
                evidence_fact_ids=(representative.fact_id,),
                observed_at=int(observer.clock), amplitude=amplitude,
                origin="observer_relative_retarded_field", hard_negative=hard_negative,
            ))
        return RetardedProjection(tuple(edges), len(visible_ids), len(hidden_ids),
                                  len(edges), cancelled)
