from decimal import Decimal

from lab.runners.run_d28a_candidate_geometry import (
    Atom,
    class_key,
    enumerate_programs,
    minimum_distance,
)
from lab.denotational_generalization import counterfactual_worlds


def test_program_enumeration_preserves_operand_identity_and_all_operators():
    atoms = (
        Atom("a", "score", Decimal(7)),
        Atom("b", "score", Decimal(3)),
    )
    candidates = enumerate_programs(atoms)
    assert candidates is not None
    operators = {program.operator for program, _predicate in candidates}
    assert operators == {"lookup", "count", "sum", "argmax", "argmin", "difference"}
    assert len([program for program, _ in candidates if program.operator == "difference"]) == 2


def test_predicate_fibers_do_not_mix_operands():
    atoms = (
        Atom("a", "field_goal", Decimal(3)),
        Atom("b", "touchdown", Decimal(7)),
    )
    candidates = enumerate_programs(atoms)
    assert candidates is not None
    assert all(len(program.operands) == 1 for program, _ in candidates)


def test_counterfactual_class_keeps_predicate_identity():
    atoms = ("a", "b")
    from lab.denotational_generalization import AbstractProgram
    program = AbstractProgram("sum", atoms)
    worlds = counterfactual_worlds(atoms)
    assert class_key(program, "x", worlds) != class_key(program, "y", worlds)


def test_hamming_minimum_distance_over_conserved_groups():
    classes = (
        ("count", "goal", "2", "aaa"),
        ("sum", "yards", "3", "bbb"),
    )
    assert minimum_distance(classes) == 4
