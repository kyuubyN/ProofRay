from lab.runners.probe_d29_question_constellations import (
    delta_signature,
    edit_distance,
    mutual_unique_nearest,
    run,
    tokens,
)


def test_edit_distance_is_symmetric_and_exact():
    left = ("how", "many", "in", "first", "half")
    right = ("how", "many", "in", "second", "half")
    assert edit_distance(left, right) == edit_distance(right, left) == 1


def test_delta_signature_finds_semantic_generator():
    left = ("how", "many", "in", "first", "half")
    right = ("how", "many", "in", "second", "half")
    assert delta_signature(left, right) == (("first",), ("second",))


def test_only_unique_mutual_neighbours_form_edges():
    items = (("a", "b"), ("a", "c"), ("x", "y", "z"))
    assert mutual_unique_nearest(items) == ((0, 1, 1),)


def test_entity_and_number_changes_are_gauged_out():
    assert tokens("How many did Alice score in 2019?") == tokens("How many did Bob score in 2024?")


def test_probe_pairing_does_not_depend_on_gold_values():
    def dataset(values):
        return {"p": {"passage": "unused", "qa_pairs": [
            {"question": "How many in the first half?", "answer": {"number": values[0]}},
            {"question": "How many in the second half?", "answer": {"number": values[1]}},
        ]}}

    first = run(dataset(("2", "3")))
    second = run(dataset(("100", "100")))
    assert first["mutual_unique_nearest_edges"] == second["mutual_unique_nearest_edges"] == 1
