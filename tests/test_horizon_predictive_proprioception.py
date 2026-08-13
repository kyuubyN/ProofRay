# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.predictive_proprioception import (
    ImpactPulse, PeripheralObservation, PredictiveProprioceptiveField,
)


def _observation(fact_id, clock, coordinate, trajectory="walk", *, load=.2,
                 channels=("motion",), intentions=("arrive",), impact=1.0):
    return PeripheralObservation(fact_id, trajectory, clock, coordinate, load,
                                 tuple(sorted(channels)), tuple(sorted(intentions)), impact)


def _trained(**configuration):
    field = PredictiveProprioceptiveField(**configuration)
    field.ingest(_observation(1, 1, 1))
    field.ingest(_observation(2, 2, 2))
    field.ingest(_observation(3, 3, 3))
    return field


def test_rhythm_prepares_the_next_time_and_place_before_impact():
    field = _trained()
    future = field.snapshot.trajectories[0]
    assert future.prepared_at == 3
    assert future.predicted_at == 4
    assert future.coordinate == 4
    result = field.impact(ImpactPulse(4, 4, ("motion",), ("arrive",)))
    assert result.state == "anticipated"
    assert result.support_fact_ids == (1, 2, 3)


def test_query_time_preparation_is_rejected_by_strict_causal_boundary():
    field = _trained()
    result = field.impact(ImpactPulse(3, 4, ("motion",), ("arrive",)))
    assert result.state != "anticipated"


def test_wrong_space_time_or_intention_cannot_be_compensated_additively():
    field = _trained()
    assert field.impact(ImpactPulse(4, 40, ("motion",), ("arrive",))).state == "peripheral"
    assert field.impact(ImpactPulse(40, 4, ("motion",), ("arrive",))).state == "peripheral"
    assert field.impact(ImpactPulse(4, 4, ("motion",), ("depart",))).state == "peripheral"


def test_missing_intention_is_unknown_but_declared_conflict_is_not():
    field = PredictiveProprioceptiveField()
    field.ingest(_observation(1, 1, 1, intentions=()))
    field.ingest(_observation(2, 2, 2, intentions=()))
    assert field.impact(ImpactPulse(3, 3, ("motion",), ("arrive",))).state == "anticipated"


def test_peripheral_frontier_is_bounded_even_when_history_grows():
    field = PredictiveProprioceptiveField(max_futures=3)
    fact_id = 0
    for offset, trajectory in enumerate(("a", "b", "c", "d", "e")):
        for clock in (2 * offset + 1, 2 * offset + 2):
            fact_id += 1
            field.ingest(_observation(fact_id, clock, clock, trajectory=trajectory,
                                      impact=ord(trajectory) - 96))
    assert len(field.snapshot.trajectories) == 3
    assert field.snapshot.memory_observations == 10


def test_impact_cost_depends_on_prepared_frontier_not_history_size():
    field = PredictiveProprioceptiveField(max_futures=3)
    for fact_id in range(1, 101):
        field.ingest(_observation(fact_id, fact_id, fact_id))
    result = field.impact(ImpactPulse(101, 101, ("motion",), ("arrive",)))
    assert result.compared_trajectories == 1
    assert field.snapshot.memory_observations == 100


def test_duplicate_fact_and_backwards_clock_fail_closed():
    field = PredictiveProprioceptiveField()
    field.ingest(_observation(1, 2, 2))
    try:
        field.ingest(_observation(1, 3, 3))
        assert False
    except ValueError:
        pass
    try:
        field.ingest(_observation(2, 1, 1))
        assert False
    except ValueError:
        pass
