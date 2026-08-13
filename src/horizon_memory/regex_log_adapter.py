# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic line-regex adapter with exact source spans and causal clocks."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from .causal_adapter_protocol import CausalAdapterBatch
from .typed_causal_ingest import (
    CausalSourceEnvelope,
    DeterministicCausalCompiler,
    StructuredCausalDeclaration,
)
from .typed_causal_program import TypedCausalFact


_STAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})")


@dataclass(frozen=True)
class RegexLogCausalMapping:
    fact_id_start: int
    line_pattern: str
    value_group: str
    subject: str
    predicate: str
    unit: str = ""
    max_matches: int = 10000

    def __post_init__(self) -> None:
        if self.fact_id_start < 0 or not self.line_pattern or not self.value_group \
                or not self.subject or not self.predicate or not 1 <= self.max_matches <= 1_000_000:
            raise ValueError("invalid regex-log causal mapping")
        compiled = re.compile(self.line_pattern)
        if self.value_group not in compiled.groupindex:
            raise ValueError("regex-log value_group must be a named capture")


class RegexLogCausalAdapter:
    adapter_id = "regex-log-v1"

    @staticmethod
    def _clock(line: str) -> int:
        match = _STAMP.match(line)
        if match is None:
            raise ValueError("matched causal log line lacks a canonical timestamp")
        stamp = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S,%f").replace(
            tzinfo=timezone.utc)
        return int(stamp.timestamp() * 1000)

    @classmethod
    def compile(cls, source_id: str, content: str, scope: str,
                mappings: tuple[RegexLogCausalMapping, ...]) -> tuple[TypedCausalFact, ...]:
        if not mappings or len(set(mappings)) != len(mappings):
            raise ValueError("unique regex-log mappings are required")
        source = CausalSourceEnvelope.seal(source_id, content)
        lines = tuple(content.splitlines(keepends=True))
        facts = []
        occupied = set()
        ordinals = [0] * len(mappings)
        offset = 0
        for line in lines:
            for mapping_index, mapping in enumerate(mappings):
                match = re.search(mapping.line_pattern, line)
                if match is None:
                    continue
                ordinal = ordinals[mapping_index]
                if ordinal >= mapping.max_matches:
                    raise ValueError("regex-log mapping exceeded max_matches")
                fact_id = mapping.fact_id_start + ordinal
                if fact_id in occupied:
                    raise ValueError("regex-log FactId ranges overlap")
                occupied.add(fact_id)
                start, end = match.span(mapping.value_group)
                value = match.group(mapping.value_group)
                clock = cls._clock(line)
                declaration = StructuredCausalDeclaration(
                    fact_id, scope, mapping.subject, mapping.predicate, value,
                    (offset + start, offset + end), clock, clock,
                    event_id=f"{source_id}:{mapping.predicate}:{ordinal}", unit=mapping.unit)
                facts.append(DeterministicCausalCompiler.compile(source, declaration))
                ordinals[mapping_index] += 1
            offset += len(line)
        if not facts:
            raise ValueError("regex-log adapter produced no facts")
        return tuple(sorted(facts))

    @classmethod
    def compile_batch(cls, batch: CausalAdapterBatch) -> tuple[TypedCausalFact, ...]:
        if any(not isinstance(item, RegexLogCausalMapping) for item in batch.declarations):
            raise TypeError("regex-log declarations must be RegexLogCausalMapping values")
        return cls.compile(batch.source_id, batch.content, batch.scope,
                           tuple(batch.declarations))
