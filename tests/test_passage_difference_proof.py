from horizon_memory.passage_difference_proof import compile_passage_homogeneous_difference


FA = (
    "In the FA Cup 2004-05 season, 660 clubs entered the competition, beating the "
    "long-standing record of 656 from the 1921-22 season. In 2005-06 FA Cup this "
    "increased to 674 entrants, in 2006-07 FA Cup to 687."
)
POPULATION = (
    "There were 86,468 people in the peninsula compared to a pre-war population "
    "of around 200,000. The population allegedly rose to some 250,000 by 1708."
)
PROCEDURES = (
    "The American Society for Aesthetic Plastic Surgery looks at the statistics "
    "for 34 different cosmetic procedures. Nineteen of the procedures are surgical."
)


def test_binds_two_conditioned_passage_quantities_and_reopens():
    question = ("How many more clubs entered the FA Cup in the 2005-06 season "
                "compared to the 2004-05 season?")
    proof = compile_passage_homogeneous_difference(question, FA)
    assert proof is not None and proof.result == 14
    assert tuple(item.value for item in proof.operands) == (674, 660)
    assert proof.verify(question, FA)
    assert not proof.verify(question, FA + " tampered")


def test_matches_approximation_class_before_nominal_subtraction():
    question = ("How many more people lived in the area in 1708 compared to the "
                "pre-war population?")
    proof = compile_passage_homogeneous_difference(question, POPULATION)
    assert proof is not None and proof.result == 50000
    assert all(item.approximate for item in proof.operands)
    assert proof.verify(question, POPULATION)


def test_combines_explicit_question_baseline_with_bound_source_fact():
    question = ("How many more than the top five procedures does the American Society "
                "for Aesthetic Plastic Surgery looks at the statistics for?")
    proof = compile_passage_homogeneous_difference(question, PROCEDURES)
    assert proof is not None and proof.result == 29
    assert tuple(item.origin for item in proof.operands) == ("passage", "question")
    assert proof.verify(question, PROCEDURES)


def test_fails_closed_on_mixed_approximation_or_ambiguous_source_binding():
    question = "How many more people lived in 1708 compared to the pre-war population?"
    assert compile_passage_homogeneous_difference(
        question, "The population was 200,000 pre-war and around 250,000 by 1708.") is None
    assert compile_passage_homogeneous_difference(
        question, POPULATION + " The population was some 251,000 by 1708.") is None


def test_range_word_is_not_a_subtraction_certificate():
    assert compile_passage_homogeneous_difference(
        "How many points difference is the IQ range in 17-year-old students?",
        "The IQ range was from 80 points to 120 points.") is None


def test_compound_measure_requiring_aggregation_abstains():
    question = ("How many more white men and boys were there in 1727 compared to "
                "coloured women and girls?")
    passage = ("The population in 1727 included 4,470 whites (910 men; 1,261 boys; "
               "1,168 women; 1,131 girls) and 3,877 coloured (787 men; 1,158 boys; "
               "945 women; 987 girls).")
    assert compile_passage_homogeneous_difference(question, passage) is None


def test_scaled_or_percentage_quantities_require_their_own_operator():
    assert compile_passage_homogeneous_difference(
        "How many more people lived there in 2013 compared to 1790?",
        "The population was 4 million in 1790 and 316 million in 2013.") is None
    assert compile_passage_homogeneous_difference(
        "How many more voters, by percent, lived in A compared to B?",
        "There were 40 percent voters in A and 20 percent voters in B.") is None


def test_explicit_temporal_connector_beats_adjacent_next_list_value():
    question = "How many more people lived there by 1940 compared to 1700?"
    passage = (
        "The population was about 10,000 in 1700, 65,000 by 1878, and about "
        "120,000 by 1940."
    )
    proof = compile_passage_homogeneous_difference(question, passage)
    assert proof is not None and proof.result == 110000
