# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Strict structured ingest for the domain-neutral typed causal executor."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .typed_causal_program import TypedCausalFact


@dataclass(frozen=True)
class CausalSourceEnvelope:
    source_id: str
    content: str
    sha256: str

    @classmethod
    def seal(cls, source_id: str, content: str) -> "CausalSourceEnvelope":
        if not source_id or not content:
            raise ValueError("causal source needs identity and content")
        return cls(source_id, content, hashlib.sha256(content.encode()).hexdigest())

    def verify(self) -> bool:
        return bool(self.source_id and self.content and
                    hashlib.sha256(self.content.encode()).hexdigest() == self.sha256)


@dataclass(frozen=True)
class StructuredCausalDeclaration:
    fact_id: int
    scope: str
    subject: str
    predicate: str
    value: str
    source_span: tuple[int, int]
    observed_at: int
    event_time: int
    version: int = 1
    unit: str = ""
    polarity: int = 1
    asserted: bool = True
    event_id: str = ""
    causes: tuple[int, ...] = ()


class DeterministicCausalCompiler:
    """Compile caller-declared structure only when its microcitation is exact."""

    @staticmethod
    def compile(source: CausalSourceEnvelope,
                declaration: StructuredCausalDeclaration) -> TypedCausalFact:
        if not source.verify():
            raise ValueError("causal source digest mismatch")
        start, end = declaration.source_span
        if start < 0 or end <= start or end > len(source.content):
            raise ValueError("causal source span is outside the sealed source")
        if source.content[start:end] != declaration.value:
            raise ValueError("causal declaration value must equal its exact microcitation")
        return TypedCausalFact(
            declaration.fact_id, declaration.scope, declaration.subject,
            declaration.predicate, declaration.value, declaration.observed_at,
            declaration.event_time, declaration.version, declaration.unit,
            declaration.polarity, declaration.asserted, declaration.event_id,
            declaration.causes, source.source_id, source.sha256,
            declaration.source_span)

    @staticmethod
    def verify(fact: TypedCausalFact, source: CausalSourceEnvelope) -> bool:
        start, end = fact.source_span
        return (source.verify() and fact.source_id == source.source_id and
                fact.source_sha256 == source.sha256 and end <= len(source.content) and
                source.content[start:end] == fact.value)
