# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Executable finite core of Kaue Oliveira Costa's Sigma-PBA calculus.

Promoted verbatim from `lab/sigma_pba.py` as a dependency of the Portuguese atomic-relations
surface-role bridge (`portuguese_atomic_relations.py`). Bindings only ever propagate from
testified, source-verified facts; incompatible answer-environments never collapse by score --
a genuine tie surfaces as `contested`, never an arbitrary pick.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Iterable


_SHA256 = re.compile(r"[0-9a-f]{64}")
_PREDICATE = re.compile(r"[a-z][a-z0-9_]*")
_VARIABLE = re.compile(r"\?[A-Z][A-Za-z0-9_]*")


def is_variable(value: str) -> bool:
    return bool(_VARIABLE.fullmatch(value))


@dataclass(frozen=True)
class SealedSource:
    source_id: str
    content: str
    sha256: str

    @classmethod
    def seal(cls, source_id: str, content: str) -> "SealedSource":
        if not source_id or not content:
            raise ValueError("source identity and content are required")
        return cls(source_id, content, hashlib.sha256(content.encode()).hexdigest())

    def verify(self) -> bool:
        return bool(self.source_id and self.content and
                    hashlib.sha256(self.content.encode()).hexdigest() == self.sha256)


def _semantic_digest(*, fact_id: int, predicate: str, arguments: tuple[str, ...], scope: str,
                     version: int, orbit: str, source_id: str, source_sha256: str,
                     source_span: tuple[int, int], compiler_rule: str,
                     assumptions: tuple[str, ...]) -> str:
    payload = {
        "arguments": arguments, "assumptions": assumptions, "compiler_rule": compiler_rule,
        "fact_id": fact_id, "orbit": orbit, "predicate": predicate, "scope": scope,
        "source_id": source_id, "source_sha256": source_sha256, "source_span": source_span,
        "version": version,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(b"horizon-sigma-pba-fact-v1\x00" + canonical).hexdigest()


@dataclass(frozen=True, order=True)
class AuthorizedFact:
    fact_id: int
    predicate: str
    arguments: tuple[str, ...]
    scope: str
    version: int
    orbit: str
    source_id: str
    source_sha256: str
    source_span: tuple[int, int]
    compiler_rule: str
    semantic_attestation: str
    assumptions: tuple[str, ...] = ()

    @classmethod
    def seal(cls, *, fact_id: int, predicate: str, arguments: tuple[str, ...], scope: str,
             source: SealedSource, source_span: tuple[int, int], compiler_rule: str,
             version: int = 1, orbit: str = "", assumptions: tuple[str, ...] = ()) \
            -> "AuthorizedFact":
        active_orbit = orbit or f"fact:{fact_id}"
        canonical_assumptions = tuple(sorted(set(assumptions)))
        digest = _semantic_digest(
            fact_id=fact_id, predicate=predicate, arguments=arguments, scope=scope,
            version=version, orbit=active_orbit, source_id=source.source_id,
            source_sha256=source.sha256, source_span=source_span,
            compiler_rule=compiler_rule, assumptions=canonical_assumptions)
        return cls(fact_id, predicate, arguments, scope, version, active_orbit,
                   source.source_id, source.sha256, source_span, compiler_rule, digest,
                   canonical_assumptions)

    def verify(self, source: SealedSource, allowed_rules: frozenset[str]) -> bool:
        start, end = self.source_span
        expected = _semantic_digest(
            fact_id=self.fact_id, predicate=self.predicate, arguments=self.arguments,
            scope=self.scope, version=self.version, orbit=self.orbit,
            source_id=self.source_id, source_sha256=self.source_sha256,
            source_span=self.source_span, compiler_rule=self.compiler_rule,
            assumptions=self.assumptions)
        return (
            self.fact_id >= 0 and bool(_PREDICATE.fullmatch(self.predicate)) and
            bool(self.arguments) and all(isinstance(item, str) and item for item in self.arguments) and
            bool(self.scope) and self.version >= 1 and bool(self.orbit) and
            self.compiler_rule in allowed_rules and _SHA256.fullmatch(self.semantic_attestation) is not None and
            self.semantic_attestation == expected and source.verify() and
            self.source_id == source.source_id and self.source_sha256 == source.sha256 and
            0 <= start < end <= len(source.content) and bool(source.content[start:end].strip()) and
            self.assumptions == tuple(sorted(set(self.assumptions)))
        )


@dataclass(frozen=True)
class RelationalGoal:
    predicate: str
    arguments: tuple[str, ...]

    def __post_init__(self) -> None:
        if not _PREDICATE.fullmatch(self.predicate) or not self.arguments or any(
                not isinstance(item, str) or not item for item in self.arguments):
            raise ValueError("goal needs a canonical predicate and non-empty arguments")


@dataclass(frozen=True)
class ConjunctiveProgram:
    goals: tuple[RelationalGoal, ...]
    output_variables: tuple[str, ...]

    def __post_init__(self) -> None:
        variables = {item for goal in self.goals for item in goal.arguments if is_variable(item)}
        if not self.goals or not self.output_variables or any(
                not is_variable(item) for item in self.output_variables):
            raise ValueError("program needs goals and canonical output variables")
        if not set(self.output_variables) <= variables:
            raise ValueError("every output variable must occur in a goal")
        if len(set(self.output_variables)) != len(self.output_variables):
            raise ValueError("output variables must be unique")


@dataclass(frozen=True, order=True)
class BindingWitness:
    variable: str
    value: str
    fact_id: int


@dataclass(frozen=True, order=True)
class ProvenancePolynomial:
    monomials: tuple[tuple[int, ...], ...]

    def __post_init__(self) -> None:
        canonical = tuple(sorted(set(tuple(sorted(set(item))) for item in self.monomials)))
        if not canonical or canonical != self.monomials or any(not item for item in canonical):
            raise ValueError("provenance polynomial must contain canonical non-empty monomials")


@dataclass(frozen=True)
class SigmaPBAOutput:
    values: tuple[str, ...]
    provenance: ProvenancePolynomial


@dataclass(frozen=True)
class SigmaPBAResult:
    state: str
    outputs: tuple[SigmaPBAOutput, ...]
    witnesses: tuple[BindingWitness, ...]
    admitted_fact_ids: tuple[int, ...]
    candidate_checks: int
    evidence_bytes: int
    environments_created: int
    reason: str


@dataclass(frozen=True)
class _Derivation:
    remaining: tuple[int, ...]
    bindings: tuple[tuple[str, str], ...]
    fact_ids: tuple[int, ...]
    assumptions: tuple[str, ...]
    witnesses: tuple[BindingWitness, ...]


class SigmaPBAExecutor:
    """Exhaustive bounded positive-conjunctive evaluator with witnessed bindings."""

    def __init__(self, *, sources: tuple[SealedSource, ...], facts: tuple[AuthorizedFact, ...],
                 scope: str, allowed_rules: frozenset[str],
                 nogoods: tuple[frozenset[str], ...] = ()):
        if not sources or not facts or not scope or not allowed_rules:
            raise ValueError("sources, facts, scope, and allowed rules are required")
        if len({item.source_id for item in sources}) != len(sources):
            raise ValueError("source identities must be unique")
        if len({item.fact_id for item in facts}) != len(facts):
            raise ValueError("FactIds must be unique")
        if any(not item for item in nogoods):
            raise ValueError("empty nogood would invalidate every environment")
        self.sources = {item.source_id: item for item in sources}
        self.facts = tuple(sorted(facts, key=lambda item: item.fact_id))
        self.by_id = {item.fact_id: item for item in self.facts}
        self.scope = scope
        self.allowed_rules = allowed_rules
        self.nogoods = tuple(nogoods)
        self.active_facts = self._authorize_active_facts()
        by_predicate: dict[tuple[str, int], list[AuthorizedFact]] = {}
        for fact in self.active_facts:
            by_predicate.setdefault((fact.predicate, len(fact.arguments)), []).append(fact)
        self.index = {key: tuple(value) for key, value in by_predicate.items()}

    def _authorize_active_facts(self) -> tuple[AuthorizedFact, ...]:
        authorized = []
        for fact in self.facts:
            source = self.sources.get(fact.source_id)
            if source is not None and fact.scope == self.scope and fact.verify(source, self.allowed_rules):
                authorized.append(fact)
        latest = {}
        for fact in authorized:
            latest[fact.orbit] = max(latest.get(fact.orbit, 0), fact.version)
        return tuple(fact for fact in authorized if fact.version == latest[fact.orbit])

    def _is_nogood(self, assumptions: Iterable[str]) -> bool:
        environment = frozenset(assumptions)
        return any(item <= environment for item in self.nogoods)

    @staticmethod
    def _unify(goal: RelationalGoal, fact: AuthorizedFact,
               bindings: tuple[tuple[str, str], ...]) \
            -> tuple[tuple[tuple[str, str], ...], tuple[BindingWitness, ...]] | None:
        result = dict(bindings)
        witnesses = []
        for expected, observed in zip(goal.arguments, fact.arguments):
            if is_variable(expected):
                prior = result.get(expected)
                if prior is not None and prior != observed:
                    return None
                if prior is None:
                    result[expected] = observed
                    witnesses.append(BindingWitness(expected, observed, fact.fact_id))
            elif expected != observed:
                return None
        return tuple(sorted(result.items())), tuple(witnesses)

    def _goal_order(self, program: ConjunctiveProgram, derivation: _Derivation) -> int:
        bound = dict(derivation.bindings)
        return max(derivation.remaining, key=lambda index: (
            sum(not is_variable(arg) or arg in bound for arg in program.goals[index].arguments),
            -len(self.index.get((program.goals[index].predicate,
                                 len(program.goals[index].arguments)), ())),
            -index,
        ))

    def execute(self, program: ConjunctiveProgram, *, max_hops: int = 6,
                max_candidate_checks: int = 100_000, max_evidence_bytes: int = 65_536,
                max_environments: int = 10_000) -> SigmaPBAResult:
        if min(max_hops, max_candidate_checks, max_evidence_bytes, max_environments) < 1:
            raise ValueError("Sigma-PBA budgets must be positive")
        if len(program.goals) > max_hops:
            return SigmaPBAResult("abstain", (), (), (), 0, 0, 1, "hop_budget_exceeded")
        agenda = [_Derivation(tuple(range(len(program.goals))), (), (), (), ())]
        completed = []
        checks = 0
        environments = 1
        admitted: set[int] = set()
        evidence_bytes = 0
        all_witnesses: set[BindingWitness] = set()

        while agenda:
            derivation = agenda.pop()
            if not derivation.remaining:
                completed.append(derivation)
                continue
            goal_index = self._goal_order(program, derivation)
            goal = program.goals[goal_index]
            candidates = self.index.get((goal.predicate, len(goal.arguments)), ())
            expanded = []
            for fact in candidates:
                checks += 1
                if checks > max_candidate_checks:
                    return SigmaPBAResult("abstain", (), tuple(sorted(all_witnesses)),
                                          tuple(sorted(admitted)), checks - 1, evidence_bytes,
                                          environments, "candidate_budget_exhausted")
                unified = self._unify(goal, fact, derivation.bindings)
                if unified is None:
                    continue
                bindings, new_witnesses = unified
                assumptions = tuple(sorted(set(derivation.assumptions).union(fact.assumptions)))
                if self._is_nogood(assumptions):
                    continue
                if fact.fact_id not in admitted:
                    cost = len(self.sources[fact.source_id].content[
                        fact.source_span[0]:fact.source_span[1]].encode())
                    if evidence_bytes + cost > max_evidence_bytes:
                        return SigmaPBAResult("abstain", (), tuple(sorted(all_witnesses)),
                                              tuple(sorted(admitted)), checks, evidence_bytes,
                                              environments, "evidence_byte_budget_exhausted")
                    admitted.add(fact.fact_id)
                    evidence_bytes += cost
                witnesses = tuple(sorted(set(derivation.witnesses).union(new_witnesses)))
                all_witnesses.update(new_witnesses)
                expanded.append(_Derivation(
                    tuple(item for item in derivation.remaining if item != goal_index),
                    bindings, tuple(sorted(set(derivation.fact_ids + (fact.fact_id,)))),
                    assumptions, witnesses))
            environments += len(expanded)
            if environments > max_environments:
                return SigmaPBAResult("abstain", (), tuple(sorted(all_witnesses)),
                                      tuple(sorted(admitted)), checks, evidence_bytes,
                                      environments - len(expanded), "environment_budget_exhausted")
            # Canonical deduplication is safe because every retained field participates in the key.
            unique = {item: item for item in expanded}
            agenda.extend(sorted(unique.values(), reverse=True, key=lambda item: (
                item.remaining, item.bindings, item.fact_ids, item.assumptions)))

        if not completed:
            return SigmaPBAResult("abstain", (), tuple(sorted(all_witnesses)),
                                  tuple(sorted(admitted)), checks, evidence_bytes,
                                  environments, "no_complete_authorized_environment")
        by_output: dict[tuple[str, ...], set[tuple[int, ...]]] = {}
        for derivation in completed:
            binding = dict(derivation.bindings)
            try:
                values = tuple(binding[item] for item in program.output_variables)
            except KeyError:
                continue
            by_output.setdefault(values, set()).add(tuple(sorted(set(derivation.fact_ids))))
        if not by_output:
            return SigmaPBAResult("abstain", (), tuple(sorted(all_witnesses)),
                                  tuple(sorted(admitted)), checks, evidence_bytes,
                                  environments, "output_variable_unbound")
        outputs = tuple(SigmaPBAOutput(values, ProvenancePolynomial(tuple(sorted(monomials))))
                        for values, monomials in sorted(by_output.items()))
        state = "resolved" if len(outputs) == 1 else "contested"
        reason = "all_complete_environments_agree" if state == "resolved" else \
                 "complete_authorized_environments_disagree"
        return SigmaPBAResult(state, outputs, tuple(sorted(all_witnesses)),
                              tuple(sorted(admitted)), checks, evidence_bytes,
                              environments, reason)

    def reopen(self, program: ConjunctiveProgram, result: SigmaPBAResult, **budgets) -> bool:
        rerun = self.execute(program, **budgets)
        return rerun.state == result.state and rerun.outputs == result.outputs and all(
            self.by_id[fact_id].verify(self.sources[self.by_id[fact_id].source_id],
                                       self.allowed_rules)
            for output in result.outputs for monomial in output.provenance.monomials
            for fact_id in monomial)
