# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""H-PLT (Proof Lattice Attention): query a finite interpretation lattice via typed Q/K/V joins.

H-DEM (`hdem_hdca_kernel.py`) owns the packed candidate-world semantics; Proof Attention here owns
typed Q/K/V joins over authorized facts; Sigma-PBA (`sigma_pba.py`) owns the authorization and
provenance envelope. A candidate fact becomes visible only in worlds matching its complete guard.
Horizon resolves only when every complete surviving world closes the query and all of them agree
on exactly the same value -- a genuine tie surfaces as `contested`, never an arbitrary pick.

Promoted (as the exact reachable subset -- 9 of 127 top-level definitions, verified via
reachability analysis from `execute_proof_lattice_attention`/`HPLTGuardedFact`/`HPLTResult`, since
the origin file's own remaining code is a large, separate research program spanning many
unrelated experimental lines) from `lab/proof_convergent_executor.py`, as a dependency of the
Portuguese atomic-relations surface-role bridge (`portuguese_atomic_relations.py`).
"""
from __future__ import annotations

from dataclasses import dataclass

from .hdem_hdca_kernel import HDEMProblem, solve_hdem_enumerative, solve_hdem_packed
from .sigma_pba import (
    AuthorizedFact, BindingWitness, ConjunctiveProgram, ProvenancePolynomial, SealedSource,
    SigmaPBAExecutor, SigmaPBAOutput, SigmaPBAResult, is_variable,
)


@dataclass(frozen=True, order=True)
class _ProofAttentionEnvironment:
    """One immutable row in deterministic Q/K/V proof transport."""

    bindings: tuple[tuple[str, str], ...]
    fact_ids: tuple[int, ...]
    assumptions: tuple[str, ...]
    witnesses: tuple[BindingWitness, ...]


def _proof_attention_unify(arguments: tuple[str, ...], observed: tuple[str, ...],
                           bindings: tuple[tuple[str, str], ...], fact_id: int) \
        -> tuple[tuple[tuple[str, str], ...], tuple[BindingWitness, ...]] | None:
    """Boolean/provenance counterpart of a learned Q/K compatibility score."""
    result = dict(bindings)
    witnesses = []
    for expected, value in zip(arguments, observed):
        if is_variable(expected):
            prior = result.get(expected)
            if prior is not None and prior != value:
                return None
            if prior is None:
                result[expected] = value
                witnesses.append(BindingWitness(expected, value, fact_id))
        elif expected != value:
            return None
    return tuple(sorted(result.items())), tuple(witnesses)


def execute_proof_attention(executor: SigmaPBAExecutor, program: ConjunctiveProgram, *,
                            max_hops: int = 6, max_candidate_checks: int = 100_000,
                            max_evidence_bytes: int = 65_536,
                            max_environments: int = 10_000) -> SigmaPBAResult:
    """Execute typed facts as sparse deterministic attention over a proof semiring.

    ``Q`` is a relational goal, ``K`` is a fact predicate/typed argument tuple and ``V``
    is the authorized binding plus FactId. Compatibility is Boolean. Alternative values
    are conserved as separate environments and compatible facts join by provenance rather
    than being averaged.
    """
    if min(max_hops, max_candidate_checks, max_evidence_bytes, max_environments) < 1:
        raise ValueError("Proof Attention budgets must be positive")
    if len(program.goals) > max_hops:
        return SigmaPBAResult("abstain", (), (), (), 0, 0, 1, "hop_budget_exceeded")

    environments = (_ProofAttentionEnvironment((), (), (), ()),)
    remaining = set(range(len(program.goals)))
    admitted: set[int] = set()
    all_witnesses: set[BindingWitness] = set()
    candidate_checks = 0
    evidence_bytes = 0
    environments_created = 1

    while remaining:
        goal_index = max(remaining, key=lambda index: (
            sum(not is_variable(arg) for arg in program.goals[index].arguments),
            -len(executor.index.get((program.goals[index].predicate,
                                     len(program.goals[index].arguments)), ())),
            -index,
        ))
        goal = program.goals[goal_index]
        keys = executor.index.get((goal.predicate, len(goal.arguments)), ())
        expanded = []
        for environment in environments:
            for fact in keys:
                candidate_checks += 1
                if candidate_checks > max_candidate_checks:
                    return SigmaPBAResult(
                        "abstain", (), tuple(sorted(all_witnesses)), tuple(sorted(admitted)),
                        candidate_checks - 1, evidence_bytes, environments_created,
                        "candidate_budget_exhausted")
                unified = _proof_attention_unify(
                    goal.arguments, fact.arguments, environment.bindings, fact.fact_id)
                if unified is None:
                    continue
                bindings, witnesses = unified
                assumptions = tuple(sorted(set(environment.assumptions).union(fact.assumptions)))
                if any(nogood <= frozenset(assumptions) for nogood in executor.nogoods):
                    continue
                if fact.fact_id not in admitted:
                    source = executor.sources[fact.source_id]
                    cost = len(source.content[slice(*fact.source_span)].encode())
                    if evidence_bytes + cost > max_evidence_bytes:
                        return SigmaPBAResult(
                            "abstain", (), tuple(sorted(all_witnesses)), tuple(sorted(admitted)),
                            candidate_checks, evidence_bytes, environments_created,
                            "evidence_byte_budget_exhausted")
                    admitted.add(fact.fact_id)
                    evidence_bytes += cost
                joined_witnesses = tuple(sorted(set(environment.witnesses).union(witnesses)))
                all_witnesses.update(witnesses)
                expanded.append(_ProofAttentionEnvironment(
                    bindings,
                    tuple(sorted(set(environment.fact_ids + (fact.fact_id,)))),
                    assumptions,
                    joined_witnesses,
                ))
        environments_created += len(expanded)
        if environments_created > max_environments:
            return SigmaPBAResult(
                "abstain", (), tuple(sorted(all_witnesses)), tuple(sorted(admitted)),
                candidate_checks, evidence_bytes, environments_created - len(expanded),
                "environment_budget_exhausted")
        environments = tuple(sorted(set(expanded), key=lambda item: (
            item.bindings, item.fact_ids, item.assumptions, item.witnesses)))
        if not environments:
            return SigmaPBAResult(
                "abstain", (), tuple(sorted(all_witnesses)), tuple(sorted(admitted)),
                candidate_checks, evidence_bytes, environments_created,
                "no_complete_authorized_environment")
        remaining.remove(goal_index)

    by_output: dict[tuple[str, ...], set[tuple[int, ...]]] = {}
    for environment in environments:
        bindings = dict(environment.bindings)
        try:
            values = tuple(bindings[variable] for variable in program.output_variables)
        except KeyError:
            continue
        by_output.setdefault(values, set()).add(environment.fact_ids)
    if not by_output:
        return SigmaPBAResult(
            "abstain", (), tuple(sorted(all_witnesses)), tuple(sorted(admitted)),
            candidate_checks, evidence_bytes, environments_created, "output_variable_unbound")

    outputs = tuple(
        SigmaPBAOutput(values, ProvenancePolynomial(tuple(sorted(monomials))))
        for values, monomials in sorted(by_output.items())
    )
    state = "resolved" if len(outputs) == 1 else "contested"
    reason = ("all_complete_environments_agree" if state == "resolved" else
              "complete_authorized_environments_disagree")
    return SigmaPBAResult(
        state, outputs, tuple(sorted(all_witnesses)), tuple(sorted(admitted)),
        candidate_checks, evidence_bytes, environments_created, reason)


@dataclass(frozen=True, order=True)
class HPLTGuardedFact:
    """One authorized K/V candidate guarded by a finite interpretation reading."""

    fact: AuthorizedFact
    guard: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if self.guard != tuple(sorted(set(self.guard))) or any(
                not name or not value for name, value in self.guard):
            raise ValueError("H-PLT guards must be canonical non-empty variable/value pairs")


@dataclass(frozen=True)
class HPLTResult:
    state: str
    outputs: tuple[SigmaPBAOutput, ...]
    complete: bool
    world_count: int
    lattice_explored_states: int
    lattice_pruned_values: int
    candidate_checks: int
    evidence_bytes: int
    problem_sha256: str
    reason: str


def _hplt_guard_provenance(problem: HDEMProblem,
                           guard: tuple[tuple[str, str], ...]) -> tuple[int, ...]:
    by_name = {variable.name: {value.value: value.fact_ids for value in variable.domain}
               for variable in problem.variables}
    result = {fact_id for constraint in problem.constraints
              for fact_id in constraint.witness_fact_ids}
    result.update(fact_id for name, value in guard for fact_id in by_name[name][value])
    return tuple(sorted(result))


def _hplt_derivation_guard(monomial: tuple[int, ...],
                           guards: dict[int, tuple[tuple[str, str], ...]]) \
        -> tuple[tuple[str, str], ...] | None:
    result = {}
    for fact_id in monomial:
        for name, value in guards[fact_id]:
            previous = result.get(name)
            if previous is not None and previous != value:
                return None
            result[name] = value
    return tuple(sorted(result.items()))


def _hplt_find_world(problem: HDEMProblem, *,
                     required: tuple[tuple[str, str], ...] = (),
                     forbidden_guards: tuple[tuple[tuple[str, str], ...], ...] = (),
                     max_states: int = 100_000) \
        -> tuple[tuple[tuple[str, str], ...] | None, bool, int, int]:
    """Find one CSP world, with proof guards treated as nogoods.

    Returning ``(None, True, ...)`` proves unsatisfiability. ``complete=False`` means the search
    budget ended before either a witness or a proof of absence. Unit propagation over forbidden
    guards is what lets H-PLT prove coverage without enumerating irrelevant ambiguity dimensions.
    """
    domains = {variable.name: {value.value for value in variable.domain}
               for variable in problem.variables}
    for name, value in required:
        if name not in domains or value not in domains[name]:
            return None, True, 0, 0
        domains[name] = {value}
    explored = 0
    pruned = 0
    exhausted = False

    def propagate(local: dict[str, set[str]]) -> bool:
        nonlocal pruned
        changed = True
        while changed:
            changed = False
            for constraint in problem.constraints:
                for position, name in enumerate(constraint.variables):
                    supported = {row[position] for row in constraint.allowed
                                 if all(row[index] in local[other]
                                        for index, other in enumerate(constraint.variables))}
                    remove = local[name].difference(supported)
                    if remove:
                        local[name].difference_update(remove)
                        pruned += len(remove)
                        changed = True
                        if not local[name]:
                            return False
            for guard in forbidden_guards:
                if any(value not in local[name] for name, value in guard):
                    continue
                open_pairs = [(name, value) for name, value in guard
                              if len(local[name]) > 1]
                if not open_pairs:
                    return False
                if len(open_pairs) == 1:
                    name, value = open_pairs[0]
                    local[name].remove(value)
                    pruned += 1
                    changed = True
                    if not local[name]:
                        return False
        return True

    def visit(local: dict[str, set[str]]) -> tuple[tuple[str, str], ...] | None:
        nonlocal explored, exhausted
        if explored >= max_states:
            exhausted = True
            return None
        explored += 1
        current = {name: set(values) for name, values in local.items()}
        if not propagate(current):
            return None
        open_names = [name for name, values in current.items() if len(values) > 1]
        if not open_names:
            return tuple((name, next(iter(values))) for name, values in sorted(current.items()))
        name = min(open_names, key=lambda item: (len(current[item]), item))
        for value in sorted(current[name]):
            child = {key: set(values) for key, values in current.items()}
            child[name] = {value}
            found = visit(child)
            if found is not None:
                return found
            if exhausted:
                return None
        return None

    witness = visit(domains)
    return witness, not exhausted, explored, pruned


def execute_proof_lattice_attention(problem: HDEMProblem, *,
                                    guarded_facts: tuple[HPLTGuardedFact, ...],
                                    sources: tuple[SealedSource, ...], scope: str,
                                    allowed_rules: frozenset[str], program: ConjunctiveProgram,
                                    lattice_mode: str = "packed",
                                    nogoods: tuple[frozenset[str], ...] = (),
                                    max_lattice_states: int = 100_000,
                                    max_lattice_worlds: int = 100_000,
                                    max_candidate_checks: int = 100_000,
                                    max_evidence_bytes: int = 65_536,
                                    max_environments: int = 10_000) -> HPLTResult:
    """Query a finite interpretation lattice through the existing Proof Attention path.

    H-DEM owns the packed candidate-world semantics; Proof Attention owns typed Q/K/V joins; Sigma
    types own the authorization and provenance envelope. A candidate fact becomes visible only in
    worlds matching its complete guard. Horizon resolves only when every complete surviving world
    closes the query and all of them yield exactly the same value.
    """
    if not isinstance(problem, HDEMProblem) or not guarded_facts or not sources \
            or not scope or not allowed_rules:
        raise ValueError("H-PLT requires a finite problem, guarded facts, sources and authority")
    if lattice_mode not in {"enumerative", "packed", "symbolic"}:
        raise ValueError("H-PLT lattice mode must be symbolic, packed or enumerative")
    if min(max_lattice_states, max_lattice_worlds, max_candidate_checks,
           max_evidence_bytes, max_environments) < 1:
        raise ValueError("H-PLT budgets must be positive")
    if len({item.fact.fact_id for item in guarded_facts}) != len(guarded_facts):
        raise ValueError("H-PLT candidate FactIds must be unique")

    domains = {variable.name: {value.value for value in variable.domain}
               for variable in problem.variables}
    for candidate in guarded_facts:
        for name, value in candidate.guard:
            if name not in domains or value not in domains[name]:
                raise ValueError("H-PLT guard references an unknown lattice reading")

    guards_by_fact = {candidate.fact.fact_id: candidate.guard for candidate in guarded_facts}

    if lattice_mode == "symbolic":
        initial, complete, explored, pruned = _hplt_find_world(
            problem, max_states=max_lattice_states)
        if not complete:
            return HPLTResult(
                "abstain", (), False, 0, explored, pruned, 0, 0,
                problem.canonical_sha256(), "interpretation_lattice_budget_exhausted")
        if initial is None:
            return HPLTResult(
                "abstain", (), True, 0, explored, pruned, 0, 0,
                problem.canonical_sha256(), "no_complete_interpretation_world")

        executor = SigmaPBAExecutor(
            sources=sources, facts=tuple(candidate.fact for candidate in guarded_facts),
            scope=scope, allowed_rules=allowed_rules, nogoods=nogoods)
        paths = execute_proof_attention(
            executor, program, max_candidate_checks=max_candidate_checks,
            max_evidence_bytes=max_evidence_bytes, max_environments=max_environments)
        if paths.state == "abstain":
            return HPLTResult(
                "abstain", (), True, 0, explored, pruned, paths.candidate_checks,
                paths.evidence_bytes, problem.canonical_sha256(),
                "no_symbolic_proof_path_closes_the_query")

        by_output: dict[tuple[str, ...], set[tuple[int, ...]]] = {}
        live_guards = set()
        for output in paths.outputs:
            for monomial in output.provenance.monomials:
                guard = _hplt_derivation_guard(monomial, guards_by_fact)
                if guard is None:
                    continue
                witness, search_complete, states, removed = _hplt_find_world(
                    problem, required=guard, max_states=max_lattice_states)
                explored += states
                pruned += removed
                if not search_complete:
                    return HPLTResult(
                        "abstain", (), False, 0, explored, pruned,
                        paths.candidate_checks, paths.evidence_bytes,
                        problem.canonical_sha256(), "interpretation_lattice_budget_exhausted")
                if witness is None:
                    continue
                live_guards.add(guard)
                proof = tuple(sorted(set(monomial).union(
                    _hplt_guard_provenance(problem, guard))))
                by_output.setdefault(output.values, set()).add(proof)
        if not live_guards:
            return HPLTResult(
                "abstain", (), True, 0, explored, pruned, paths.candidate_checks,
                paths.evidence_bytes, problem.canonical_sha256(),
                "no_guarded_proof_path_is_interpretation_consistent")

        uncovered, coverage_complete, states, removed = _hplt_find_world(
            problem, forbidden_guards=tuple(sorted(live_guards)),
            max_states=max_lattice_states)
        explored += states
        pruned += removed
        if not coverage_complete:
            return HPLTResult(
                "abstain", (), False, 0, explored, pruned, paths.candidate_checks,
                paths.evidence_bytes, problem.canonical_sha256(),
                "interpretation_lattice_budget_exhausted")
        if uncovered is not None:
            return HPLTResult(
                "abstain", (), True, 0, explored, pruned, paths.candidate_checks,
                paths.evidence_bytes, problem.canonical_sha256(),
                "at_least_one_complete_world_does_not_close_the_query")

        outputs = tuple(SigmaPBAOutput(
            values, ProvenancePolynomial(tuple(sorted(monomials))))
            for values, monomials in sorted(by_output.items()))
        state = "resolved" if len(outputs) == 1 else "contested"
        return HPLTResult(
            state, outputs, True, 0, explored, pruned, paths.candidate_checks,
            paths.evidence_bytes, problem.canonical_sha256(),
            ("symbolic_counterexample_search_proves_consensus" if state == "resolved" else
             "symbolic_paths_prove_multiple_possible_answers"))

    if lattice_mode == "packed":
        lattice = solve_hdem_packed(
            problem, max_states=max_lattice_states, max_worlds=max_lattice_worlds)
    else:
        # The explicit product is a deliberately expensive correctness oracle.
        lattice = solve_hdem_enumerative(
            problem, max_assignments=max_lattice_states)
    if not lattice.complete:
        return HPLTResult(
            "abstain", (), False, len(lattice.worlds), lattice.explored_states,
            lattice.pruned_values, 0, 0, lattice.problem_sha256,
            "interpretation_lattice_budget_exhausted")
    if not lattice.worlds:
        return HPLTResult(
            "abstain", (), True, 0, lattice.explored_states, lattice.pruned_values,
            0, 0, lattice.problem_sha256, "no_complete_interpretation_world")

    by_output: dict[tuple[str, ...], set[tuple[int, ...]]] = {}
    total_checks = 0
    total_bytes = 0
    for world in lattice.worlds:
        assignment = dict(world.assignment)
        active = tuple(candidate.fact for candidate in guarded_facts
                       if all(assignment.get(name) == value
                              for name, value in candidate.guard))
        if not active:
            return HPLTResult(
                "abstain", (), True, len(lattice.worlds), lattice.explored_states,
                lattice.pruned_values, total_checks, total_bytes, lattice.problem_sha256,
                "interpretation_world_has_no_authorized_fact")
        executor = SigmaPBAExecutor(
            sources=sources, facts=active, scope=scope, allowed_rules=allowed_rules,
            nogoods=nogoods)
        result = execute_proof_attention(
            executor, program, max_candidate_checks=max_candidate_checks,
            max_evidence_bytes=max_evidence_bytes, max_environments=max_environments)
        total_checks += result.candidate_checks
        total_bytes += result.evidence_bytes
        if result.state == "abstain":
            return HPLTResult(
                "abstain", (), True, len(lattice.worlds), lattice.explored_states,
                lattice.pruned_values, total_checks, total_bytes, lattice.problem_sha256,
                "at_least_one_complete_world_does_not_close_the_query")
        for output in result.outputs:
            monomials = by_output.setdefault(output.values, set())
            for monomial in output.provenance.monomials:
                guard = _hplt_derivation_guard(monomial, guards_by_fact)
                if guard is None:
                    continue
                monomials.add(tuple(sorted(set(monomial).union(
                    _hplt_guard_provenance(problem, guard)))))

    outputs = tuple(SigmaPBAOutput(
        values, ProvenancePolynomial(tuple(sorted(monomials))))
        for values, monomials in sorted(by_output.items()))
    state = "resolved" if len(outputs) == 1 else "contested"
    return HPLTResult(
        state, outputs, True, len(lattice.worlds), lattice.explored_states,
        lattice.pruned_values, total_checks, total_bytes, lattice.problem_sha256,
        ("all_complete_interpretation_worlds_agree" if state == "resolved" else
         "complete_interpretation_worlds_disagree"))


__all__ = ["HPLTGuardedFact", "HPLTResult", "execute_proof_attention",
           "execute_proof_lattice_attention"]
