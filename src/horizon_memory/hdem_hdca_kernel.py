# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""H-DEM/H-DCA reference kernel: finite possible-world resolution over a typed CSP.

`solve_hdem_enumerative` is the obvious, slow-by-design correctness oracle (exhaustive explicit
possible-world enumeration). `solve_hdem_packed` is the fast path (generalized-arc-consistency
fixed point plus lazy environment splitting) that must equal the oracle exactly -- both return a
canonical `HDEMResult` keyed on the problem's own content hash, so any caller can differentially
verify agreement. `solve_hdca` is a further, sound UNDER-approximation restricted to binary-forest-
shaped constraint graphs (integer-bitset arc consistency): every value it resolves must equal
H-DEM's own certain answer, but it may soundly abstain where H-DEM itself would resolve (a cyclic
or higher-arity constraint network exceeds its own binary-forest capacity).

Promoted (as the exact reachable subset -- 15 of 96 top-level definitions, verified via reachability
analysis from these public entry points, since the origin file's own remaining ~85% is Simplified-
Chinese-specific research code with its own heavier external dependencies) from
`lab/cjk_covariant_span_readout.py`'s own frozen H-DEM/H-DCA reference kernel, as a dependency of
the Portuguese atomic-relations surface-role bridge (`portuguese_atomic_relations.py`). Language-
neutral: nothing in this module is specific to Portuguese, Chinese, or any other language.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import itertools
import json


@dataclass(frozen=True, order=True)
class HDEMValue:
    value: str
    fact_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.value or self.fact_ids != tuple(sorted(set(self.fact_ids))) \
                or any(fact_id < 0 for fact_id in self.fact_ids):
            raise ValueError("H-DEM values require text and canonical non-negative FactIds")


@dataclass(frozen=True, order=True)
class HDEMVariable:
    name: str
    domain: tuple[HDEMValue, ...]

    def __post_init__(self) -> None:
        if not self.name or not self.domain or self.domain != tuple(sorted(self.domain)) \
                or len({item.value for item in self.domain}) != len(self.domain):
            raise ValueError("H-DEM variable domain must be non-empty, unique and canonical")


@dataclass(frozen=True, order=True)
class HDEMConstraint:
    constraint_id: str
    variables: tuple[str, ...]
    allowed: tuple[tuple[str, ...], ...]
    witness_fact_ids: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if not self.constraint_id or not self.variables \
                or len(set(self.variables)) != len(self.variables):
            raise ValueError("H-DEM constraint needs a unique non-empty scope")
        if not self.allowed or self.allowed != tuple(sorted(set(self.allowed))) \
                or any(len(row) != len(self.variables) for row in self.allowed):
            raise ValueError("H-DEM allowed relation must be non-empty, canonical and arity-correct")
        if self.witness_fact_ids != tuple(sorted(set(self.witness_fact_ids))) \
                or any(fact_id < 0 for fact_id in self.witness_fact_ids):
            raise ValueError("H-DEM constraint witnesses must be canonical FactIds")


@dataclass(frozen=True)
class HDEMProblem:
    variables: tuple[HDEMVariable, ...]
    constraints: tuple[HDEMConstraint, ...]
    answer_variables: tuple[str, ...]

    def __post_init__(self) -> None:
        names = tuple(item.name for item in self.variables)
        if not names or names != tuple(sorted(names)) or len(set(names)) != len(names):
            raise ValueError("H-DEM variables must have unique canonical names")
        if tuple(item.constraint_id for item in self.constraints) != \
                tuple(sorted(item.constraint_id for item in self.constraints)) \
                or len({item.constraint_id for item in self.constraints}) != len(self.constraints):
            raise ValueError("H-DEM constraints must have unique canonical identities")
        if not self.answer_variables or len(set(self.answer_variables)) != len(self.answer_variables) \
                or any(name not in names for name in self.answer_variables):
            raise ValueError("H-DEM answer variables must be unique known variables")
        domains = {item.name: {value.value for value in item.domain} for item in self.variables}
        for constraint in self.constraints:
            if any(name not in domains for name in constraint.variables):
                raise ValueError("H-DEM constraint references an unknown variable")
            for row in constraint.allowed:
                if any(value not in domains[name]
                       for name, value in zip(constraint.variables, row)):
                    raise ValueError("H-DEM allowed tuple contains an out-of-domain value")

    def canonical_sha256(self) -> str:
        payload = {
            "variables": [(item.name, [(value.value, value.fact_ids)
                                        for value in item.domain])
                          for item in self.variables],
            "constraints": [(item.constraint_id, item.variables, item.allowed,
                              item.witness_fact_ids) for item in self.constraints],
            "answer_variables": self.answer_variables,
        }
        return hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True, order=True)
class HDEMWorld:
    assignment: tuple[tuple[str, str], ...]
    answer: tuple[str, ...]
    provenance: tuple[int, ...]


@dataclass(frozen=True, order=True)
class HDEMAnswerProof:
    answer: tuple[str, ...]
    monomials: tuple[tuple[int, ...], ...]


