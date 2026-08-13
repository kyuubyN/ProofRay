# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compile raw text into observer-relative events and propagation paths without a model."""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass

from .observer_relative_field import (
    MassiveCausalEvent, ObserverRelativeCausalField, ObserverSection, PropagationPath,
    RetardedProjection,
)
from .raw_causal_channels import (
    RawCausalChannels, RawCausalDocument, RawCausalSyndromeIndex, observe_raw_text,
)


_LATEST = re.compile(r"\b(?:latest|most recent|recently|currently|now|current)\b", re.I)
_EARLIEST = re.compile(r"\b(?:first|earliest|initially|originally)\b", re.I)
_MULTIHOP = re.compile(r"\b(?:why|how|because|before|after|between|lead to|result)\b", re.I)


@dataclass(frozen=True)
class PerspectiveCompilation:
    observer: ObserverSection
    projection: RetardedProjection
    orbit_members: tuple[tuple[str, tuple[int, ...]], ...]
    expected_channels: tuple[str, ...]


@dataclass(frozen=True, order=True)
class CertifiedPropagation:
    fact_id: int
    channel: str
    coherence: float
    length: float
    delay: float = 0.0

    def __post_init__(self) -> None:
        if self.fact_id < 0 or not self.channel or not 0 < self.coherence <= 1 \
                or self.length < 0 or self.delay < 0 or self.channel == "lexical":
            raise ValueError("invalid certified propagation")


class ObserverPerspectiveCompiler:
    """Make the query a physical observer of a proof field, never a gold-conditioned ranker."""

    def __init__(self, documents: tuple[RawCausalDocument, ...], scope: str, *,
                 authority_mass: float = 1.0):
        if not scope:
            raise ValueError("observer compiler scope is required")
        if not 0 < authority_mass <= 1:
            raise ValueError("authority mass must be in (0,1]")
        self.scope = scope
        self.authority_mass = authority_mass
        self.documents = documents
        self.index = RawCausalSyndromeIndex(documents)
        self._channels = self.index.channels
        self._minimum_time = min(item.session_index for item in documents)
        self._maximum_time = max(item.session_index for item in documents)
        self._span = max(1.0, self._maximum_time - self._minimum_time)
        self._events, members = self._compile_events()
        self.orbit_members = tuple(sorted((orbit, tuple(sorted(fact_ids)))
                                          for orbit, fact_ids in members.items()))

    @staticmethod
    def _orbit(value: RawCausalChannels) -> str:
        payload = "\x1f".join((" ".join(value.lexical), " ".join(value.numbers),
                                " ".join(value.temporal), value.polarity, value.modality))
        return hashlib.sha256(b"horizon-observer-orbit-v1\x00" + payload.encode()).hexdigest()[:24]

    @staticmethod
    def _charges(value: RawCausalChannels) -> tuple[str, ...]:
        charges = {f"polarity:{value.polarity}", f"modality:{value.modality}"}
        if value.numbers:
            charges.add("number:" + ",".join(value.numbers))
        return tuple(sorted(charges))

    def _compile_events(self):
        events, members = [], {}
        for document in self.documents:
            value = self._channels[document.fact_id]
            orbit = self._orbit(value)
            members.setdefault(orbit, set()).add(document.fact_id)
            events.append(MassiveCausalEvent(
                self.scope, f"orbit:{orbit}", orbit, document.fact_id,
                float(document.session_index), float(document.session_index), self.authority_mass,
                self._charges(value)))
        return tuple(sorted(events)), members

    def _observer(self, query_text: str, query: RawCausalChannels,
                  has_certified_paths: bool) -> ObserverSection:
        explicit_times = [float(document.session_index) for document in self.documents
                          if set(query.temporal).intersection(
                              self._channels[document.fact_id].temporal)]
        if explicit_times:
            target = sum(explicit_times) / len(explicit_times)
            temporal_scale = max(1.0, self._span / 8)
        elif _LATEST.search(query_text):
            target, temporal_scale = float(self._maximum_time), max(1.0, self._span / 8)
        elif _EARLIEST.search(query_text):
            target, temporal_scale = float(self._minimum_time), max(1.0, self._span / 8)
        else:
            target = (self._minimum_time + self._maximum_time) / 2
            # Generic questions do not silently mean "recent".  A practically flat
            # kernel is explicit here; targeted time modes above remain narrow.
            temporal_scale = 1e12
        required = set()
        if query.polarity == "negative":
            required.add("polarity:negative")
        if query.numbers:
            required.add("number:" + ",".join(query.numbers))
        ideal = 2.0 if _MULTIHOP.search(query_text) else (1.5 if has_certified_paths else 1.0)
        # All persisted events have time to reach the after-conversation observer.
        clock = float(self._maximum_time + 8)
        return ObserverSection(
            hashlib.sha256(query_text.encode()).hexdigest()[:16], self.scope, clock,
            target, ideal, temporal_scale, 1.0, 0.0, tuple(sorted(required)))

    def compile(self, query_text: str,
                certified_paths: tuple[CertifiedPropagation, ...] = ()) -> PerspectiveCompilation:
        if certified_paths != tuple(sorted(set(certified_paths))):
            raise ValueError("certified paths must be unique and canonically sorted")
        known_ids = {document.fact_id for document in self.documents}
        if any(path.fact_id not in known_ids for path in certified_paths):
            raise ValueError("certified path references an unknown FactId")
        query = observe_raw_text(query_text, question=True)
        observer = self._observer(query_text, query, bool(certified_paths))
        expected = tuple(sorted({"lexical"} | {path.channel for path in certified_paths}))
        components = {item.fact_id: item for item in self.index.components(query_text)}
        orbit_for_fact = {event.fact_id: event.orbit_id for event in self._events}
        certified_by_fact = {}
        for path in certified_paths:
            certified_by_fact.setdefault(path.fact_id, {})[path.channel] = path
        paths = []
        for document in self.documents:
            row = components[document.fact_id]
            orbit = orbit_for_fact[document.fact_id]
            number_conflict = bool(query.numbers and self._channels[document.fact_id].numbers and
                                   not set(query.numbers).intersection(
                                       self._channels[document.fact_id].numbers))
            for channel in expected:
                certificate = certified_by_fact.get(document.fact_id, {}).get(channel)
                coherence = (min(1.0, max(0.0, row.lexical)) if channel == "lexical" else
                             (certificate.coherence if certificate is not None else 0.0))
                path_length = 1.0 if channel == "lexical" else (
                    certificate.length if certificate is not None else observer.ideal_path_length)
                delay = 0.0 if certificate is None else certificate.delay
                paths.append(PropagationPath(
                    document.fact_id, f"{orbit}:{channel}", path_length, delay,
                    1.0 - coherence, 0.0 if coherence > 0 else math.pi / 2,
                    1, number_conflict))
        projection = ObserverRelativeCausalField(self._events).project(observer, tuple(sorted(paths)))
        return PerspectiveCompilation(observer, projection, self.orbit_members, expected)
