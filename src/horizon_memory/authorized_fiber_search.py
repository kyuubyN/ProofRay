# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Exact indexed routing to an authorized causal fiber before HSSD verification."""
from __future__ import annotations

from dataclasses import dataclass

from .hssd_query_compiler import StructuralHSSDQueryCompiler
from .proof_pressure_search import (
    ProofPressureResult, SearchAdmission, SearchObligation,
)
from .raw_causal_channels import RawCausalDocument, observe_raw_text
from .typed_causal_program import TypedCausalFact


@dataclass(frozen=True)
class AuthorizedFiberRoute:
    state: str
    subject: str | None
    candidate_fact_ids: tuple[int, ...]
    inspected_fact_ids: int
    reason: str


class AuthorizedFiberSearchEngine:
    """O(1) subject lookup followed by work proportional to the selected fiber.

    This surface is intentionally narrow.  It only routes an exact entity conserved by
    the structural query compiler.  Missing or colliding identities fail closed instead
    of falling back to a fuzzy score under the same authority label.
    """

    def __init__(self, documents: tuple[RawCausalDocument, ...],
                 facts: tuple[TypedCausalFact, ...]):
        if not documents or not facts:
            raise ValueError("authorized fiber search requires documents and typed facts")
        by_fact = {item.fact_id: item for item in facts}
        by_doc = {item.fact_id: item for item in documents}
        if len(by_fact) != len(facts) or len(by_doc) != len(documents) or set(by_doc) != set(by_fact):
            raise ValueError("documents and facts must have the same unique FactIds")
        self.by_id = by_doc
        self.facts = by_fact
        self.byte_cost = {fact_id: len(doc.text.encode()) for fact_id, doc in by_doc.items()}
        fibers: dict[str, list[int]] = {}
        identities: dict[str, set[str]] = {}
        for fact in facts:
            fibers.setdefault(fact.subject, []).append(fact.fact_id)
            observed = observe_raw_text(fact.subject)
            # Keep the literal identifier as a conserved address. Morphological
            # observation may legitimately stem a hash ending in letters such as `ed`.
            keys = {fact.subject.casefold(), *observed.entities, *observed.lexical}
            for key in keys:
                identities.setdefault(key, set()).add(fact.subject)
        self._fibers = {subject: tuple(sorted(values)) for subject, values in fibers.items()}
        self._identity_subjects = {key: tuple(sorted(values))
                                   for key, values in identities.items()}
        self._compiler = StructuralHSSDQueryCompiler()

    def route(self, query_text: str) -> AuthorizedFiberRoute:
        plan = self._compiler.compile(query_text)
        query_keys = set(plan.address_atoms.entities) | set(plan.address_atoms.lexical)
        subjects = {subject for key in query_keys
                    for subject in self._identity_subjects.get(key, ())}
        if len(subjects) != 1:
            return AuthorizedFiberRoute(
                "abstain", None, (), 0,
                "query does not select one exact indexed causal subject")
        subject = next(iter(subjects))
        query_tokens = set(plan.address_atoms.lexical)
        fact_ids = self._fibers[subject]
        ordered = tuple(sorted(fact_ids, key=lambda fact_id: (
            -len(query_tokens.intersection(observe_raw_text(
                self.facts[fact_id].predicate).lexical)),
            self.byte_cost[fact_id], fact_id,
        )))
        return AuthorizedFiberRoute(
            "routed", subject, ordered, len(fact_ids),
            "one exact query entity selected one indexed causal subject")

    def search(self, query_text: str, *, max_results: int = 32,
               max_bytes: int | None = None,
               hard_exclusions: tuple[int, ...] = (),
               exploration_reserve: int = 0,
               core_width: int | None = None) -> ProofPressureResult:
        if max_results < 1 or (max_bytes is not None and max_bytes < 1):
            raise ValueError("positive result and byte budgets are required")
        unknown = set(hard_exclusions).difference(self.by_id)
        if unknown:
            raise ValueError("hard exclusions must reference known FactIds")
        route = self.route(query_text)
        selected = []
        admissions = []
        used = 0
        if route.state == "routed":
            for fact_id in route.candidate_fact_ids:
                if fact_id in hard_exclusions or len(selected) >= max_results:
                    continue
                cost = self.byte_cost[fact_id]
                if max_bytes is not None and used + cost > max_bytes:
                    continue
                selected.append(fact_id)
                used += cost
                admissions.append(SearchAdmission(
                    fact_id, "authorized_fiber", "exact_indexed_subject", (),
                    ("proof:typed_hssd_required",), 1.0, cost))
        obligation = SearchObligation(
            "proof:typed_hssd_required", "proof", 1.0)
        return ProofPressureResult(
            tuple(selected), (obligation,), tuple(admissions),
            (obligation.key,), False, used, tuple(sorted(hard_exclusions)))
