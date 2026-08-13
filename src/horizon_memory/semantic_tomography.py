# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Adaptive charge measurements for HFEF without free-form semantic generation.

The authority supplies finite valid hypotheses.  A local model observes one charge at a time; this
module intersects those observations, detects stale/inconsistent measurements and resolves only a
unique surviving hypothesis.  It performs no model invocation.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass


_SENTENCE = re.compile(r"\S(?:.*?\S)?(?:[.!?](?=\s|$)|$)", re.DOTALL)
_LABELS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"


@dataclass(frozen=True)
class Microcitation:
    citation_id: str
    start: int
    end: int
    text: str
    sha256: str


def microcitation_ledger(source: str, max_citations: int = 64) -> tuple[Microcitation, ...]:
    """Authority-owned sentence boundaries; duplicate text remains distinguishable by position."""
    if not source or len(source) > 8192 or not 1 <= max_citations <= 256:
        raise ValueError("source or citation limit is invalid")
    citations = []
    for match in _SENTENCE.finditer(source):
        if len(citations) >= max_citations:
            raise ValueError("source exceeds the microcitation limit")
        text = match.group(0)
        citations.append(Microcitation(
            f"s{len(citations) + 1}", match.start(), match.end(), text,
            hashlib.sha256(text.encode()).hexdigest(),
        ))
    if not citations:
        raise ValueError("source contains no microcitation")
    return tuple(citations)


@dataclass(frozen=True)
class SemanticHypothesis:
    hypothesis_id: str
    charges: tuple[tuple[str, str], ...]
    payload: str | None

    def __post_init__(self) -> None:
        if not self.hypothesis_id or self.charges != tuple(sorted(self.charges)):
            raise ValueError("hypothesis id and canonically sorted charges are required")
        if len(dict(self.charges)) != len(self.charges) or any(not key or not value
                                                               for key, value in self.charges):
            raise ValueError("charges must be unique non-empty string pairs")


@dataclass(frozen=True)
class ChargeMeasurement:
    field: str
    options: tuple[str, ...]
    survivor_sha256: str
    information_bits: float


@dataclass(frozen=True)
class TomographyResult:
    state: str
    hypothesis_id: str | None
    payload: str | None
    survivor_count: int
    transcript_sha256: str
    reason: str


class AdaptiveSyndromeDecoder:
    """Choose the highest-information remaining charge, then intersect fail-closed."""

    def __init__(self, hypotheses: tuple[SemanticHypothesis, ...]):
        if len(hypotheses) < 2 or len({item.hypothesis_id for item in hypotheses}) != len(hypotheses):
            raise ValueError("at least two uniquely identified hypotheses are required")
        fields = {key for item in hypotheses for key, _ in item.charges}
        if any(set(dict(item.charges)) != fields for item in hypotheses):
            raise ValueError("all hypotheses must expose the same charge fields")
        self._hypotheses = {item.hypothesis_id: item for item in hypotheses}
        self._survivors = tuple(sorted(self._hypotheses))
        self._observed: dict[str, str] = {}
        self._transcript: list[tuple[str, str, str]] = []

    def _survivor_sha256(self) -> str:
        return hashlib.sha256("\0".join(self._survivors).encode()).hexdigest()

    def next_measurement(self) -> ChargeMeasurement | None:
        if len(self._survivors) <= 1:
            return None
        population = len(self._survivors)
        candidates = []
        fields = dict(self._hypotheses[self._survivors[0]].charges)
        for field in fields:
            if field in self._observed:
                continue
            counts: dict[str, int] = {}
            for hypothesis_id in self._survivors:
                value = dict(self._hypotheses[hypothesis_id].charges)[field]
                counts[value] = counts.get(value, 0) + 1
            if len(counts) < 2 or len(counts) > len(_LABELS):
                continue
            entropy = -sum((count / population) * math.log2(count / population)
                           for count in counts.values())
            candidates.append((-entropy, max(counts.values()), field, tuple(sorted(counts))))
        if not candidates:
            return None
        negative_entropy, _, field, options = min(candidates)
        return ChargeMeasurement(field, options, self._survivor_sha256(),
                                 round(-negative_entropy, 12))

    def observe(self, measurement: ChargeMeasurement, value: str) -> None:
        if measurement.survivor_sha256 != self._survivor_sha256():
            raise ValueError("stale charge measurement")
        if measurement.field in self._observed or value not in measurement.options:
            raise ValueError("invalid or repeated charge observation")
        before = self._survivors
        self._survivors = tuple(hypothesis_id for hypothesis_id in before
                                if dict(self._hypotheses[hypothesis_id].charges)[measurement.field]
                                == value)
        self._observed[measurement.field] = value
        self._transcript.append((measurement.field, value, measurement.survivor_sha256))
        if not self._survivors:
            raise ValueError("charge observations have an empty intersection")

    def result(self) -> TomographyResult:
        transcript = json.dumps(self._transcript, separators=(",", ":")).encode()
        digest = hashlib.sha256(transcript).hexdigest()
        if len(self._survivors) == 1:
            item = self._hypotheses[self._survivors[0]]
            return TomographyResult("resolved", item.hypothesis_id, item.payload, 1, digest,
                                    "unique charge syndrome")
        if self.next_measurement() is None:
            return TomographyResult("abstain", None, None, len(self._survivors), digest,
                                    "indistinguishable charge syndrome")
        return TomographyResult("open", None, None, len(self._survivors), digest,
                                "additional charge measurement required")


