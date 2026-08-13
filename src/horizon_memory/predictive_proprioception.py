# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Always-on causal proprioception: prepare trajectories before an impact arrives."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class PeripheralObservation:
    fact_id: int
    trajectory_id: str
    clock: float
    coordinate: float
    body_load: float
    channels: tuple[str, ...]
    intentions: tuple[str, ...]
    impact: float

    def __post_init__(self) -> None:
        if self.fact_id < 0 or not self.trajectory_id or self.clock < 0 \
                or not 0 <= self.body_load <= 1 or not self.channels \
                or self.channels != tuple(sorted(set(self.channels))) \
                or self.intentions != tuple(sorted(set(self.intentions))) or self.impact < 0:
            raise ValueError("invalid peripheral observation")


@dataclass(frozen=True, order=True)
class PreparedTrajectory:
    trajectory_id: str
    prepared_at: float
    predicted_at: float
    time_radius: float
    coordinate: float
    space_radius: float
    body_headroom: float
    channels: tuple[str, ...]
    intentions: tuple[str, ...]
    probability: float
    impact: float
    support_fact_ids: tuple[int, ...]


@dataclass(frozen=True)
class ProprioceptiveSnapshot:
    clock: float
    trajectories: tuple[PreparedTrajectory, ...]
    observed_fact_ids: tuple[int, ...]
    memory_observations: int


@dataclass(frozen=True)
class ImpactPulse:
    clock: float
    coordinate: float
    channels: tuple[str, ...]
    intentions: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.clock < 0 or not self.channels \
                or self.channels != tuple(sorted(set(self.channels))) \
                or self.intentions != tuple(sorted(set(self.intentions))):
            raise ValueError("invalid impact pulse")


@dataclass(frozen=True)
class AnticipatoryClosure:
    state: str
    trajectory_id: str | None
    score: float
    support_fact_ids: tuple[int, ...]
    compared_trajectories: int
    reason: str


