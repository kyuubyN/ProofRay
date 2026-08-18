from fractions import Fraction

import pytest

from lab.residual_algebra import AlgebraAtom, synthesize_residual


def atom(identity, value, dimension="points"):
    return AlgebraAtom(identity, Fraction(value), dimension)


def test_residual_synthesis_discovers_addition_without_a_question_template():
    circuits = synthesize_residual((atom("a", 7), atom("b", 3)), Fraction(10), max_depth=1)
    assert any(circuit.operator == "add" for circuit in circuits)
    assert all(circuit.execute({"a": Fraction(7), "b": Fraction(3)}) == 10
               for circuit in circuits)


def test_residual_synthesis_discovers_multiplication():
    circuits = synthesize_residual((atom("a", 7), atom("b", 3)), Fraction(21), max_depth=1)
    assert any(circuit.operator == "multiply" for circuit in circuits)


def test_fact_identity_cannot_be_duplicated_to_manufacture_answer():
    circuits = synthesize_residual((atom("a", 7),), Fraction(14), max_depth=2)
    assert circuits == ()


def test_incompatible_units_cannot_add():
    circuits = synthesize_residual(
        (atom("a", 7, "yards"), atom("b", 3, "points")),
        Fraction(10), target_dimension="yards", max_depth=1)
    assert circuits == ()


def test_equal_units_divide_to_scalar():
    circuits = synthesize_residual(
        (atom("a", 8, "yards"), atom("b", 2, "yards")),
        Fraction(4), target_dimension="scalar", max_depth=1)
    assert any(circuit.operator == "divide" for circuit in circuits)


def test_budget_exhaustion_is_explicit_not_silent():
    atoms = tuple(atom(chr(ord("a") + index), index + 1) for index in range(6))
    with pytest.raises(RuntimeError):
        synthesize_residual(atoms, Fraction(999), max_depth=2, max_classes=7)


def test_fast_eclasses_preserves_identities_and_dimensions():
    from lab.runners.run_d31_residual_algebraic_closure import generate_residual_eclasses, abstract_circuit_shape
    atoms = (atom("a", 10, "scalar"), atom("b", 4, "scalar"), atom("c", 2, "scalar"))
    nodes = generate_residual_eclasses(atoms, max_depth=2, max_classes=1000)
    assert len(nodes) > 0
    # Check that no node has duplicated fact ids
    for n in nodes:
        assert len(n.fact_ids) == len(set(n.fact_ids))
        assert n.dimension == "scalar"
    # Check that shapes are extracted properly
    shapes = {abstract_circuit_shape(n) for n in nodes}
    assert "add($,$)" in shapes
    assert "divide($,$)" in shapes


def test_abstract_circuit_shape_canonical():
    from lab.runners.run_d31_residual_algebraic_closure import generate_residual_eclasses, abstract_circuit_shape
    atoms = (atom("a", 10), atom("b", 5))
    nodes = generate_residual_eclasses(atoms, max_depth=1)
    shapes = {abstract_circuit_shape(n) for n in nodes}
    assert "$" in shapes
    assert "add($,$)" in shapes
    assert "subtract($,$)" in shapes

