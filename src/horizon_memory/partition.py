# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""FH-05 / V24 — particao causal e removivel do namespace da Horizon Memory.

O modulo recebe somente sinais observaveis antes da consulta. Texto da consulta, resposta gold,
atributos do holdout e futuro da sessao nao fazem parte da interface e, portanto, nao podem virar
autoridade de particionamento por acidente.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Iterable


MAX_CONTEXT_VALUES = 32
MAX_CONTEXT_VALUE_BYTES = 256


def _canonical_values(name: str, values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{name} must be an iterable of strings, not a scalar")
    canonical = []
    for value in values:
        if not isinstance(value, str):
            raise TypeError(f"{name} entries must be strings")
        value = value.strip()
        if not value:
            raise ValueError(f"{name} entries must not be empty")
        if len(value.encode("utf-8")) > MAX_CONTEXT_VALUE_BYTES:
            raise ValueError(f"{name} entry exceeds byte limit")
        canonical.append(value)
    result = tuple(sorted(set(canonical)))
    if len(result) > MAX_CONTEXT_VALUES:
        raise ValueError(f"{name} exceeds item limit")
    return result


@dataclass(frozen=True)
class PartitionContext:
    """Contexto causal capturado na escrita ou imediatamente antes da consulta."""

    scope_id: int
    session_id: str
    active_goals: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.scope_id, int) or isinstance(self.scope_id, bool) or self.scope_id < 0:
            raise ValueError("scope_id must be a non-negative integer")
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        if len(self.session_id.strip().encode("utf-8")) > MAX_CONTEXT_VALUE_BYTES:
            raise ValueError("session_id exceeds byte limit")
        object.__setattr__(self, "session_id", self.session_id.strip())
        object.__setattr__(self, "active_goals", _canonical_values("active_goals", self.active_goals))
        object.__setattr__(self, "entities", _canonical_values("entities", self.entities))
        object.__setattr__(self, "sources", _canonical_values("sources", self.sources))


@dataclass(frozen=True)
class PartitionResult:
    partition_ids: tuple[str, ...]
    confidence: float
    provenance: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.partition_ids:
            raise ValueError("partition_ids must not be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")


class PartitionStrategy(Enum):
    NONE = "no_partition"
    SCOPE_SESSION = "scope_session"
    SCOPE_GOAL = "scope_goal"
    SCOPE_GOAL_ENTITY_SOURCE = "scope_goal_entity_source"


def _partition_id(strategy: PartitionStrategy, payload: dict) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    digest = hashlib.sha256(b"horizon-v24-partition\x00" + encoded).hexdigest()[:32]
    return f"v24:{strategy.value}:{digest}"


class CausalPartitioner:
    """Mapeamento deterministico sem estado treinavel e sem acesso ao texto da consulta."""

    def partition(self, context: PartitionContext, strategy: PartitionStrategy) -> PartitionResult:
        if not isinstance(context, PartitionContext):
            raise TypeError("context must be PartitionContext")
        if not isinstance(strategy, PartitionStrategy):
            raise TypeError("strategy must be PartitionStrategy")

        payload: dict = {"scope_id": context.scope_id}
        provenance = ["scope_id"]
        confidence = 1.0
        if strategy == PartitionStrategy.SCOPE_SESSION:
            payload["session_id"] = context.session_id
            provenance.append("session_id")
        elif strategy == PartitionStrategy.SCOPE_GOAL:
            payload["active_goals"] = context.active_goals
            provenance.append("active_goals")
            if not context.active_goals:
                confidence = 0.5
        elif strategy == PartitionStrategy.SCOPE_GOAL_ENTITY_SOURCE:
            payload.update(active_goals=context.active_goals, entities=context.entities,
                           sources=context.sources)
            provenance.extend(("active_goals", "entities", "sources"))
            populated = sum(bool(x) for x in (context.active_goals, context.entities, context.sources))
            confidence = populated / 3.0

        return PartitionResult(
            partition_ids=(_partition_id(strategy, payload),),
            confidence=confidence,
            provenance=tuple(provenance),
        )


class PartitionIndex:
    """Indice experimental exato FactId -> particao; nao valida conteudo nem substitui a Horizon."""

    def __init__(self, strategy: PartitionStrategy, partitioner: CausalPartitioner | None = None):
        if not isinstance(strategy, PartitionStrategy):
            raise TypeError("strategy must be PartitionStrategy")
        self.strategy = strategy
        self.partitioner = partitioner or CausalPartitioner()
        self._postings: dict[str, set[int]] = {}
        self._fact_partitions: dict[int, tuple[str, ...]] = {}

    def add(self, fact_id: int, context: PartitionContext) -> None:
        if not isinstance(fact_id, int) or isinstance(fact_id, bool) or fact_id < 0:
            raise ValueError("fact_id must be a non-negative integer")
        result = self.partitioner.partition(context, self.strategy)
        previous = self._fact_partitions.get(fact_id)
        if previous is not None and previous != result.partition_ids:
            raise ValueError("fact_id already belongs to a different partition")
        self._fact_partitions[fact_id] = result.partition_ids
        for partition_id in result.partition_ids:
            self._postings.setdefault(partition_id, set()).add(fact_id)

    def candidates(self, context: PartitionContext) -> tuple[int, ...]:
        result = self.partitioner.partition(context, self.strategy)
        found: set[int] = set()
        for partition_id in result.partition_ids:
            found.update(self._postings.get(partition_id, ()))
        return tuple(sorted(found))

    @property
    def fact_count(self) -> int:
        return len(self._fact_partitions)

    def contains_global(self, fact_id: int) -> bool:
        """Controle global: existencia no indice inteiro, independente da particao consultada."""
        return fact_id in self._fact_partitions

    def canonical_bytes(self) -> bytes:
        """Representacao contabilizavel do indice (sem estimativa de heap dependente do Python)."""
        payload = {
            "schema_version": 1,
            "strategy": self.strategy.value,
            "postings": [[pid, sorted(facts)] for pid, facts in sorted(self._postings.items())],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    @property
    def byte_size(self) -> int:
        return len(self.canonical_bytes())