class PredictiveProprioceptiveField:
    """Incrementally project rhythms; impact reads only a bounded prepared frontier.

    A trajectory is learned from at least two causally ordered observations.  Its next
    time and place are extrapolated during ingestion.  At impact, body, space, time and
    intention align multiplicatively: a strong partial match cannot compensate for a
    missing declared dimension.  This is anticipation, not truth; closure exposes its
    supporting FactIds and remains distinct from a verified answer.
    """

    def __init__(self, *, max_futures: int = 3, minimum_score: float = 0.20):
        if max_futures < 1 or not 0 < minimum_score <= 1:
            raise ValueError("invalid proprioceptive frontier configuration")
        self.max_futures = max_futures
        self.minimum_score = minimum_score
        self._history: dict[str, list[PeripheralObservation]] = {}
        self._seen_fact_ids: set[int] = set()
        self._snapshot = ProprioceptiveSnapshot(0.0, (), (), 0)

    @staticmethod
    def _trajectory(rows: tuple[PeripheralObservation, ...]) -> PreparedTrajectory | None:
        if len(rows) < 2:
            return None
        intervals = tuple(right.clock - left.clock for left, right in zip(rows, rows[1:]))
        if any(interval <= 0 for interval in intervals):
            raise ValueError("trajectory clocks must be strictly increasing")
        velocities = tuple((right.coordinate - left.coordinate) / interval
                           for left, right, interval in zip(rows, rows[1:], intervals))
        period = statistics.median(intervals)
        velocity = statistics.median(velocities)
        last = rows[-1]
        predicted_at = last.clock + period
        predicted_coordinate = last.coordinate + velocity * period
        interval_error = statistics.mean(abs(value - period) for value in intervals) / period
        velocity_scale = max(1.0, abs(velocity))
        velocity_error = statistics.mean(abs(value - velocity) for value in velocities) / velocity_scale
        rhythmic_coherence = math.exp(-(interval_error + velocity_error))
        persistence = min(1.0, len(rows) / 4)
        probability = rhythmic_coherence * persistence
        common_channels = set(rows[0].channels)
        common_intentions = set(rows[0].intentions)
        for row in rows[1:]:
            common_channels.intersection_update(row.channels)
            common_intentions.intersection_update(row.intentions)
        if not common_channels:
            return None
        # Empty intention remains unknown and is allowed; conflicting declared intention
        # erases the trajectory instead of inventing a stable goal.
        if any(row.intentions for row in rows) and not common_intentions:
            return None
        return PreparedTrajectory(
            last.trajectory_id, last.clock, predicted_at,
            max(1.0, period * (0.25 + interval_error)), predicted_coordinate,
            max(1.0, abs(velocity) * period * (0.25 + velocity_error)),
            1.0 - statistics.mean(row.body_load for row in rows),
            tuple(sorted(common_channels)), tuple(sorted(common_intentions)),
            min(1.0, probability), statistics.mean(row.impact for row in rows),
            tuple(row.fact_id for row in rows))

    def ingest(self, observation: PeripheralObservation) -> ProprioceptiveSnapshot:
        if observation.fact_id in self._seen_fact_ids:
            raise ValueError("peripheral FactId replay is not a new observation")
        if observation.clock < self._snapshot.clock:
            raise ValueError("peripheral scan clock cannot move backwards")
        rows = self._history.setdefault(observation.trajectory_id, [])
        if rows and observation.clock <= rows[-1].clock:
            raise ValueError("trajectory observations must be strictly ordered")
        rows.append(observation)
        self._seen_fact_ids.add(observation.fact_id)
        futures = tuple(filter(None, (self._trajectory(tuple(items))
                                      for items in self._history.values())))
        futures = tuple(sorted(futures, key=lambda item: (
            -(item.probability * item.impact * item.body_headroom),
            item.predicted_at, item.trajectory_id))[:self.max_futures])
        self._snapshot = ProprioceptiveSnapshot(
            observation.clock, futures, tuple(sorted(self._seen_fact_ids)),
            len(self._seen_fact_ids))
        return self._snapshot

    @property
    def snapshot(self) -> ProprioceptiveSnapshot:
        return self._snapshot

    def impact(self, pulse: ImpactPulse) -> AnticipatoryClosure:
        ranked = []
        pulse_channels, pulse_intentions = set(pulse.channels), set(pulse.intentions)
        for future in self._snapshot.trajectories:
            if not future.prepared_at < pulse.clock:
                continue
            channel = (len(pulse_channels.intersection(future.channels)) /
                       len(pulse_channels.union(future.channels)))
            if not channel:
                continue
            if pulse_intentions and future.intentions:
                intention = len(pulse_intentions.intersection(future.intentions)) / \
                    len(pulse_intentions.union(future.intentions))
            else:
                intention = 1.0  # absence is unknown, never a declared contradiction
            temporal = math.exp(-((pulse.clock - future.predicted_at) /
                                  future.time_radius) ** 2)
            spatial = math.exp(-((pulse.coordinate - future.coordinate) /
                                 future.space_radius) ** 2)
            score = (future.probability * future.body_headroom * channel * intention *
                     temporal * spatial)
            ranked.append((score, future))
        if not ranked:
            return AnticipatoryClosure("unprepared", None, 0.0, (),
                                       len(self._snapshot.trajectories),
                                       "no ex-ante trajectory covers all declared layers")
        score, future = max(ranked, key=lambda item: (item[0], item[1].trajectory_id))
        if score < self.minimum_score:
            return AnticipatoryClosure("peripheral", future.trajectory_id, score,
                                       future.support_fact_ids, len(self._snapshot.trajectories),
                                       "weak anticipation remains peripheral")
        return AnticipatoryClosure("anticipated", future.trajectory_id, score,
                                   future.support_fact_ids, len(self._snapshot.trajectories),
                                   "impact closed a previously prepared trajectory")
