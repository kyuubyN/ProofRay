# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Standalone proof-pressure retrieval for Horizon sufficient statistics.

Retrieval surfaces may propose FactIds, but admission is driven by the unresolved
query obligations they close.  No model, API, VTE runtime, or answer label is used.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

from .contextual_cavity import ContextualCavityIndex
from .raw_causal_channels import (
    RawCausalDocument,
    RawCausalSyndromeIndex,
    observe_raw_text,
)


@dataclass(frozen=True)
class SearchObligation:
    key: str
    kind: str
    weight: float


@dataclass(frozen=True)
class SearchAdmission:
    fact_id: int
    mode: str
    reason: str
    closed: tuple[str, ...]
    residual_after: tuple[str, ...]
    pressure_gain: float
    byte_cost: int
    witness_fact_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class ProofPressureResult:
    fact_ids: tuple[int, ...]
    obligations: tuple[SearchObligation, ...]
    admissions: tuple[SearchAdmission, ...]
    residual: tuple[str, ...]
    proof_closed: bool
    bytes_selected: int
    excluded: tuple[int, ...]


class HorizonSearchEngine:
    """Multi-surface search whose scarce resource is proof closure, not score mass."""

    _KIND_WEIGHT = {
        "entity": 3.0,
        "number": 3.0,
        "temporal": 3.0,
        "relation": 2.0,
        "answer": 2.5,
        "polarity": 2.5,
        "modality": 2.0,
        "lexical": 1.0,
    }

    def __init__(
        self,
        documents: tuple[RawCausalDocument, ...],
        *,
        cavity_radius: int = 3,
        cavity_decay: float = .75,
        cavity_forward_weight: float = 1.0,
        cavity_backward_weight: float = .5,
        speaker_weight: float = 1.0,
        role_weight: float = .2,
        sublexical_weight: float = .25,
        core_width: int = 1,
        frontier_width: int = 32,
    ):
        if not documents or core_width < 1 or frontier_width < 1:
            raise ValueError("documents and positive core/frontier widths are required")
        if len({item.fact_id for item in documents}) != len(documents):
            raise ValueError("FactIds must be unique")
        if min(speaker_weight, role_weight, sublexical_weight) < 0:
            raise ValueError("search weights cannot be negative")
        self.documents = documents
        self.by_id = {item.fact_id: item for item in documents}
        self.channels = {item.fact_id: observe_raw_text(item.text) for item in documents}
        self.index = RawCausalSyndromeIndex(documents)
        self.cavity = ContextualCavityIndex(
            documents,
            radius=cavity_radius,
            decay=cavity_decay,
            forward_weight=cavity_forward_weight,
            backward_weight=cavity_backward_weight,
        )
        self.speaker_weight = speaker_weight
        self.role_weight = role_weight
        self.sublexical_weight = sublexical_weight
        self.core_width = core_width
        self.frontier_width = frontier_width
        self.byte_cost = {item.fact_id: len(item.text.encode("utf-8")) for item in documents}
        self._query_cache: dict[str, tuple[
            tuple[SearchObligation, ...],
            tuple[tuple[str, tuple[int, ...]], ...],
            dict[int, tuple[int, ...]],
            dict[int, set[str]],
        ]] = {}

    def compile_obligations(self, query_text: str) -> tuple[SearchObligation, ...]:
        query = observe_raw_text(query_text, question=True)
        counts = Counter(query.lexical)
        obligations: dict[str, SearchObligation] = {}

        def add(kind: str, value: str, weight: float | None = None) -> None:
            key = f"{kind}:{value}"
            obligations[key] = SearchObligation(
                key, kind, self._KIND_WEIGHT[kind] if weight is None else weight)

        for token in sorted(counts):
            # Repeated query terms are not repeated proof mass.
            add("lexical", token, 1.0 + min(.5, .15 * (counts[token] - 1)))
        for value in query.entities:
            add("entity", value)
        for value in query.numbers:
            add("number", value)
        for value in query.temporal:
            add("temporal", value)
        for value in query.relations:
            add("relation", value)
        if query.interrogative != "none":
            add("answer", query.interrogative)
        if query.polarity == "negative":
            add("polarity", "negative")
        if query.modality == "modal":
            add("modality", "modal")
        return tuple(sorted(obligations.values(), key=lambda item: item.key))

    def _coverage(self, fact_id: int, obligations: tuple[SearchObligation, ...]) -> set[str]:
        value = self.channels[fact_id]
        result: set[str] = set()
        for obligation in obligations:
            kind, target = obligation.kind, obligation.key.split(":", 1)[1]
            if kind == "lexical" and target in value.lexical:
                result.add(obligation.key)
            elif kind == "entity" and target in value.entities:
                result.add(obligation.key)
            elif kind == "number" and target in value.numbers:
                result.add(obligation.key)
            elif kind == "temporal" and target in value.temporal:
                result.add(obligation.key)
            elif kind == "relation" and target in value.relations:
                result.add(obligation.key)
            elif kind == "polarity" and value.polarity == target:
                result.add(obligation.key)
            elif kind == "modality" and value.modality == target:
                result.add(obligation.key)
            elif kind == "answer":
                if target == "time" and (value.temporal or value.numbers):
                    result.add(obligation.key)
                elif target == "quantity" and value.numbers:
                    result.add(obligation.key)
                elif target in ("person", "place") and value.entities:
                    result.add(obligation.key)
                elif target == "boolean" and value.lexical:
                    result.add(obligation.key)
        return result

    @staticmethod
    def _rank_ids(rows, key) -> tuple[int, ...]:
        return tuple(item.fact_id for item in sorted(rows, key=key))

    def _surfaces(self, query_text: str) -> tuple[
        tuple[tuple[str, tuple[int, ...]], ...], dict[int, tuple[int, ...]]
    ]:
        components = self.index.components(query_text)
        # The ballistic core is the exact lexical control.  Auxiliary observables
        # belong to a separate field surface and cannot contaminate its head.
        direct = self.index.rank(components, (1.0, 0.0, 0.0, 0.0, 0.0, 0.0))
        field = self.index.rank(
            components, (1.0, self.sublexical_weight, .5, .2, .25, 1.0))
        entity = self._rank_ids(
            components,
            lambda item: (-item.entity, -item.observable, -item.lexical, item.fact_id),
        )
        relation = self._rank_ids(
            components,
            lambda item: (-item.relation, -item.sublexical, -item.lexical, item.fact_id),
        )
        observable = self._rank_ids(
            components,
            lambda item: (-item.observable, item.contradiction, -item.lexical, item.fact_id),
        )
        cavity_rows = self.cavity.rank(
            query_text,
            lexical_weight=1.0,
            sublexical_weight=self.sublexical_weight,
            speaker_weight=self.speaker_weight,
            role_weight=self.role_weight,
        )
        witnesses = {item.fact_id: item.witness_fact_ids for item in cavity_rows}
        modes = (
            ("cavity", tuple(item.fact_id for item in cavity_rows)),
            ("direct", tuple(item.fact_id for item in direct)),
            ("entity", entity),
            ("field", tuple(item.fact_id for item in field)),
            ("observable", observable),
            ("relation", relation),
        )
        return tuple(sorted(modes)), witnesses

    def search(
        self,
        query_text: str,
        *,
        max_results: int = 32,
        max_bytes: int | None = None,
        hard_exclusions: tuple[int, ...] = (),
        exploration_reserve: int = 0,
        core_width: int | None = None,
    ) -> ProofPressureResult:
        if not query_text.strip() or max_results < 1 or exploration_reserve < 0:
            raise ValueError("query, positive result limit, and non-negative reserve are required")
        if max_bytes is not None and max_bytes < 1:
            raise ValueError("byte budget must be positive")
        active_core_width = self.core_width if core_width is None else core_width
        if active_core_width < 1:
            raise ValueError("core width must be positive")
        unknown = set(hard_exclusions).difference(self.by_id)
        if unknown:
            raise ValueError("hard exclusions must reference known FactIds")

        cached = self._query_cache.get(query_text)
        if cached is None:
            obligations = self.compile_obligations(query_text)
            modes, cavity_witnesses = self._surfaces(query_text)
            coverage = {fact_id: self._coverage(fact_id, obligations)
                        for fact_id in self.by_id}
            cached = (obligations, modes, cavity_witnesses, coverage)
            self._query_cache[query_text] = cached
        obligations, modes, cavity_witnesses, coverage = cached
        obligation_by_key = {item.key: item for item in obligations}
        by_mode = dict(modes)
        mode_rank = {
            (mode, fact_id): rank
            for mode, fact_ids in modes
            for rank, fact_id in enumerate(fact_ids)
        }
        excluded = set(hard_exclusions)
        selected: list[int] = []
        admissions: list[SearchAdmission] = []
        closed: set[str] = set()
        used_bytes = 0

        def admit(fact_id: int, mode: str, reason: str, gain: float) -> bool:
            nonlocal used_bytes
            if fact_id in excluded or fact_id in selected:
                return False
            cost = self.byte_cost[fact_id]
            if max_bytes is not None and used_bytes + cost > max_bytes:
                return False
            newly_closed = tuple(sorted(coverage[fact_id].difference(closed)))
            selected.append(fact_id)
            closed.update(newly_closed)
            used_bytes += cost
            admissions.append(SearchAdmission(
                fact_id=fact_id,
                mode=mode,
                reason=reason,
                closed=newly_closed,
                residual_after=tuple(sorted(set(obligation_by_key).difference(closed))),
                pressure_gain=round(gain, 9),
                byte_cost=cost,
                witness_fact_ids=cavity_witnesses.get(fact_id, ()) if mode == "cavity" else (),
            ))
            return True

        # Ballistic lexical light is protected before the plural halo competes.
        for fact_id in by_mode["direct"]:
            if len(selected) >= min(active_core_width, max_results):
                break
            new = coverage[fact_id].difference(closed)
            gain = sum(obligation_by_key[key].weight for key in new)
            admit(fact_id, "direct", "protected_core", gain)

        frontier = {
            mode: tuple(fact_id for fact_id in fact_ids[:self.frontier_width]
                        if fact_id not in excluded)
            for mode, fact_ids in modes
        }
        while len(selected) < max_results:
            candidates = []
            for mode, fact_ids in frontier.items():
                for fact_id in fact_ids:
                    if fact_id in selected:
                        continue
                    new = coverage[fact_id].difference(closed)
                    closure = sum(obligation_by_key[key].weight for key in new)
                    reciprocal = 1.0 / (1.0 + mode_rank[(mode, fact_id)])
                    # Structural routes can reveal a center through a matching witness, but
                    # this small term can never outweigh a newly closed typed obligation.
                    structural = .35 if mode == "cavity" and cavity_witnesses.get(fact_id) else 0.0
                    cost_penalty = math.log2(1 + self.byte_cost[fact_id]) / 100.0
                    gain = closure + reciprocal + structural - cost_penalty
                    candidates.append((closure, gain, -self.byte_cost[fact_id],
                                       -mode_rank[(mode, fact_id)], mode, -fact_id, fact_id))
            if not candidates:
                break
            best = max(candidates)
            closure, gain, _, _, mode, _, fact_id = best
            if closure <= 0:
                break
            if not admit(fact_id, mode, "residual_reduction", gain):
                frontier[mode] = tuple(value for value in frontier[mode] if value != fact_id)

        # Exploration is explicit and separately labelled; it never claims proof closure.
        remaining_reserve = min(exploration_reserve, max_results - len(selected))
        if remaining_reserve:
            pool = []
            for mode, fact_ids in modes:
                for fact_id in fact_ids[:self.frontier_width]:
                    if fact_id in selected or fact_id in excluded:
                        continue
                    reciprocal = 1.0 / (1.0 + mode_rank[(mode, fact_id)])
                    structural = .35 if mode == "cavity" and cavity_witnesses.get(fact_id) else 0.0
                    pool.append((reciprocal + structural, -mode_rank[(mode, fact_id)],
                                 mode, -fact_id, fact_id))
            while pool and remaining_reserve:
                gain, _, mode, _, fact_id = max(pool)
                pool = [item for item in pool if item[-1] != fact_id]
                if admit(fact_id, mode, "explicit_exploration", gain):
                    remaining_reserve -= 1

        residual = tuple(sorted(set(obligation_by_key).difference(closed)))
        return ProofPressureResult(
            fact_ids=tuple(selected),
            obligations=obligations,
            admissions=tuple(admissions),
            residual=residual,
            proof_closed=not residual,
            bytes_selected=used_bytes,
            excluded=tuple(sorted(excluded)),
        )
