# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Opt-in deterministic multi-fact narrative composition -- never wired into any default routing/
ranking/answer path. Ported from `lab/fact_conjunction_aggregator.py`,
`lab/discourse_relation_classifier.py`, `lab/typed_fact_realization.py` and
`lab/narrative_plan.py` (private, gitignored) into `src/horizon_memory/narrative_composition.py`,
behavior-identical -- every test below matches its lab-side counterpart exactly."""
import hashlib

import pytest

from horizon_memory.typed_causal_program import TypedCausalFact
from horizon_memory.research import (
    DiscourseFact, DiscourseRelation, RealizedFact, aggregate_same_subject_facts,
    build_discourse_facts, classify_relation, current_value_fact, plan_narrative,
    realize_fact, render_narrative, render_pair,
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _typed_fact(fact_id, subject, predicate, event_time, source_span, source_id="doc-1",
                causes=()):
    return TypedCausalFact(
        fact_id=fact_id, scope="test", subject=subject, predicate=predicate, value="v",
        observed_at=event_time, event_time=event_time, source_id=source_id,
        source_sha256=_sha256(source_id), source_span=source_span, causes=causes,
    )


def _df(fact_id, subject, predicate_text, span, **kwargs):
    return DiscourseFact(RealizedFact(subject, predicate_text, fact_id, span), **kwargs)


# --- fact_conjunction_aggregator: same-subject conjunction reduction ---


def _fact(fact_id, subject, predicate_text, span):
    return RealizedFact(subject, predicate_text, fact_id, span)


def test_two_same_subject_facts_coordinate_with_subject_ellipsis():
    a = _fact(1, "John", "broke the window", (0, 24))
    b = _fact(2, "John", "apologized", (25, 40))
    result = aggregate_same_subject_facts((a, b))
    assert result.text == "John broke the window and apologized."
    assert result.fact_ids == (1, 2)
    assert result.source_spans == ((0, 24), (25, 40))
    assert result.rule == "same_subject_conjunction_reduction"


def test_three_same_subject_facts_use_list_conjunction_not_repeated_and():
    a = _fact(1, "Maria", "cooked dinner", (0, 10))
    b = _fact(2, "Maria", "cleaned the kitchen", (11, 20))
    c = _fact(3, "Maria", "went to bed", (21, 30))
    result = aggregate_same_subject_facts((a, b, c))
    assert result.text == "Maria cooked dinner, cleaned the kitchen, and went to bed."
    assert result.fact_ids == (1, 2, 3)


def test_different_subjects_are_never_forced_together():
    a = _fact(1, "John", "broke the window", (0, 10))
    b = _fact(2, "Maria", "apologized", (11, 20))
    assert aggregate_same_subject_facts((a, b)) is None


def test_subject_match_is_case_insensitive_but_case_preserving_in_output():
    a = _fact(1, "john", "broke the window", (0, 10))
    b = _fact(2, "John", "apologized", (11, 20))
    result = aggregate_same_subject_facts((a, b))
    assert result is not None
    assert result.text.startswith("john ")  # preserves the FIRST fact's own surface casing


def test_a_single_fact_is_not_this_mechanisms_job():
    a = _fact(1, "John", "broke the window", (0, 10))
    assert aggregate_same_subject_facts((a,)) is None


def test_an_empty_tuple_returns_none():
    assert aggregate_same_subject_facts(()) is None


def test_predicate_text_with_stray_trailing_period_is_normalized():
    a = _fact(1, "John", "broke the window.", (0, 10))
    b = _fact(2, "John", "apologized.", (11, 20))
    result = aggregate_same_subject_facts((a, b))
    assert result.text == "John broke the window and apologized."


def test_alternate_conjunction_is_a_pure_parameter_not_a_language_switch():
    a = _fact(1, "Maria", "cozinhou o jantar", (0, 10))
    b = _fact(2, "Maria", "lavou a louça", (11, 20))
    result = aggregate_same_subject_facts((a, b), conjunction="e")
    assert result.text == "Maria cozinhou o jantar e lavou a louça."


def test_realized_fact_rejects_empty_subject_or_predicate():
    with pytest.raises(ValueError):
        RealizedFact("", "broke the window", 1, (0, 10))
    with pytest.raises(ValueError):
        RealizedFact("John", "  ", 1, (0, 10))


def test_provenance_is_never_merged_or_lost():
    a = _fact(1, "John", "broke the window", (0, 10))
    b = _fact(2, "John", "apologized", (11, 20))
    c = _fact(3, "John", "left", (21, 30))
    result = aggregate_same_subject_facts((a, b, c))
    assert result.fact_ids == (1, 2, 3)
    assert result.source_spans == ((0, 10), (11, 20), (21, 30))


# --- typed_fact_realization: pure span-arithmetic realization of a TypedCausalFact ---


def test_subject_at_start_of_span_realizes_the_predicate_tail():
    source = "John broke the window."
    fact = _typed_fact(1, "John", "broke", 1, (0, len(source)))
    result = realize_fact(fact, source)
    assert result is not None
    assert result.subject == "John"
    assert result.predicate_text == "broke the window."
    assert result.fact_id == 1
    assert result.source_span == (0, len(source))


def test_subject_not_present_verbatim_fails_closed_rather_than_guessing():
    source = "John broke the window."
    fact = _typed_fact(1, "Maria", "broke", 1, (0, len(source)))
    assert realize_fact(fact, source) is None


def test_predicate_tail_never_bleeds_past_the_facts_own_recorded_span():
    first = "John broke the window."
    second = " John apologized profusely."
    source = first + second
    fact = _typed_fact(1, "John", "broke", 1, (0, len(first)))
    result = realize_fact(fact, source)
    assert result is not None
    assert result.predicate_text == "broke the window."
    assert "apologized" not in result.predicate_text


def test_empty_remainder_after_subject_fails_closed():
    source = "John."
    fact = _typed_fact(1, "John", "exist", 1, (0, len(source)))
    assert realize_fact(fact, source) is None


def test_out_of_range_source_span_fails_closed():
    source = "John broke the window."
    fact = _typed_fact(1, "John", "broke", 1, (0, len(source) + 50))
    assert realize_fact(fact, source) is None


# --- discourse_relation_classifier: pairwise CAUSE / CONTRAST / SEQUENCE / JOINT / NONE ---


def test_explicit_causal_edge_is_read_directly_not_inferred():
    cause = _df(1, "It", "rained heavily", (0, 20))
    effect = _df(2, "The match", "was postponed", (21, 45), causes=frozenset({1}))
    pair = classify_relation(cause, effect)
    assert pair.relation == DiscourseRelation.CAUSE
    assert pair.ordered == (cause, effect)
    rendered = render_pair(pair)
    assert rendered.text == "It rained heavily, so The match was postponed."
    assert rendered.fact_ids == (1, 2)


def test_causal_edge_direction_is_respected_regardless_of_argument_order():
    cause = _df(1, "It", "rained heavily", (0, 20))
    effect = _df(2, "The match", "was postponed", (21, 45), causes=frozenset({1}))
    pair = classify_relation(effect, cause)
    assert pair.relation == DiscourseRelation.CAUSE
    assert pair.ordered == (cause, effect)


def test_same_fiber_different_content_is_contrast_ordered_by_clock():
    older = _df(1, "John", "was single", (0, 10), fiber_key=("john", "status"), clock=(1, 0, 1))
    newer = _df(2, "John", "is married", (11, 20), fiber_key=("john", "status"), clock=(2, 0, 2))
    pair = classify_relation(newer, older)
    assert pair.relation == DiscourseRelation.CONTRAST
    assert pair.ordered == (older, newer)
    rendered = render_pair(pair)
    assert rendered.text == "Previously, John was single, but now is married."


def test_same_fiber_identical_content_is_a_duplicate_not_a_relation():
    a = _df(1, "John", "is married", (0, 10), fiber_key=("john", "status"), clock=(1, 0, 1))
    b = _df(2, "John", "is married", (11, 20), fiber_key=("john", "status"), clock=(1, 0, 2))
    pair = classify_relation(a, b)
    assert pair.relation == DiscourseRelation.NONE
    assert render_pair(pair) is None


def test_same_subject_different_fiber_no_causal_link_is_joint():
    a = _df(1, "Maria", "cooked dinner", (0, 10))
    b = _df(2, "Maria", "went to bed", (11, 20))
    pair = classify_relation(a, b)
    assert pair.relation == DiscourseRelation.JOINT
    rendered = render_pair(pair)
    assert rendered.text == "Maria cooked dinner and went to bed."


def test_different_subjects_with_no_structural_link_is_none_never_forced():
    a = _df(1, "John", "broke the window", (0, 10))
    b = _df(2, "Maria", "apologized", (11, 20))
    pair = classify_relation(a, b)
    assert pair.relation == DiscourseRelation.NONE
    assert render_pair(pair) is None


def test_causal_edge_takes_priority_over_shared_fiber():
    a = _df(1, "John", "was healthy", (0, 10), fiber_key=("john", "health"), clock=(1, 0, 1))
    b = _df(2, "John", "got sick", (11, 20), fiber_key=("john", "health"), clock=(2, 0, 2),
           causes=frozenset({1}))
    pair = classify_relation(a, b)
    assert pair.relation == DiscourseRelation.CAUSE


def test_portuguese_connectors_via_language_parameter_not_a_separate_code_path():
    cause = _df(1, "Choveu", "muito forte", (0, 10))
    effect = _df(2, "O jogo", "foi adiado", (11, 25), causes=frozenset({1}))
    pair = classify_relation(cause, effect)
    rendered = render_pair(pair, language="pt")
    assert rendered.text == "Choveu muito forte, portanto O jogo foi adiado."


def test_contrast_without_a_usable_clock_still_detects_the_conflict_order_disclosed():
    a = _df(1, "John", "was single", (0, 10), fiber_key=("john", "status"))
    b = _df(2, "John", "is married", (11, 20), fiber_key=("john", "status"))
    pair = classify_relation(a, b)
    assert pair.relation == DiscourseRelation.CONTRAST
    assert pair.ordered == (a, b)


def test_sequence_fires_when_same_subject_different_fiber_and_event_time_differs():
    older = _df(1, "John", "cooked dinner", (0, 10), clock=(1, 1, 1))
    newer = _df(2, "John", "cleaned the kitchen", (11, 20), clock=(1, 2, 2))
    pair = classify_relation(newer, older)
    assert pair.relation == DiscourseRelation.SEQUENCE
    assert pair.ordered == (older, newer)
    rendered = render_pair(pair)
    assert rendered.text == "First, John cooked dinner, then cleaned the kitchen."


def test_sequence_never_fires_when_event_time_ties_joint_still_wins():
    a = _df(1, "John", "cooked dinner", (0, 10), clock=(1, 1, 1))
    b = _df(2, "John", "cleaned the kitchen", (11, 20), clock=(1, 1, 2))
    pair = classify_relation(a, b)
    assert pair.relation == DiscourseRelation.JOINT


def test_sequence_never_fires_without_a_usable_clock_on_both_sides():
    a = _df(1, "John", "cooked dinner", (0, 10))
    b = _df(2, "John", "cleaned the kitchen", (11, 20), clock=(1, 2, 2))
    pair = classify_relation(a, b)
    assert pair.relation == DiscourseRelation.JOINT


def test_build_discourse_facts_wires_real_typed_facts_into_a_joint_narrative():
    first_clause = "John cooked dinner."
    second_clause = " John cleaned the kitchen."
    source = first_clause + second_clause
    fact_a = _typed_fact(1, "John", "cooked", 1, (0, len(first_clause)))
    fact_b = _typed_fact(2, "John", "cleaned", 1, (len(first_clause), len(source)))

    facts = build_discourse_facts((fact_a, fact_b), source)
    assert len(facts) == 2
    pair = classify_relation(facts[0], facts[1])
    assert pair.relation == DiscourseRelation.JOINT
    rendered = render_pair(pair)
    assert rendered.text == "John cooked dinner and cleaned the kitchen."
    assert rendered.fact_ids == (1, 2)
    assert rendered.source_spans == (fact_a.source_span, fact_b.source_span)


def test_build_discourse_facts_wires_a_real_causal_edge_into_a_cause_narrative():
    first_clause = "It rained heavily."
    second_clause = " The match was postponed."
    source = first_clause + second_clause
    cause_fact = _typed_fact(1, "It", "rain", 1, (0, len(first_clause)))
    effect_fact = _typed_fact(
        2, "The match", "postpone", 2, (len(first_clause), len(source)), causes=(1,))

    facts = build_discourse_facts((cause_fact, effect_fact), source)
    pair = classify_relation(*facts)
    assert pair.relation == DiscourseRelation.CAUSE
    rendered = render_pair(pair)
    assert rendered.text == "It rained heavily, so The match was postponed."


def test_build_discourse_facts_skips_an_unrealizable_fact_rather_than_fabricating_it():
    source = "John cooked dinner."
    realizable = _typed_fact(1, "John", "cooked", 1, (0, len(source)))
    unrealizable = _typed_fact(2, "Maria", "cleaned", 1, (0, len(source)))

    facts = build_discourse_facts((realizable, unrealizable), source)
    assert len(facts) == 1
    assert facts[0].realized.fact_id == 1


# --- narrative_plan: an arbitrary collection of facts, graph + topological order + fused render ---


def test_three_fact_same_subject_joint_chain_renders_via_the_nary_aggregator():
    a = _df(1, "Maria", "cooked dinner", (0, 10))
    b = _df(2, "Maria", "cleaned the kitchen", (11, 20))
    c = _df(3, "Maria", "went to bed", (21, 30))

    plan = plan_narrative((a, b, c))
    assert len(plan.components) == 1
    rendered = render_narrative(plan)
    assert rendered.text == "Maria cooked dinner, cleaned the kitchen, and went to bed."
    assert rendered.fact_ids == (1, 2, 3)
    assert rendered.source_spans == ((0, 10), (11, 20), (21, 30))


def test_cause_then_joint_mixed_component_orders_and_connects_correctly():
    fact1 = _df(1, "It", "rained heavily", (0, 10))
    fact2 = _df(2, "The match", "was postponed", (11, 20), causes=frozenset({1}))
    fact3 = _df(3, "The match", "was rescheduled", (21, 30))

    plan = plan_narrative((fact1, fact2, fact3))
    assert len(plan.components) == 1
    rendered = render_narrative(plan)
    assert rendered.text == (
        "It rained heavily, so The match was postponed, and was rescheduled.")
    assert rendered.fact_ids == (1, 2, 3)


def test_two_independent_components_render_as_separate_sentences_in_input_order():
    fact1 = _df(1, "It", "rained heavily", (0, 10))
    fact2 = _df(2, "The match", "was postponed", (11, 20), causes=frozenset({1}))
    unrelated = _df(3, "Maria", "left early", (21, 30))

    plan = plan_narrative((fact1, fact2, unrelated))
    assert len(plan.components) == 2
    rendered = render_narrative(plan)
    assert rendered.text == "It rained heavily, so The match was postponed. Maria left early."
    assert rendered.fact_ids == (1, 2, 3)


def test_a_genuine_order_cycle_is_reported_contested_and_never_guessed():
    fact1 = _df(1, "Alpha", "did X", (0, 10), causes=frozenset({3}))
    fact2 = _df(2, "Beta", "did Y", (11, 20), causes=frozenset({1}))
    fact3 = _df(3, "Gamma", "did Z", (21, 30), causes=frozenset({2}))

    plan = plan_narrative((fact1, fact2, fact3))
    assert len(plan.components) == 1
    assert plan.components[0].contested is True
    rendered = render_narrative(plan)
    assert rendered.text == "Alpha did X. Beta did Y. Gamma did Z."
    assert rendered.fact_ids == (1, 2, 3)


def test_a_single_unrelated_fact_is_its_own_standalone_component():
    lone = _df(1, "John", "left", (0, 10))
    plan = plan_narrative((lone,))
    assert len(plan.components) == 1
    assert plan.components[0].contested is False
    rendered = render_narrative(plan)
    assert rendered.text == "John left."
    assert rendered.fact_ids == (1,)


def test_portuguese_language_selection_applies_to_the_whole_narrative():
    fact1 = _df(1, "Choveu", "muito forte", (0, 10))
    fact2 = _df(2, "O jogo", "foi adiado", (11, 25), causes=frozenset({1}))
    plan = plan_narrative((fact1, fact2))
    rendered = render_narrative(plan, language="pt")
    assert rendered.text == "Choveu muito forte, portanto O jogo foi adiado."


# --- current_value_fact: correct current-value identification in a revision chain ---


def test_current_value_skips_a_trailing_no_anchor_confirmation_clause():
    # Reproduces the real lh-en-fr-004-shaped failure found against `domains_lh_en`, 2026-08-22:
    # the true value ($30) sits second-to-last, followed by a bare agreement clause with no
    # anchor at all.
    subject = "The bill"
    a = _df(1, subject, "originally requested $50 for dinner", (0, 10), clock=(1, 0, 0))
    b = _df(2, subject, "reduced it to $20", (11, 20), clock=(1, 1, 1))
    c = _df(3, subject, "raised it to $35", (21, 30), clock=(1, 2, 2))
    d = _df(4, subject, "argued it is $30", (31, 40), clock=(1, 3, 3))
    e = _df(5, subject, "agreed", (41, 50), clock=(1, 4, 4))

    plan = plan_narrative((a, b, c, d, e))
    assert len(plan.components) == 1
    value_fact = current_value_fact(plan.components[0])
    assert value_fact is not None
    assert value_fact.realized.fact_id == 4


def test_current_value_falls_back_to_the_literal_last_fact_when_no_anchor_exists_anywhere():
    a = _df(1, "The plan", "changed a bit", (0, 10), clock=(1, 0, 0))
    b = _df(2, "The plan", "changed again", (11, 20), clock=(1, 1, 1))
    plan = plan_narrative((a, b))
    value_fact = current_value_fact(plan.components[0])
    assert value_fact is not None
    assert value_fact.realized.fact_id == 2


def test_current_value_is_none_for_a_pure_joint_listing():
    a = _df(1, "Maria", "cooked dinner", (0, 10))
    b = _df(2, "Maria", "went to Paris", (11, 20))
    plan = plan_narrative((a, b))
    assert current_value_fact(plan.components[0]) is None


def test_current_value_is_none_for_a_pure_cause_chain():
    a = _df(1, "It", "rained heavily in Boston", (0, 10))
    b = _df(2, "The match", "was postponed", (11, 20), causes=frozenset({1}))
    plan = plan_narrative((a, b))
    assert current_value_fact(plan.components[0]) is None


def test_current_value_is_none_for_a_contested_component():
    a = _df(1, "Alpha", "did X with 5 units", (0, 10), causes=frozenset({3}))
    b = _df(2, "Beta", "did Y with 10 units", (11, 20), causes=frozenset({1}))
    c = _df(3, "Gamma", "did Z with 15 units", (21, 30), causes=frozenset({2}))
    plan = plan_narrative((a, b, c))
    assert plan.components[0].contested is True
    assert current_value_fact(plan.components[0]) is None


def test_current_value_is_none_for_a_singleton_component():
    lone = _df(1, "John", "left for Chicago", (0, 10))
    plan = plan_narrative((lone,))
    assert current_value_fact(plan.components[0]) is None