@dataclass(frozen=True)
class MeasurementCodebook:
    prompt_payload: str
    outputs: tuple[str, ...]
    constraint_trigger: str
    constrained_tails: tuple[str, ...]
    values: tuple[str, ...]

    def resolve(self, output: str) -> str:
        if output not in self.outputs:
            raise ValueError("measurement output is outside the finite codebook")
        return self.values[self.outputs.index(output)]


def measurement_codebook(context: str, measurement: ChargeMeasurement,
                         glosses: dict[str, str] | None = None) -> MeasurementCodebook:
    """Compact one-symbol readout. Exact validation closes ChoiceConstraint's first-token gap."""
    if not context.strip() or len(context) > 4096 or len(measurement.options) > len(_LABELS):
        raise ValueError("measurement context or option count is invalid")
    tails = tuple(_LABELS[index] for index in range(len(measurement.options)))
    trigger = "CHOICE:"
    outputs = tuple(f"{trigger}{tail}" for tail in tails)
    glosses = glosses or {}
    if set(glosses) - set(measurement.options) or any(not value.strip() or len(value) > 256
                                                     for value in glosses.values()):
        raise ValueError("measurement glosses must describe only finite options")
    payload = json.dumps({
        "task": "measure_semantic_charge", "context": context, "field": measurement.field,
        "options": [{"code": code, "value": value, **(
            {"meaning": glosses[value]} if value in glosses else {})}
                    for code, value in zip(tails, measurement.options)],
        "instruction": "Output exactly CHOICE:X with X replaced by one option code and no prose.",
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return MeasurementCodebook(payload, outputs, trigger, tails, measurement.options)


def semantic_measurement_codebook(context: str, measurement: ChargeMeasurement,
                                  glosses: dict[str, str] | None = None) -> MeasurementCodebook:
    """Constrain the canonical charge values themselves, avoiding an arbitrary letter gauge."""
    if not context.strip() or len(context) > 4096:
        raise ValueError("measurement context is invalid")
    if any(not value or len(value) > 128 or not value.isascii() or any(ord(char) < 32 for char in value)
           or value.startswith("CHOICE:") for value in measurement.options):
        raise ValueError("semantic measurement options must be safe bounded ASCII")
    glosses = glosses or {}
    if set(glosses) - set(measurement.options) or any(not value.strip() or len(value) > 256
                                                     for value in glosses.values()):
        raise ValueError("measurement glosses must describe only finite options")
    trigger = "CHOICE:"
    tails = measurement.options
    outputs = tuple(f"{trigger}{value}" for value in tails)
    payload = json.dumps({
        "task": "measure_semantic_charge", "context": context, "field": measurement.field,
        "options": [{"value": value, **({"meaning": glosses[value]} if value in glosses else {})}
                    for value in measurement.options],
        "instruction": "Output exactly CHOICE:<value> using one listed value and no prose.",
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return MeasurementCodebook(payload, outputs, trigger, tails, measurement.options)
