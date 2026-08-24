# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Lossless query-witness ordering over already-authorized evidence objects.

The primitive changes position only. It cannot create, delete, rewrite or authorize a claim;
the caller retains each object's provenance and the Horizon verifier remains the authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, TypeVar

from .materialized_proof_pressure_search import MaterializedIndependentHorizonSearchEngine
from .raw_causal_channels import RawCausalDocument


T = TypeVar("T")


@dataclass(frozen=True)
class QueryWitnessFrontloadConfig:
    """Explicit opt-in budget; no package profile enables it by default."""

    witnesses_per_query: int = 3

    def __post_init__(self) -> None:
        if self.witnesses_per_query < 1:
            raise ValueError("witnesses_per_query must be positive")


def _rank_indexes(query: str, texts: tuple[str, ...]) -> tuple[int, ...]:
    documents = tuple(RawCausalDocument(
        index + 1, text, 0, index + 1) for index, text in enumerate(texts))
    if not documents or not query.strip():
        return tuple(range(len(texts)))
    engine = MaterializedIndependentHorizonSearchEngine(
        documents, frontier_width=max(32, len(documents)))
    total_bytes = sum(len(text.encode("utf-8")) + 1 for text in texts)
    run = engine.search(
        query, max_results=len(documents), max_bytes=max(1, total_bytes),
        exploration_reserve=len(documents))
    ranked = [item.fact_id - 1 for item in run.admissions]
    seen = set(ranked)
    ranked.extend(index for index in range(len(texts)) if index not in seen)
    return tuple(ranked)


def frontload_query_witnesses(
        items: tuple[T, ...], *, final_question: str, turn_queries: tuple[str, ...] = (),
        text_of: Callable[[T], str] = str,
        config: QueryWitnessFrontloadConfig = QueryWitnessFrontloadConfig()) -> tuple[T, ...]:
    """Round-robin query witnesses first, then every untouched object in original order."""
    if not isinstance(items, tuple):
        raise TypeError("items must be a tuple so the conserved boundary is explicit")
    if not isinstance(config, QueryWitnessFrontloadConfig):
        raise TypeError("config must be QueryWitnessFrontloadConfig")
    if len(items) < 2:
        return items
    texts = tuple(text_of(item) for item in items)
    if any(not isinstance(text, str) for text in texts):
        raise TypeError("text_of must return str")
    queries = tuple(query for query in (final_question, *turn_queries)
                    if isinstance(query, str) and query.strip())
    if not queries:
        return items
    rankings = tuple(_rank_indexes(query, texts) for query in queries)
    prefix: list[int] = []
    seen: set[int] = set()
    for depth in range(config.witnesses_per_query):
        for ranking in rankings:
            candidate = next((index for index in ranking[depth:] if index not in seen), None)
            if candidate is not None:
                prefix.append(candidate)
                seen.add(candidate)
    order = prefix + [index for index in range(len(items)) if index not in seen]
    if order == list(range(len(items))):
        return items
    return tuple(items[index] for index in order)


def frontload_text_lines(answer_text: str, *, final_question: str,
                         turn_queries: tuple[str, ...] = (),
                         config: QueryWitnessFrontloadConfig = QueryWitnessFrontloadConfig()) -> str:
    """Compatibility surface for newline-delimited lossless evidence renders."""
    lines = tuple(answer_text.splitlines())
    if len(lines) < 2:
        return answer_text
    ordered = frontload_query_witnesses(
        lines, final_question=final_question, turn_queries=turn_queries,
        text_of=lambda value: value, config=config)
    return "\n".join(ordered)


def utf8_line_prefix(text: str, max_bytes: int) -> str:
    """Return the largest complete-line prefix within an exact UTF-8 byte budget."""
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    selected: list[str] = []
    used = 0
    for line in text.splitlines():
        cost = len(line.encode("utf-8")) + (1 if selected else 0)
        if used + cost > max_bytes:
            break
        selected.append(line)
        used += cost
    return "\n".join(selected)


__all__ = [
    "QueryWitnessFrontloadConfig", "frontload_query_witnesses", "frontload_text_lines",
    "utf8_line_prefix",
]
