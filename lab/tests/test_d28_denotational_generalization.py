from decimal import Decimal

import pytest

from lab.denotational_generalization import (
    AbstractProgram,
    EvaluationView,
    TrainingView,
    abstract_question,
    counterfactual_worlds,
    denotation_consistent,
    equivalence_classes,
    evaluation_view,
    family_fold,
    semantic_signature,
)


def test_counterfactual_interference_separates_one_world_coincidence():
    world = {"left": Decimal(7), "right": Decimal(3), "literal": Decimal(4)}
    difference = AbstractProgram("difference", ("left", "right"))
    lookup = AbstractProgram("lookup", ("literal",))
    assert difference.execute(world) == lookup.execute(world) == "4"

    identities = tuple(world)
    assert semantic_signature(difference, identities) != semantic_signature(lookup, identities)
    classes = equivalence_classes((difference, lookup), identities)
    assert len(classes) == 2


def test_denotation_filter_is_explicitly_training_only_primitive():
    world = {"a": Decimal(9), "b": Decimal(2), "c": Decimal(7)}
    programs = (
        AbstractProgram("difference", ("a", "b")),
        AbstractProgram("lookup", ("c",)),
        AbstractProgram("sum", ("a", "b")),
    )
    assert denotation_consistent(programs, world, "7") == programs[:2]


def test_evaluation_view_erases_gold_by_construction():
    train = TrainingView("case", "How many?", "There were 3.", "3")
    view = evaluation_view(train)
    assert type(view) is EvaluationView
    assert not hasattr(view, "gold")


def test_abstract_family_is_stable_under_entities_and_numbers():
    first = "How many points did Alice score in 2019?"
    second = "How many points did Bob score in 2024?"
    assert abstract_question(first) == abstract_question(second)
    assert family_fold(first) == family_fold(second)


def test_counterfactual_worlds_are_reproducible_and_identity_complete():
    first = counterfactual_worlds(("x", "y", "z"))
    second = counterfactual_worlds(("x", "y", "z"))
    assert first == second
    assert all(set(world) == {"x", "y", "z"} for world in first)


@pytest.mark.parametrize(
    ("operator", "operands"),
    [("lookup", ("a", "b")), ("difference", ("a",)), ("sum", ("a", "a"))],
)
def test_invalid_programs_fail_closed(operator, operands):
    with pytest.raises(ValueError):
        AbstractProgram(operator, operands)
