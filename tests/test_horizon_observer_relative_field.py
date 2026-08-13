# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import math

from horizon_memory.observer_relative_field import (
    MassiveCausalEvent, ObserverRelativeCausalField, ObserverSection, PropagationPath,
)
from horizon_memory.unified_causal_field import HorizonUnifiedCausalField


def _event(hypothesis, orbit, fid, event_time, recorded_at, mass=1.0,
           charges=("polarity:positive",)):
    return MassiveCausalEvent("world", hypothesis, orbit, fid, event_time,
                              recorded_at, mass, tuple(sorted(charges)))


def _observer(clock=300, target=100, ideal=1, phase=0,
              charges=("polarity:positive",)):
    return ObserverSection("query", "world", clock, target, ideal, 10, 1, phase,
                           tuple(sorted(charges)))


def _path(fid, path_id, length=1, delay=0, phase=0, hard=False):
    return PropagationPath(fid, path_id, length, delay, 0, phase, 1, hard)


def _resolve(events, observer, paths):
    projection = ObserverRelativeCausalField(tuple(sorted(events))).project(
        observer, tuple(sorted(paths)))
    field = HorizonUnifiedCausalField(min_margin=0.05)
    field.begin_breath("world", 1)
    for edge in projection.edges:
        field.inhale(edge)
    field.exhale("world", 999)
    return projection, field.resolve("world")


def test_retarded_cone_hides_information_that_has_not_arrived():
    events = (_event("old", "o1", 1, 100, 100), _event("old", "o2", 2, 100, 100))
    projection, result = _resolve(events, _observer(clock=105),
                                  (_path(1, "p1", delay=3), _path(2, "p2", delay=10)))
    assert projection.visible_events == 1
    assert projection.causally_hidden_events == 1
    assert result.state == "abstain"  # one arrived orbit cannot manufacture certainty


def test_distant_target_time_beats_recency_when_observer_asks_for_the_past():
    events = (
        _event("past_state", "past-a", 1, 100, 100),
        _event("past_state", "past-b", 2, 101, 101),
        _event("current_state", "now-a", 3, 295, 295),
        _event("current_state", "now-b", 4, 296, 296),
    )
    _, result = _resolve(events, _observer(target=100), tuple(
        _path(fid, f"p{fid}") for fid in range(1, 5)))
    assert result.state == "resolved"
    assert result.canonical == "past_state"


def test_repeated_routes_and_reports_in_one_orbit_do_not_create_mass():
    events = (
        _event("copy", "same", 1, 100, 100, 0.4),
        _event("copy", "same", 2, 100, 100, 0.4),
        _event("authority", "a", 3, 100, 100, 0.9),
        _event("authority", "b", 4, 100, 100, 0.9),
    )
    paths = (_path(1, "route-a"), _path(1, "route-b"), _path(2, "route-c"),
             _path(3, "route-d"), _path(4, "route-e"))
    projection, result = _resolve(events, _observer(), paths)
    assert projection.projected_orbits == 3
    assert result.state == "resolved"
    assert result.canonical == "authority"


def test_declared_charge_conflict_repels_through_the_same_field():
    events = (
        _event("claim", "positive-a", 1, 100, 100),
        _event("claim", "positive-b", 2, 100, 100),
        _event("claim", "negative", 3, 100, 100, charges=("polarity:negative",)),
    )
    _, result = _resolve(events, _observer(),
                         (_path(1, "p1"), _path(2, "p2"), _path(3, "p3", hard=True)))
    assert result.state == "abstain"
    assert 3 in result.evidence_fact_ids


def test_opposite_phase_is_destructive_interference_not_an_extra_vote():
    event = _event("ambiguous", "one", 1, 100, 100)
    projection = ObserverRelativeCausalField((event,)).project(
        _observer(), tuple(sorted((_path(1, "a", phase=0),
                                   _path(1, "b", phase=math.pi)))))
    assert projection.edges == ()
    assert projection.cancelled_orbits == 1


def test_global_time_coordinate_shift_preserves_observer_section():
    original = (_event("state", "a", 1, 100, 100), _event("state", "b", 2, 101, 101))
    shifted = (_event("state", "a", 1, 150, 150), _event("state", "b", 2, 151, 151))
    paths = (_path(1, "p1"), _path(2, "p2"))
    left = ObserverRelativeCausalField(tuple(sorted(original))).project(
        _observer(clock=300, target=100), tuple(sorted(paths)))
    right = ObserverRelativeCausalField(tuple(sorted(shifted))).project(
        _observer(clock=350, target=150), tuple(sorted(paths)))
    assert [round(edge.amplitude, 12) for edge in left.edges] == [
        round(edge.amplitude, 12) for edge in right.edges]