@dataclass(frozen=True)
class HDEMResult:
    state: str
    answer: tuple[str, ...] | None
    proofs: tuple[HDEMAnswerProof, ...]
    worlds: tuple[HDEMWorld, ...]
    complete: bool
    explored_states: int
    pruned_values: int
    problem_sha256: str
    reason: str


@dataclass(frozen=True)
class HDCAResult:
    state: str
    answer: tuple[str, ...] | None
    domains: tuple[tuple[str, tuple[str, ...]], ...]
    proof_fact_ids: tuple[int, ...]
    certified_acyclic: bool
    revisions: int
    problem_sha256: str
    reason: str


def _hdem_world(problem: HDEMProblem, assignment: dict[str, str]) -> HDEMWorld:
    by_variable = {item.name: {value.value: value for value in item.domain}
                   for item in problem.variables}
    provenance = {fact_id for name, value in assignment.items()
                  for fact_id in by_variable[name][value].fact_ids}
    provenance.update(fact_id for item in problem.constraints
                      for fact_id in item.witness_fact_ids)
    return HDEMWorld(
        tuple(sorted(assignment.items())),
        tuple(assignment[name] for name in problem.answer_variables),
        tuple(sorted(provenance)),
    )


def _hdem_result(problem: HDEMProblem, worlds: tuple[HDEMWorld, ...], *, complete: bool,
                 explored_states: int, pruned_values: int, reason: str) -> HDEMResult:
    grouped: dict[tuple[str, ...], set[tuple[int, ...]]] = {}
    for world in worlds:
        grouped.setdefault(world.answer, set()).add(world.provenance)
    proofs = tuple(HDEMAnswerProof(answer, tuple(sorted(monomials)))
                   for answer, monomials in sorted(grouped.items()))
    if not complete:
        state, answer = "abstain", None
    elif not proofs:
        state, answer = "abstain", None
    elif len(proofs) == 1:
        state, answer = "resolved", proofs[0].answer
    else:
        state, answer = "contested", None
    return HDEMResult(state, answer, proofs, worlds, complete, explored_states, pruned_values,
                      problem.canonical_sha256(), reason)


def solve_hdem_enumerative(problem: HDEMProblem, *, max_assignments: int = 1_000_000) \
        -> HDEMResult:
    """Obvious finite possible-world oracle. Slow by design; correctness control only."""
    if max_assignments < 1:
        raise ValueError("max_assignments must be positive")
    names = tuple(item.name for item in problem.variables)
    domains = tuple(tuple(value.value for value in item.domain) for item in problem.variables)
    constraints = tuple((item.variables, frozenset(item.allowed)) for item in problem.constraints)
    worlds = []
    explored = 0
    for values in itertools.product(*domains):
        explored += 1
        if explored > max_assignments:
            return _hdem_result(problem, tuple(worlds), complete=False,
                                explored_states=explored - 1, pruned_values=0,
                                reason="enumerative assignment budget exhausted")
        assignment = dict(zip(names, values))
        if all(tuple(assignment[name] for name in variables) in allowed
               for variables, allowed in constraints):
            worlds.append(_hdem_world(problem, assignment))
    return _hdem_result(problem, tuple(sorted(worlds)), complete=True,
                        explored_states=explored, pruned_values=0,
                        reason="complete explicit possible-world enumeration")


def _hdem_propagate(problem: HDEMProblem, domains: dict[str, set[str]]) -> tuple[bool, int]:
    """Generalized arc consistency over extensional relations, to a monotone fixed point."""
    pruned = 0
    changed = True
    while changed:
        changed = False
        for constraint in problem.constraints:
            for position, name in enumerate(constraint.variables):
                supported = {
                    row[position] for row in constraint.allowed
                    if all(row[index] in domains[other]
                           for index, other in enumerate(constraint.variables))
                }
                remove = domains[name].difference(supported)
                if remove:
                    domains[name].difference_update(remove)
                    pruned += len(remove)
                    changed = True
                    if not domains[name]:
                        return False, pruned
    return True, pruned


def solve_hdem_packed(problem: HDEMProblem, *, max_states: int = 100_000,
                      max_worlds: int = 1_000_000) -> HDEMResult:
    """Packed-domain fast path plus lazy splits; must equal the enumerative oracle exactly."""
    if max_states < 1 or max_worlds < 1:
        raise ValueError("H-DEM packed budgets must be positive")
    initial = {item.name: {value.value for value in item.domain} for item in problem.variables}
    worlds: set[HDEMWorld] = set()
    visited: set[tuple[tuple[str, tuple[str, ...]], ...]] = set()
    explored = 0
    pruned_total = 0
    exhausted = False

    def visit(domains: dict[str, set[str]]) -> None:
        nonlocal explored, pruned_total, exhausted
        if exhausted:
            return
        signature = tuple((name, tuple(sorted(values)))
                          for name, values in sorted(domains.items()))
        if signature in visited:
            return
        if explored >= max_states:
            exhausted = True
            return
        visited.add(signature)
        explored += 1
        local = {name: set(values) for name, values in domains.items()}
        consistent, pruned = _hdem_propagate(problem, local)
        pruned_total += pruned
        if not consistent:
            return
        open_names = [name for name, values in local.items() if len(values) > 1]
        if not open_names:
            assignment = {name: next(iter(values)) for name, values in local.items()}
            worlds.add(_hdem_world(problem, assignment))
            if len(worlds) > max_worlds:
                exhausted = True
            return
        name = min(open_names, key=lambda item: (len(local[item]), item))
        for value in sorted(local[name]):
            child = {key: set(values) for key, values in local.items()}
            child[name] = {value}
            visit(child)

    visit(initial)
    ordered = tuple(sorted(worlds)) if not exhausted else tuple(sorted(worlds))[:max_worlds]
    return _hdem_result(
        problem, ordered, complete=not exhausted, explored_states=explored,
        pruned_values=pruned_total,
        reason=("packed GAC fixed point with complete lazy environment split"
                if not exhausted else "packed state/world budget exhausted"),
    )


def _hdca_is_binary_forest(problem: HDEMProblem) -> bool:
    parent = {item.name: item.name for item in problem.variables}

    def root(value: str) -> str:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    seen_edges = set()
    for constraint in problem.constraints:
        if len(constraint.variables) > 2:
            return False
        if len(constraint.variables) < 2:
            continue
        edge = frozenset(constraint.variables)
        if edge in seen_edges:
            continue
        seen_edges.add(edge)
        left, right = constraint.variables
        a, b = root(left), root(right)
        if a == b:
            return False
        parent[a] = b
    return True


def solve_hdca(problem: HDEMProblem, *, max_revisions: int = 1_000_000) -> HDCAResult:
    """Edge candidate: integer-bitset arc consistency; cyclic/higher-arity networks abstain."""
    if max_revisions < 1:
        raise ValueError("H-DCA revision budget must be positive")
    by_name = {item.name: item for item in problem.variables}
    value_index = {item.name: {value.value: index for index, value in enumerate(item.domain)}
                   for item in problem.variables}
    masks = {item.name: (1 << len(item.domain)) - 1 for item in problem.variables}
    acyclic = _hdca_is_binary_forest(problem)
    if not acyclic:
        return HDCAResult("abstain", None, tuple(
            (item.name, tuple(value.value for value in item.domain)) for item in problem.variables),
            (), False, 0, problem.canonical_sha256(),
            "cyclic or higher-arity context requires the H-DEM oracle")

    revisions = 0
    changed = True
    while changed:
        changed = False
        for constraint in problem.constraints:
            variables = constraint.variables
            for position, name in enumerate(variables):
                current = masks[name]
                supported_mask = 0
                for row in constraint.allowed:
                    supported = True
                    for other_position, other_name in enumerate(variables):
                        bit = 1 << value_index[other_name][row[other_position]]
                        if not masks[other_name] & bit:
                            supported = False
                            break
                    if supported:
                        supported_mask |= 1 << value_index[name][row[position]]
                revised = current & supported_mask
                removed = (current ^ revised).bit_count()
                if removed:
                    revisions += removed
                    if revisions > max_revisions:
                        domains = tuple((item.name, tuple(
                            value.value for index, value in enumerate(item.domain)
                            if masks[item.name] & (1 << index))) for item in problem.variables)
                        return HDCAResult("abstain", None, domains, (), True, revisions - removed,
                                          problem.canonical_sha256(),
                                          "H-DCA revision budget exhausted")
                    masks[name] = revised
                    changed = True
                    if not revised:
                        domains = tuple((item.name, tuple(
                            value.value for index, value in enumerate(item.domain)
                            if masks[item.name] & (1 << index))) for item in problem.variables)
                        return HDCAResult("abstain", None, domains, (), True, revisions,
                                          problem.canonical_sha256(),
                                          "acyclic context has no consistent assignment")

    domains = tuple((item.name, tuple(value.value for index, value in enumerate(item.domain)
                                      if masks[item.name] & (1 << index)))
                    for item in problem.variables)
    answer_domains = tuple(dict(domains)[name] for name in problem.answer_variables)
    proof = {fact_id for item in problem.constraints for fact_id in item.witness_fact_ids}
    for name in problem.answer_variables:
        variable = by_name[name]
        for index, value in enumerate(variable.domain):
            if masks[name] & (1 << index):
                proof.update(value.fact_ids)
    if all(len(values) == 1 for values in answer_domains):
        answer = tuple(values[0] for values in answer_domains)
        return HDCAResult("resolved", answer, domains, tuple(sorted(proof)), True, revisions,
                          problem.canonical_sha256(), "unique answer bit in acyclic fixed point")
    return HDCAResult("contested", None, domains, tuple(sorted(proof)), True, revisions,
                      problem.canonical_sha256(), "multiple globally extensible answer bits")


__all__ = [
    "HDCAResult", "HDEMAnswerProof", "HDEMConstraint", "HDEMProblem", "HDEMResult", "HDEMValue",
    "HDEMVariable", "HDEMWorld", "solve_hdca", "solve_hdem_enumerative", "solve_hdem_packed",
]
