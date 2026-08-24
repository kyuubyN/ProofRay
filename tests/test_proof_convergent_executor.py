from horizon_memory.routing import RouteDocument
from unittest.mock import patch
import pytest
from horizon_memory.proof_convergent_executor import (
    AttestedScalarLedger, compact_scalar_answer, integrate_with_deterministic_fallback,
    open_compact_scalar_answer, render_convergent_answer, WordNetNounGraph,
    ConservedLinkEdge, ConservedLinkForest, ConservedLinkGraph, ConservedLinkWord,
    converged_binary_event, focus_interrogative_clause,
    link_graph_to_authorized_hypergraph, project_conserved_binary_event,
    resolve_conserved_attribute, resolve_conserved_binary_relation, conserved_span_answer,
    resolve_binary_relation_via_sigma,
    resolve_binary_relation_via_sat_sigma,
    compile_surface_binary_demand,
    compile_surface_binary_checks,
    LinkGrammarBridge,
)
from lab.runners.run_d145_longmemeval_composer_judge_pilot import (
    _compose_one_integrated, _compose_one_paired,
)
from lab.runners.run_d145_longmemeval_composer_judge_pilot import (
    CALIBRATION_EPISODE_COUNT, CALIBRATION_START_ORDINAL, TOTAL_EPISODES,
)


def docs(*texts):
    return tuple(RouteDocument(i, text, 1, "s", 1, f"source:{i}", role="user")
                 for i, text in enumerate(texts, 1))


def test_frozen_corpus_range_contract_leaves_post_calibration_holdout():
    assert (CALIBRATION_START_ORDINAL, CALIBRATION_EPISODE_COUNT) == (200, 200)
    assert TOTAL_EPISODES == 500
    assert CALIBRATION_START_ORDINAL + CALIBRATION_EPISODE_COUNT == 400


def test_sum_converges_across_selector_gauges_with_exact_proofs():
    ledger = AttestedScalarLedger.build(docs(
        "My camping trip to Big Sur lasted 3 days.",
        "The camping trip in Yosemite lasted 5 days.",
        "My unrelated course lasted 10 days.",
    ))
    answer = ledger.sum_convergent("How many days did I spend camping in total?")
    assert (answer.state, answer.value, answer.unit) == ("resolved", "8", "day")
    assert answer.worlds[0].fact_ids == (1, 2)
    assert answer.surface_complete and not answer.semantic_complete


def test_selector_disagreement_is_contested_not_silently_ranked():
    ledger = AttestedScalarLedger.build(docs(
        "My road trip to York lasted 5 days.",
        "My trip to New York lasted 10 days.",
    ))
    answer = ledger.sum_convergent("How many days in total was my trip to New York?")
    assert answer.state == "contested"
    assert len(answer.worlds) >= 2


def test_repeated_report_collapses_only_with_same_measure_and_strong_identity():
    ledger = AttestedScalarLedger.build(docs(
        "My Big Sur camping trip lasted 3 days.",
        "That Big Sur camping trip lasted 3 days.",
    ))
    answer = ledger.sum_convergent("How many days was the Big Sur camping trip in total?")
    assert answer.state == "resolved"
    assert answer.value == "3"


def test_uncertain_measurement_never_executes():
    ledger = AttestedScalarLedger.build(docs("Maybe the trip will last about 4 days."))
    answer = ledger.sum_convergent("How many days did the trips take in total?")
    assert answer.state == "abstain"


def test_currency_symbol_conserves_currency_without_topic_catalogue():
    ledger = AttestedScalarLedger.build(docs(
        "I spent $250 on the watch.", "I spent $600 on the handbag."))
    answer = ledger.sum_convergent("How much money did I spend on the watch and handbag in total?")
    assert (answer.state, answer.value, answer.unit) == ("resolved", "850", "USD")


def test_irregular_inflection_and_post_unit_half_are_surface_normalization():
    drive = AttestedScalarLedger.build(docs(
        "I drove for four hours to A.", "I drove for six hours to B."))
    answer = drive.sum_convergent("How many hours did I spend driving in total?")
    assert (answer.state, answer.value) == ("resolved", "10")
    watch = AttestedScalarLedger.build(docs(
        "I watched the first series in two weeks.",
        "I watched the second series in a week and a half."))
    answer = watch.sum_convergent("How many weeks did I spend watching all the series?")
    assert (answer.state, answer.value) == ("resolved", "3.5")


def test_question_cardinality_and_entity_obligations_eliminate_incomplete_worlds():
    road = AttestedScalarLedger.build(docs(
        "My road trip to A took four hours.",
        "I drove five hours to B.",
        "I drove six hours to C.",
        "The game was three weeks ago."))
    answer = road.sum_convergent(
        "How many hours did I spend driving to my three road trip destinations combined?")
    assert (answer.state, answer.value) == ("resolved", "15")

    movies = AttestedScalarLedger.build(docs(
        "I watched the Marvel Cinematic Universe in two weeks.",
        "I watched the main Star Wars films in a week and a half."))
    answer = movies.sum_convergent(
        "How many weeks did I take to watch all the Marvel Cinematic Universe and Star Wars?")
    assert (answer.state, answer.value) == ("resolved", "3.5")


def test_non_aggregate_lookup_cannot_cross_the_sum_executor():
    ledger = AttestedScalarLedger.build(docs("I practice guitar for 30 minutes daily."))
    assert ledger.sum_convergent(
        "How much time do I practice violin every day?").state == "unsupported"
    assert ledger.sum_convergent(
        "Where did I go on a week-long trip?").state == "unsupported"


def test_attended_event_count_closes_named_orbits_and_collapses_role_repeats():
    ledger = AttestedScalarLedger.build(docs(
        "I just got back from my college roommate's wedding. My friend Emily tied the knot "
        "with her partner Sarah.",
        "I've been to a few weddings recently; one was my cousin's wedding at a vineyard.",
        "I last wore the locket to my cousin's wedding last month.",
        "I loved my cousin Rachel's wedding, and the vineyard was beautiful.",
        "I just got back from my friend's wedding; the bride, Jen, and her husband Tom were happy.",
        "My sister's wedding was amazing.",
        "I am planning my own wedding next year.",
    ))
    answer = ledger.attended_event_count_convergent(
        "How many weddings have I attended in this year?")
    assert (answer.state, answer.value, answer.unit) == ("resolved", "3", "count")


def test_attended_event_count_abstains_on_unidentified_or_ambiguous_orbit():
    unidentified = AttestedScalarLedger.build(docs(
        "I've been to a wedding recently, but I did not identify the couple."))
    assert unidentified.attended_event_count_convergent(
        "How many weddings have I attended in this year?").state == "abstain"
    ambiguous = AttestedScalarLedger.build(docs(
        "I just got back from my cousin Rachel's wedding.",
        "I just got back from my cousin Alex's wedding.",
        "I last wore it to my cousin's wedding."))
    assert ambiguous.attended_event_count_convergent(
        "How many weddings have I attended in this year?").state == "abstain"


def test_scoped_duration_closes_completed_events_and_excludes_negative_or_habitual_text():
    camping = AttestedScalarLedger.build(docs(
        "I just got back from a 5-day camping trip to Yellowstone.",
        "I completed a 3-day camping trip to Big Sur.",
        "The 7-day road trip was not camping this time."))
    answer = camping.scoped_duration_sum_convergent(
        "How many days did I spend on camping trips in the United States this year?")
    assert (answer.state, answer.value, answer.unit) == ("resolved", "8", "day")

    exercise = AttestedScalarLedger.build(docs(
        "I went for a 30-minute jog on Saturday.",
        "I used to practice yoga three times a week, each time for 2 hours."))
    answer = exercise.scoped_duration_sum_convergent(
        "How many hours of jogging and yoga did I do last week?")
    assert (answer.state, answer.value, answer.unit) == ("resolved", "0.5", "hour")


def test_scoped_duration_transports_place_only_inside_attested_session_group():
    documents = docs(
        "I just got back from an island-hopping trip to Hawaii with my family.",
        "With my family, we planned everything for the 10-day trip far in advance.",
        "I got back from New York City after five days.",
        "I am thinking of spending 4 days in Paris." )
    ledger = AttestedScalarLedger.build(documents, fact_groups={1: 7, 2: 7, 3: 8, 4: 9})
    answer = ledger.scoped_duration_sum_convergent(
        "How many days did I spend in total traveling in Hawaii and in New York City?")
    assert (answer.state, answer.value, answer.unit) == ("resolved", "15", "day")


def test_scoped_duration_adds_one_day_only_for_explicit_dated_singular_attendance():
    ledger = AttestedScalarLedger.build(docs(
        "I attended a lecture on the 10th of April.",
        "I attended a 2-day workshop on the 17th and 18th of April.",
        "I attended a conference in February."))
    answer = ledger.scoped_duration_sum_convergent(
        "How many days did I spend attending workshops, lectures, and conferences in April?")
    assert (answer.state, answer.value, answer.unit) == ("resolved", "3", "day")


def test_artifact_event_count_uses_scale_and_kit_types_then_collapses_repeats():
    ledger = AttestedScalarLedger.build(docs(
        "I just got my new 1/72 scale B-29 bomber model kit and a 1/24 scale '69 Camaro.",
        "I recently finished a simple Revell F-15 Eagle kit.",
        "I started working on a diorama with a 1/16 scale German Tiger I tank.",
        "I recently finished a Tamiya 1/48 scale Spitfire Mk.V.",
        "I worked on weathering my 1/72 scale B-29 bomber model kit again."))
    answer = ledger.artifact_event_count_convergent(
        "How many model kits have I worked on or bought?")
    assert (answer.state, answer.value, answer.unit) == ("resolved", "5", "count")


def test_functional_device_count_requires_both_instrument_type_and_function_frame():
    graph = WordNetNounGraph(
        {"instrumentality": (1,), "watch": (2,), "hearing_aid": (3,),
         "system": (4,), "machine": (5,), "planner": (6,)},
        {1: (), 2: (1,), 3: (1,), 4: (1,), 5: (1,), 6: (1,)},
        {3: "an electronic device worn to compensate for impaired hearing"})
    documents = docs(
        "I've been wearing my Fitbit Versa 3 smartwatch non-stop.",
        "I review health metrics such as sleep and steps.",
        "I have my hearing aids and have been relying on them for months.",
        "I've been testing blood sugar with my Accu Check system three times a day.",
        "I organize my health tasks in one place.",
        "I do inhalation treatments twice a day with my nebulizer machine.",
        "I've been using my planner to organize appointments." )
    ledger = AttestedScalarLedger.build(
        documents, fact_groups={1: 1, 2: 1, 3: 2, 4: 3, 5: 3, 6: 4, 7: 3})
    answer = ledger.functional_device_count_convergent(
        "How many health-related devices do I use in a day?", graph)
    assert (answer.state, answer.value, answer.unit) == ("resolved", "4", "count")


def test_cumulative_acquisition_uses_monotone_latest_state_and_symbolic_source():
    ledger = AttestedScalarLedger.build(docs(
        "I've already bought three tops from H&M.",
        "I've already got five tops from H&M so far.",
        "I've already got nine tops from another store so far."))
    answer = ledger.acquisition_count_convergent(
        "How many tops have I bought from H&M so far?")
    assert (answer.state, answer.value, answer.unit) == ("resolved", "5", "count")
    regressed = AttestedScalarLedger.build(docs(
        "I've already bought five tops from H&M.",
        "I've already bought three tops from H&M so far."))
    assert regressed.acquisition_count_convergent(
        "How many tops have I bought from H&M so far?").state == "contested"


def test_assistant_utterance_projection_preserves_role_and_follows_causal_successor():
    documents = (
        RouteDocument(1, "[user] What budget did we allocate to influencer marketing?",
                      1, "s", 1, "source:1", role="user"),
        RouteDocument(2, "[assistant] Budget:\n\n* Influencer marketing: $2,000",
                      1, "s", 1, "source:2", role="assistant"),
        RouteDocument(3, "[user] What are some unrelated campaign ideas?",
                      1, "s", 1, "source:3", role="user"),
        RouteDocument(4, "[assistant] Try a newsletter and community event.",
                      1, "s", 1, "source:4", role="assistant"),
    )
    ledger = AttestedScalarLedger.build(
        documents, fact_groups={1: 1, 2: 1, 3: 2, 4: 2})
    answer = ledger.assistant_utterance_projection_convergent(
        "Looking back at our previous chat, can you remind me how much was allocated for "
        "influencer marketing?")
    assert (answer.state, answer.value, answer.unit) == ("resolved", "$2,000", "text")
    assert answer.worlds[0].fact_ids == (2,)


def test_assistant_utterance_projection_can_use_ordinal_list_item():
    documents = (
        RouteDocument(1, "[user] Which five bottles should I buy for a cocktail bar?",
                      1, "s", 1, "source:1", role="user"),
        RouteDocument(2, "[assistant] Bottles:\n1. Gin\n2. Vermouth\n3. Campari\n"
                         "4. Bitters\n5. Absinthe",
                      1, "s", 1, "source:2", role="assistant"),
    )
    ledger = AttestedScalarLedger.build(documents, fact_groups={1: 1, 2: 1})
    answer = ledger.assistant_utterance_projection_convergent(
        "Can you remind me what the fifth bottle was?")
    assert (answer.state, answer.value) == ("resolved", "Absinthe")


def test_lookup_requires_entity_or_relation_binding_and_preserves_exact_surface():
    ledger = AttestedScalarLedger.build(docs(
        "I practice guitar for 30 minutes daily.",
        "I practice violin but never stated a duration."))
    guitar = ledger.lookup_convergent(
        "How much time do I practice guitar?")
    violin = ledger.lookup_convergent(
        "How much time do I practice violin?")
    assert (guitar.state, guitar.value) == ("resolved", "30 minutes")
    assert violin.state == "abstain"


def test_lookup_supports_clock_and_data_rate_as_typed_surfaces():
    ledger = AttestedScalarLedger.build(docs(
        "I usually get home from work at 6:30 pm.",
        "My new internet plan has a speed of 500 Mbps."))
    clock = ledger.lookup_convergent("What time do I usually get home from work?")
    speed = ledger.lookup_convergent("What speed is my new internet plan?")
    assert (clock.state, clock.value) == ("resolved", "6:30 pm.")
    assert (speed.state, speed.value) == ("resolved", "500 Mbps")


def test_compact_scalar_roundtrip_verifies_sources_and_fails_closed():
    ledger = AttestedScalarLedger.build(docs(
        "My first drive took 4 hours.", "My second drive took 6 hours."))
    answer = ledger.sum_convergent("How many hours did all the drives take in total?")
    blob = compact_scalar_answer(answer, ledger)
    value, unit, citations = open_compact_scalar_answer(blob, ledger)
    assert (value, unit) == ("10", "hour")
    assert len(citations) == 2
    damaged = bytearray(blob)
    damaged[10] ^= 1
    try:
        open_compact_scalar_answer(bytes(damaged), ledger)
    except ValueError as error:
        assert "integrity" in str(error)
    else:
        raise AssertionError("corrupt compact proof must fail closed")


def test_relative_offset_cannot_answer_duration_lookup():
    ledger = AttestedScalarLedger.build(docs(
        "I picked up the keys a week before the move.",
        "The move took 5 hours."))
    answer = ledger.lookup_convergent("How long did the move take?")
    assert (answer.state, answer.value) == ("resolved", "5 hours")


def test_per_item_rate_cannot_masquerade_as_total_sum():
    ledger = AttestedScalarLedger.build(docs(
        "I sold 20 plants for $7.50 each."))
    answer = ledger.sum_convergent(
        "What is the total amount of money from all the plant sales?")
    assert answer.state == "abstain"


def test_repeated_orbit_can_close_through_shared_attested_entities():
    ledger = AttestedScalarLedger.build(docs(
        "The Outer Banks road trip took 4 hours.",
        "My drive to Outer Banks in North Carolina took 4 hours.",
        "The drive to Washington took 6 hours."))
    answer = ledger.sum_convergent(
        "How many hours did all the Outer Banks and Washington drives take in total?")
    assert (answer.state, answer.value) == ("resolved", "10")


def test_attested_acronym_expansion_collapses_repeat_without_alias_dictionary():
    ledger = AttestedScalarLedger.build(docs(
        "I watched the Marvel Cinematic Universe in two weeks.",
        "I watched the MCU in two weeks.",
        "I watched Star Wars in a week and a half."))
    answer = ledger.sum_convergent(
        "How many weeks did all the Marvel Cinematic Universe and Star Wars movies take?")
    assert (answer.state, answer.value) == ("resolved", "3.5")


def test_lookup_preserves_epistemic_qualifier_but_sum_will_not_erase_it():
    ledger = AttestedScalarLedger.build(docs("I waited for the decision for over a year."))
    lookup = ledger.lookup_convergent("How long did I wait for the decision?")
    summed = ledger.sum_convergent("How many years did all the waits take in total?")
    assert (lookup.state, lookup.value) == ("resolved", "over a year")
    assert summed.state == "abstain"


def test_product_sum_executes_same_sentence_rate_and_lump_sums():
    ledger = AttestedScalarLedger.build(docs(
        "I sold 20 potted plant products at the market for $7.50 each.",
        "I sold 15 jar products at the market, earning $225.",
        "I sold 12 herb products at the market, earning a total of $120."))
    answer = ledger.product_sum_convergent(
        "What is the total amount of money I earned selling all products at the markets?")
    assert (answer.state, answer.value, answer.unit) == ("resolved", "495", "USD")
    assert answer.worlds[0].fact_ids == (1, 2, 3)


def test_product_sum_rejects_cross_sentence_factor_and_planned_sales():
    cross = AttestedScalarLedger.build(docs(
        "I sold 20 plants.", "The price was $7.50 each."))
    assert cross.product_sum_convergent(
        "What is the total money from all sales?").state == "abstain"
    planned = AttestedScalarLedger.build(docs(
        "I plan to sell 20 plants for $7.50 each."))
    assert planned.product_sum_convergent(
        "What is the total money from all sales?").state == "abstain"


def test_calendar_may_is_not_modal_may():
    ledger = AttestedScalarLedger.build(docs(
        "I sold the products at the market on May 29th, earning $225."))
    answer = ledger.product_sum_convergent(
        "What is the total money earned from all products at the market?")
    assert (answer.state, answer.value) == ("resolved", "225")


def test_coordinated_count_propagates_type_only_inside_witnessed_list():
    ledger = AttestedScalarLedger.build(docs(
        "The shelf contains 3 red books, two blue books and a green book."))
    answer = ledger.coordinated_count_convergent(
        "How many books are there in total on the shelf?")
    assert (answer.state, answer.value, answer.unit) == ("resolved", "6", "count")


def test_coordinated_count_abstains_without_type_anchor_or_cardinality():
    no_type = AttestedScalarLedger.build(docs(
        "The shelf contains 3 red objects and two blue objects."))
    assert no_type.coordinated_count_convergent(
        "How many books are there in total?").state == "abstain"
    debt = AttestedScalarLedger.build(docs(
        "The shelf contains 3 red books and several blue books."))
    assert debt.coordinated_count_convergent(
        "How many books are there in total?").state == "abstain"


def test_coordinated_count_handles_oxford_comma_and_named_apposition():
    ledger = AttestedScalarLedger.build(docs(
        "The first tank has 10 neon tetras, 5 honey gouramis, and a pleco catfish.",
        "The second tank has my betta fish, Bubbles."))
    answer = ledger.coordinated_count_convergent(
        "How many fish are there in total in both tanks?")
    assert (answer.state, answer.value) == ("resolved", "17")


def test_acquisition_count_closes_coordinated_objects_and_repeat_orbits():
    ledger = AttestedScalarLedger.build(docs(
        "I bought the peace lily and a succulent plant two weeks ago.",
        "My snake plant, which I got last month, needs a larger pot.",
        "I am glad I got that same succulent plant two weeks ago."))
    answer = ledger.acquisition_count_convergent(
        "How many plants did I acquire in the last month?")
    assert (answer.state, answer.value) == ("resolved", "3")


def test_acquisition_count_resolves_local_pronoun_and_rejects_mixed_operator():
    ledger = AttestedScalarLedger.build(docs(
        "My engagement ring is a jewelry piece. I got it a month ago."))
    answer = ledger.acquisition_count_convergent(
        "How many pieces of jewelry did I acquire in the last two months?")
    assert (answer.state, answer.value) == ("resolved", "1")
    assert ledger.acquisition_count_convergent(
        "How many kits have I worked on or bought?").state == "unsupported"


def test_typed_lookup_fallback_accepts_paraphrase_but_rejects_slot_collision():
    positive = AttestedScalarLedger.build(docs(
        "My daily commute takes 45 minutes each way."))
    assert positive.lookup_convergent(
        "How long is my daily commute to work?").value == "45 minutes each way"
    negative = AttestedScalarLedger.build(docs(
        "I have practiced guitar for 30 minutes daily."))
    assert negative.lookup_convergent(
        "How much time do I dedicate to practicing violin every day?").state == "abstain"


def test_typed_lookup_fallback_conserves_speed_and_clock_dimensions():
    ledger = AttestedScalarLedger.build(docs(
        "My internet speed was upgraded to 500 Mbps three weeks ago.",
        "I usually get home from work at 6:30 pm on weekdays."))
    assert ledger.lookup_convergent("What speed is my new internet plan?").value == "500 Mbps"
    assert ledger.lookup_convergent(
        "What time do I usually get home from work on weeknights?").value == "6:30 pm"


def test_typed_lookup_fallback_rejects_derived_and_coordinated_queries():
    ledger = AttestedScalarLedger.build(docs(
        "I paid $50 for the painting ten years ago.",
        "I attended a two-day workshop and a one-day lecture."))
    assert ledger.lookup_convergent(
        "How much is the painting worth in terms of what I paid?").state == "unsupported"
    assert ledger.lookup_convergent(
        "How many days did I spend at workshops and lectures?").state == "unsupported"


def test_relative_marker_across_sentence_boundary_does_not_taint_measurement():
    ledger = AttestedScalarLedger.build(docs(
        "I visited Japan a few months ago. I spent two weeks traveling there."))
    answer = ledger.lookup_convergent("How long was I in Japan for?")
    assert (answer.state, answer.value) == ("resolved", "two weeks")


def test_textual_projection_resolves_location_and_copular_attribute():
    location = AttestedScalarLedger.build(docs(
        "I redeemed the coffee coupon at Target yesterday."))
    assert location.textual_projection_convergent(
        "Where did I redeem the coffee coupon?").value == "Target"
    attribute = AttestedScalarLedger.build(docs(
        "My dog is a Golden Retriever."))
    assert attribute.textual_projection_convergent(
        "What breed is my dog?").value == "Golden Retriever"


def test_textual_projection_contested_values_never_rank_to_truth():
    ledger = AttestedScalarLedger.build(docs(
        "I redeemed the coupon at Target.", "I redeemed the coupon at Walmart."))
    assert ledger.textual_projection_convergent(
        "Where did I redeem the coupon?").state == "contested"


def test_explicit_absence_uses_same_slot_contrast_not_retrieval_miss():
    ledger = AttestedScalarLedger.build(docs(
        "I practice guitar for 30 minutes daily."))
    missing = ledger.explicit_absence_convergent(
        "How much time do I practice violin every day?")
    present = ledger.explicit_absence_convergent(
        "How much time do I practice guitar every day?")
    assert (missing.state, missing.value) == (
        "resolved", "You did not mention this information. Related attested information: "
        "I practice guitar for 30 minutes daily.")
    assert present.state == "unsupported"


def test_explicit_absence_accepts_named_entity_duration_contrast():
    ledger = AttestedScalarLedger.build(docs(
        "I was in Japan recently. I spent two weeks traveling there."))
    answer = ledger.explicit_absence_convergent("How long was I in Korea for?")
    assert answer.state == "resolved"
    assert answer.worlds[0].fact_ids == (1,)
    assert "Japan" in answer.value


def test_explicit_absence_never_treats_auxiliary_take_as_a_closed_slot():
    ledger = AttestedScalarLedger.build(docs(
        "I took on more responsibilities three months ago."))
    answer = ledger.explicit_absence_convergent(
        "How long did it take me to assemble the IKEA bookshelf?")
    assert answer.state == "unsupported"


def test_integrated_executor_admits_a_unique_operator_world():
    ledger = AttestedScalarLedger.build(docs(
        "I sold 20 potted plant products at the market for $7.50 each.",
        "I sold 15 jar products at the market, earning $225.",
        "I sold 12 herb products at the market, earning a total of $120."))
    answer = ledger.answer_convergent(
        "What is the total amount of money I earned selling all products at the markets?")
    assert (answer.state, answer.value, answer.unit) == ("resolved", "495", "USD")


def test_compact_codec_roundtrips_an_attested_absence():
    ledger = AttestedScalarLedger.build(docs(
        "I practice guitar for 30 minutes daily."))
    answer = ledger.answer_convergent("How much time do I practice violin every day?")
    blob = compact_scalar_answer(answer, ledger)
    value, unit, citations = open_compact_scalar_answer(blob, ledger)
    assert (value, unit, citations) == (
        "You did not mention this information. Related attested information: "
        "I practice guitar for 30 minutes daily.", "absence", ((1, 0, 39),))


def test_relative_value_projects_the_relation_without_inventing_a_price():
    ledger = AttestedScalarLedger.build(docs(
        "I realized that my flea-market find is worth triple what I paid for it."))
    answer = ledger.answer_convergent(
        "How much is the painting worth in terms of the amount I paid for it?")
    assert (answer.state, answer.value, answer.unit) == (
        "resolved", "The painting is worth triple what I paid for it", "relative_value")


def test_relative_value_disagreement_is_contested():
    ledger = AttestedScalarLedger.build(docs(
        "The painting is worth double what I paid for it.",
        "The painting is worth triple what I paid for it."))
    assert ledger.relative_value_convergent(
        "How much is the painting worth in terms of the amount I paid for it?").state == "contested"


def test_currency_result_type_dominates_a_temporal_window_unit():
    ledger = AttestedScalarLedger.build(docs(
        "I recently bought a luxury evening gown for $800.",
        "I got a luxury designer handbag for $1,200.",
        "I bought budget-friendly shirts for $20. But my recent leather boots from a luxury "
        "designer cost $500."))
    answer = ledger.answer_convergent(
        "What is the total amount I spent on luxury items in the past few months?")
    assert (answer.state, answer.value, answer.unit) == ("resolved", "2500", "USD")


def test_classified_money_sum_collapses_a_repeated_temporally_identical_event():
    ledger = AttestedScalarLedger.build(docs(
        "I attended a writing workshop at a literary festival in November. I paid $200 to attend.",
        "That November event was a writing workshop at the literary festival. I paid $200 to attend.",
        "I attended a digital marketing workshop in March. I paid $500 to attend."))
    answer = ledger.answer_convergent(
        "How much total money did I spend on attending workshops in the last four months?")
    assert (answer.state, answer.value, answer.unit) == ("resolved", "700", "USD")


def test_integration_contract_overrides_only_with_a_reopenable_proof():
    ledger = AttestedScalarLedger.build(docs(
        "My first trip lasted 3 days.", "My second trip lasted 5 days."))
    integrated = integrate_with_deterministic_fallback(
        ledger, "How many days did all the trips take in total?", "old fallback")
    assert (integrated.answer_text, integrated.authority) == (
        "8 days", "proof_convergent")
    assert integrated.proof_blob is not None
    assert open_compact_scalar_answer(integrated.proof_blob, ledger)[:2] == ("8", "day")


def test_integration_contract_preserves_fallback_on_abstain_or_contest():
    absent = AttestedScalarLedger.build(docs("I enjoy reading novels."))
    result = integrate_with_deterministic_fallback(
        absent, "How many days did all trips take in total?", "fallback answer")
    assert (result.answer_text, result.authority, result.proof_blob) == (
        "fallback answer", "deterministic_fallback", None)
    contested = AttestedScalarLedger.build(docs(
        "The painting is worth double what I paid for it.",
        "The painting is worth triple what I paid for it."))
    result = integrate_with_deterministic_fallback(
        contested, "How much is the painting worth in terms of the amount I paid for it?",
        "fallback answer")
    assert (result.answer_text, result.authority) == (
        "fallback answer", "deterministic_fallback")


def test_deterministic_renderer_preserves_surface_units_and_formats_currency():
    ledger = AttestedScalarLedger.build(docs("I spent $800 on the bag."))
    lookup = ledger.lookup_convergent("How much money did I spend on the bag?")
    assert render_convergent_answer(lookup) == "$800"


def test_d145_integration_runner_skips_fallback_on_proven_answer(tmp_path):
    row = {
        "question": "How many days did all trips take in total?",
        "haystack_session_ids": ["s"], "haystack_dates": ["2026-01-01"],
        "haystack_sessions": [[
            {"role": "user", "content": "My first trip lasted 3 days."},
            {"role": "user", "content": "My second trip lasted 5 days."},
        ]],
    }
    with patch(
            "lab.runners.run_d145_longmemeval_composer_judge_pilot._compose_one",
            side_effect=AssertionError("fallback must not run")):
        result = _compose_one_integrated(0, row, (), str(tmp_path))
    assert (result["authority"], result["answer_text"], result["proof_bytes"] > 0) == (
        "proof_convergent", "8 days", True)


def test_d145_integration_runner_preserves_fallback_on_proof_miss(tmp_path):
    row = {
        "question": "What color was the bicycle?",
        "haystack_session_ids": ["s"], "haystack_dates": ["2026-01-01"],
        "haystack_sessions": [[{"role": "user", "content": "The bicycle was blue."}]],
    }
    fallback = {"ordinal": 0, "state": "resolved", "answer_text": "blue"}
    with patch("lab.runners.run_d145_longmemeval_composer_judge_pilot._compose_one",
               return_value=fallback):
        result = _compose_one_integrated(0, row, (), str(tmp_path))
    assert (result["authority"], result["answer_text"], result["proof_bytes"]) == (
        "deterministic_fallback", "blue", 0)


def test_d145_paired_runner_reuses_exact_plain_bytes_on_proof_miss(tmp_path):
    row = {
        "question": "What color was the bicycle?",
        "haystack_session_ids": ["s"], "haystack_dates": ["2026-01-01"],
        "haystack_sessions": [[{"role": "user", "content": "The bicycle was blue."}]],
    }
    fallback = {"ordinal": 0, "state": "resolved", "answer_text": "blue\nwith exact bytes"}
    with patch("lab.runners.run_d145_longmemeval_composer_judge_pilot._compose_one",
               return_value=fallback) as compose:
        result = _compose_one_paired(0, row, (), str(tmp_path))
    compose.assert_called_once()
    assert result["plain"]["answer_text"].encode() == \
        result["integrated"]["answer_text"].encode()
    assert result["integrated"]["authority"] == "deterministic_fallback"


def test_d145_paired_runner_keeps_current_plain_arm_even_when_proof_resolves(tmp_path):
    row = {
        "question": "How many days did all trips take in total?",
        "haystack_session_ids": ["s"], "haystack_dates": ["2026-01-01"],
        "haystack_sessions": [[
            {"role": "user", "content": "My first trip lasted 3 days."},
            {"role": "user", "content": "My second trip lasted 5 days."},
        ]],
    }
    fallback = {"ordinal": 0, "state": "resolved", "answer_text": "large evidence packet"}
    with patch("lab.runners.run_d145_longmemeval_composer_judge_pilot._compose_one",
               return_value=fallback):
        result = _compose_one_paired(0, row, (), str(tmp_path))
    assert result["plain"]["answer_text"] == "large evidence packet"
    assert result["integrated"]["answer_text"] == "8 days"
    assert result["integrated"]["authority"] == "proof_convergent"


def test_activity_duration_sum_preserves_uncertainty_and_collapses_repeat_orbits():
    ledger = AttestedScalarLedger.build(docs(
        "I spent around 70 hours playing Odyssey.",
        "The indie game Hyper Light Drifter took me 5 hours to finish.",
        "I completed The Last of Us on hard difficulty in 30 hours.",
        "I spent around 30 hours playing The Last of Us on hard difficulty.",
        "The game Celeste took me 10 hours to complete.",
        "I finished The Last of Us on normal difficulty in 25 hours."))
    answer = ledger.answer_convergent("How many hours have I spent playing games in total?")
    assert (answer.state, answer.value, answer.unit) == (
        "resolved", "around 140 hours", "hour")


def test_derived_money_questions_never_degrade_to_scalar_lookup():
    ledger = AttestedScalarLedger.build(docs(
        "I spent $75 at the store and the card gives one percent cashback.",
        "The handbag was originally $500 and the sale price was $200.",
        "The pre-approval was $325,000 and the final price was $300,000."))
    assert ledger.lookup_convergent(
        "How much cashback did I earn at the store?").state == "unsupported"
    assert ledger.lookup_convergent(
        "How much did I save on the handbag?").state == "unsupported"
    assert ledger.lookup_convergent(
        "How much more was the pre-approval than the final price?").state == "unsupported"


def test_acquisition_count_cannot_answer_a_duration_question():
    ledger = AttestedScalarLedger.build(docs("I bought a tablet case yesterday."))
    assert ledger.acquisition_count_convergent(
        "How many days did the tablet case take to arrive after I bought it?").state == "unsupported"


def test_plain_sum_cannot_answer_a_from_to_interval():
    ledger = AttestedScalarLedger.build(docs(
        "High school lasted four years.", "My degree lasted four years."))
    assert ledger.sum_convergent(
        "How many years in total from high school to completion of my degree?").state == "unsupported"


def test_absence_requires_a_concrete_contrast_action():
    ledger = AttestedScalarLedger.build(docs("I worked abroad for two years."))
    assert ledger.explicit_absence_convergent(
        "How long have I been working in my current role?").state == "unsupported"


def test_binary_difference_binds_independent_roles_and_conserves_currency():
    ledger = AttestedScalarLedger.build(docs(
        "The lender pre-approved me for $350,000.",
        "The final sale price of the house was $325,000."))
    answer = ledger.answer_convergent(
        "How much more was the pre-approval amount than the final sale price of the house?")
    assert (answer.state, answer.value, answer.unit) == ("resolved", "25000", "USD")


def test_savings_difference_uses_original_and_paid_roles():
    ledger = AttestedScalarLedger.build(docs(
        "The designer handbag was originally $500.",
        "I got the designer handbag for $200 at the outlet."))
    answer = ledger.answer_convergent("How much did I save on the designer handbag?")
    assert (answer.state, answer.value, answer.unit) == ("resolved", "300", "USD")


def test_difference_abstains_when_either_role_is_ambiguous():
    ledger = AttestedScalarLedger.build(docs(
        "The original price was $500 or perhaps $450.", "I paid $200."))
    assert ledger.difference_convergent(
        "How much did I save on the handbag?").state == "abstain"


def test_cashback_product_binds_rate_and_purchase_to_the_same_merchant():
    ledger = AttestedScalarLedger.build(docs(
        "My SaveMart membership earns 1% cashback on all purchases.",
        "I spent $75 on groceries at SaveMart last Thursday."))
    answer = ledger.answer_convergent(
        "How much cashback did I earn at SaveMart last Thursday?")
    assert (answer.state, answer.value, answer.unit) == ("resolved", "0.75", "USD")


def test_cashback_product_abstains_with_multiple_unversioned_rates():
    ledger = AttestedScalarLedger.build(docs(
        "The store offers 1% cashback.", "The store also advertises 2% cashback.",
        "I spent $75 at the store."))
    assert ledger.cashback_convergent(
        "How much cashback did I earn at the store?").state == "abstain"


def test_current_role_duration_subtracts_pre_promotion_tenure():
    ledger = AttestedScalarLedger.build(docs(
        "I started as a coordinator and moved into my current specialist role after 2 years and 4 months.",
        "I now have 3 years and 9 months of experience in the company."))
    answer = ledger.answer_convergent("How long have I been working in my current role?")
    assert (answer.state, answer.value, answer.unit) == (
        "resolved", "1 year and 5 months", "month")


def test_current_role_duration_abstains_without_both_tenure_roles():
    ledger = AttestedScalarLedger.build(docs(
        "I have 3 years and 9 months of experience in the company."))
    assert ledger.current_role_duration_convergent(
        "How long have I been working in my current role?").state == "abstain"


def test_average_age_executes_only_over_the_closed_query_kinship_fiber():
    ledger = AttestedScalarLedger.build(docs(
        "I just turned 32.",
        "My parents are getting older: my mom is 55 and my dad is 58.",
        "My grandma is 75 and my grandpa is 78."))
    answer = ledger.answer_convergent(
        "What is the average age of me, my parents, and my grandparents?")
    assert (answer.state, answer.value, answer.unit) == ("resolved", "59.6", "age")


def test_average_age_abstains_when_a_required_relative_is_missing():
    ledger = AttestedScalarLedger.build(docs(
        "I am 32. My mother is 55 and my father is 58. My grandmother is 75."))
    assert ledger.average_age_convergent(
        "What is the average age of me, my parents, and my grandparents?").state == "abstain"


def test_corpus_nonmembership_proves_missing_distinctive_identifier_compactly():
    ledger = AttestedScalarLedger.build(docs(
        "I bought a laptop backpack and it arrived five days later."))
    question = "How many days did it take for my iPad case to arrive after I bought it?"
    answer = ledger.answer_convergent(question)
    assert (answer.state, answer.value, answer.unit) == (
        "resolved", "You did not mention this information.", "corpus_absence")
    blob = compact_scalar_answer(answer, ledger)
    assert open_compact_scalar_answer(blob, ledger) == (
        "You did not mention this information.", "corpus_absence", ())
    different = AttestedScalarLedger.build(docs(
        "I bought an iPad case and it arrived five days later."))
    try:
        open_compact_scalar_answer(blob, different)
    except ValueError as error:
        assert "corpus authority" in str(error)
    else:
        raise AssertionError("absence proof must be bound to the complete authority corpus")


def test_corpus_nonmembership_refuses_when_identifier_occurs_in_authority():
    ledger = AttestedScalarLedger.build(docs(
        "I bought an iPad case but did not record its arrival date."))
    assert ledger.corpus_nonmembership_convergent(
        "How many days did it take for my iPad case to arrive after I bought it?").state == "unsupported"


def test_timeline_interval_subtracts_query_bound_calendar_endpoints():
    ledger = AttestedScalarLedger.build(docs(
        "I attended Arcadia High School from 2010 to 2014.",
        "I graduated with my Bachelor's degree in 2020."))
    answer = ledger.answer_convergent(
        "How many years in total did I spend in formal education from high school to the "
        "completion of my Bachelor's degree?")
    assert (answer.state, answer.value, answer.unit) == ("resolved", "10", "year")


def test_timeline_interval_abstains_when_an_endpoint_has_two_values():
    ledger = AttestedScalarLedger.build(docs(
        "I attended high school from 2010 to 2014.",
        "Another note says I attended high school from 2011 to 2014.",
        "I graduated with my Bachelor's degree in 2020."))
    assert ledger.timeline_interval_convergent(
        "How many years in total did I spend in education from high school to completion of my "
        "Bachelor's degree?").state == "abstain"


def test_owned_set_uses_versioned_hypernyms_and_collapses_generic_repeats():
    graph = WordNetNounGraph(
        {"musical_instrument": (1,), "electric_guitar": (2,), "acoustic_guitar": (3,),
         "drum": (4,), "piano": (5,), "ukulele": (6,)},
        {1: (), 2: (1,), 3: (1,), 4: (1,), 5: (1,), 6: (1,)})
    ledger = AttestedScalarLedger.build(docs(
        "I've had my Fender Stratocaster electric guitar for years.",
        "My acoustic guitar needs maintenance.",
        "I've had my Yamaha acoustic guitar for eight years.",
        "I'm thinking about getting a new ukulele.",
        "I'm thinking of selling my Pearl Export drum set.",
        "My piano, a Korg, has sticky keys."))
    answer = ledger.owned_typed_set_convergent(
        "How many musical instruments do I currently own?", graph)
    assert (answer.state, answer.value, answer.unit) == ("resolved", "4", "count")


def test_owned_set_is_unavailable_without_a_versioned_ontology(monkeypatch):
    monkeypatch.delenv("HORIZON_WORDNET_DIR", raising=False)
    ledger = AttestedScalarLedger.build(docs("I own a guitar."))
    assert ledger.owned_typed_set_convergent(
        "How many musical instruments do I currently own?").state == "unsupported"


def _link_word(source, index, surface, grammar_word):
    start = source.index(surface) if surface else (0 if index == 0 else len(source))
    return ConservedLinkWord(index, surface, grammar_word, start, start + len(surface))


def test_link_graph_projects_active_and_passive_to_the_same_witnessed_roles():
    active_source = "Aldren admired Fiora."
    active = ConservedLinkGraph(active_source, (
        _link_word(active_source, 0, "", "LEFT-WALL"),
        _link_word(active_source, 1, "Aldren", "Aldren[!<CAPITALIZED-WORDS>]"),
        _link_word(active_source, 2, "admired", "admired.v-d"),
        _link_word(active_source, 3, "Fiora", "Fiora[!<CAPITALIZED-WORDS>]"),
        _link_word(active_source, 4, ".", "."),
        _link_word(active_source, 5, "", "RIGHT-WALL"),
    ), (
        ConservedLinkEdge(0, "WV", 2), ConservedLinkEdge(1, "Ss*s", 2),
        ConservedLinkEdge(2, "Os", 3),
    ), 0)
    passive_source = "Fiora was admired by Aldren."
    passive = ConservedLinkGraph(passive_source, (
        _link_word(passive_source, 0, "", "LEFT-WALL"),
        _link_word(passive_source, 1, "Fiora", "Fiora[!<CAPITALIZED-WORDS>]"),
        _link_word(passive_source, 2, "was", "was.v-d"),
        _link_word(passive_source, 3, "admired", "admired.v-d"),
        _link_word(passive_source, 4, "by", "by"),
        _link_word(passive_source, 5, "Aldren", "Aldren[!<CAPITALIZED-WORDS>]"),
        _link_word(passive_source, 6, ".", "."),
        _link_word(passive_source, 7, "", "RIGHT-WALL"),
    ), (
        ConservedLinkEdge(0, "WV", 3), ConservedLinkEdge(1, "Ss*s", 2),
        ConservedLinkEdge(2, "Pv", 3), ConservedLinkEdge(3, "MVp", 4),
        ConservedLinkEdge(4, "Js", 5),
    ), 0)
    left = project_conserved_binary_event(active)
    right = project_conserved_binary_event(passive)
    assert active.verify() and passive.verify()
    assert (left.predicate, left.argument_1, left.argument_2) == (
        right.predicate, right.argument_1, right.argument_2) == (
        "admired", "aldren", "fiora")
    assert (left.voice, right.voice) == ("active", "passive")
    active_d45 = link_graph_to_authorized_hypergraph(
        active, source_id="active", analysis_id="a1", alternative_set="pair")
    passive_d45 = link_graph_to_authorized_hypergraph(
        passive, source_id="passive", analysis_id="a1", alternative_set="pair")
    assert active_d45.semantic_signature == passive_d45.semantic_signature


def test_link_forest_converges_across_duplicate_roles_and_rejects_role_swap():
    source = "Aldren admired Fiora."
    words = (
        _link_word(source, 0, "", "LEFT-WALL"),
        _link_word(source, 1, "Aldren", "Aldren[!<CAPITALIZED-WORDS>]"),
        _link_word(source, 2, "admired", "admired.v-d"),
        _link_word(source, 3, "Fiora", "Fiora[!<CAPITALIZED-WORDS>]"),
        _link_word(source, 4, ".", "."),
        _link_word(source, 5, "", "RIGHT-WALL"),
    )
    canonical = ConservedLinkGraph(source, words, (
        ConservedLinkEdge(0, "WV", 2), ConservedLinkEdge(1, "Ss*s", 2),
        ConservedLinkEdge(2, "Os", 3)), 0)
    no_wall_head = ConservedLinkGraph(source, words, (
        ConservedLinkEdge(1, "Ss*s", 2), ConservedLinkEdge(2, "Os", 3)), 0)
    swapped = ConservedLinkGraph(source, words, (
        ConservedLinkEdge(0, "WV", 2), ConservedLinkEdge(1, "Os", 2),
        ConservedLinkEdge(2, "Ss*s", 3)), 0)
    stable = converged_binary_event(ConservedLinkForest(
        source, (canonical, no_wall_head), 2, False, False))
    assert (stable.argument_1, stable.argument_2) == ("aldren", "fiora")
    assert converged_binary_event(ConservedLinkForest(
        source, (canonical, swapped), 2, False, False)) is None
    assert converged_binary_event(ConservedLinkForest(
        source, (canonical,), 20, True, False)) is None
    assert converged_binary_event(ConservedLinkForest(
        source, (canonical,), 1, False, True)) is None

    query_source = "Who admired Fiora?"
    query_words = (
        _link_word(query_source, 0, "", "LEFT-WALL"),
        _link_word(query_source, 1, "Who", "who"),
        _link_word(query_source, 2, "admired", "admired.v-d"),
        _link_word(query_source, 3, "Fiora", "Fiora"),
        _link_word(query_source, 4, "?", "?"),
        _link_word(query_source, 5, "", "RIGHT-WALL"),
    )
    query = ConservedLinkGraph(query_source, query_words, (
        ConservedLinkEdge(0, "WV", 2), ConservedLinkEdge(1, "Ss*w", 2),
        ConservedLinkEdge(2, "Os", 3)), 0)
    relation = resolve_conserved_binary_relation(
        ConservedLinkForest(source, (canonical, no_wall_head), 2, False, False),
        ConservedLinkForest(query_source, (query,), 1, False, False))
    assert relation == ("aldren", (0, 6))
    sigma_relation = resolve_binary_relation_via_sigma(
        ConservedLinkForest(source, (canonical, no_wall_head), 2, False, False),
        ConservedLinkForest(query_source, (query,), 1, False, False), source_id="source:1")
    assert sigma_relation[:2] == relation and sigma_relation[2]
    ledger = AttestedScalarLedger.build(docs(source))
    answer = conserved_span_answer(1, relation[0], relation[1],
                                   reason="link_binary_role_binding")
    blob = compact_scalar_answer(answer, ledger)
    assert open_compact_scalar_answer(blob, ledger) == ("aldren", "text", ((1, 0, 6),))


def test_sat_projection_compiles_query_roles_through_d45_sigma_and_reopens():
    source = "Aldren admired Fiora."
    words = (
        _link_word(source, 0, "", "LEFT-WALL"),
        _link_word(source, 1, "Aldren", "Aldren[!<CAPITALIZED-WORDS>]"),
        _link_word(source, 2, "admired", "admired.v-d"),
        _link_word(source, 3, "Fiora", "Fiora[!<CAPITALIZED-WORDS>]"),
        _link_word(source, 4, ".", "."),
        _link_word(source, 5, "", "RIGHT-WALL"),
    )
    graph = ConservedLinkGraph(source, words, (
        ConservedLinkEdge(0, "WV", 2), ConservedLinkEdge(1, "Ss*s", 2),
        ConservedLinkEdge(2, "Os", 3)), 0)
    question_source = "Who admired Fiora?"
    question = ConservedLinkGraph(question_source, (
        _link_word(question_source, 0, "", "LEFT-WALL"),
        _link_word(question_source, 1, "Who", "who"),
        _link_word(question_source, 2, "admired", "admired.v-d"),
        _link_word(question_source, 3, "Fiora", "Fiora"),
        _link_word(question_source, 4, "?", "?"),
        _link_word(question_source, 5, "", "RIGHT-WALL"),
    ), (
        ConservedLinkEdge(0, "WV", 2), ConservedLinkEdge(1, "Ss*w", 2),
        ConservedLinkEdge(2, "Os", 3)), 0)

    class FakeSatBridge:
        use_sat_parser = True

        @staticmethod
        def parse(value):
            assert value == source
            return ConservedLinkForest(source, (graph,), 1, True, False, 1, 1)

        @staticmethod
        def sat_projection_exists(value, requirements):
            assert value == source
            return "possible" if all(any(
                edge.left == requirement.left_word and
                edge.right == requirement.right_word and
                edge.label.startswith(requirement.label_prefix)
                for edge in graph.edges) for requirement in requirements) else "impossible"

    bridge = FakeSatBridge()
    answer = resolve_binary_relation_via_sat_sigma(
        source, ConservedLinkForest(question_source, (question,), 1, False, False),
        bridge=bridge, source_id="generated:unit")
    assert (answer.value, answer.span) == ("aldren", (0, 6))
    assert answer.admitted_fact_ids and answer.reopen(bridge)


@pytest.mark.parametrize(("question", "expected"), (
    ("Who announced this?", ("announced", "ARG1", "who", "ARG2", "this")),
    ("What did He announce?", ("announce", "ARG2", "what", "ARG1", "he")),
    ("Where did Aurelia move?", ("move", "ARG2", "where", "ARG1", "aurelia")),
))
def test_surface_binary_query_head_compiles_only_the_frozen_one_hole_grammar(question, expected):
    demand = compile_surface_binary_demand(question)
    assert (demand.predicate, demand.answer_role,
            demand.answer_type, demand.known_role, demand.known_value) == expected


def test_surface_binary_query_head_rejects_unbounded_or_noncanonical_constructions():
    assert compile_surface_binary_demand("Please tell me who announced this?") is None
    assert compile_surface_binary_demand("What did He announce in January?") is None


@pytest.mark.parametrize(("source", "question", "expected"), (
    ("That's overstating it.", "What did That overstate?", "it"),
    ("They are taking delivery.", "What did They take?", "delivery"),
    ("You would be violating the law.", "What did You violate?", "law"),
))
def test_surface_binary_head_normalizes_productive_ing_without_a_verb_lexicon(
        source, question, expected):
    demand = compile_surface_binary_demand(question)
    checks = compile_surface_binary_checks(source, demand)
    assert {check.candidate for check in checks} == {expected}
    assert all(source[slice(*check.span)].casefold() == expected for check in checks)


@pytest.mark.parametrize(("source", "question", "expected"), (
    ("You can buy me dinner.", "What did You buy?", "dinner"),
    ("I shall send you a copy.", "What did I send?", "copy"),
    ("You really got me thinking.", "What did You get?", "me"),
    ("Anne included this info.", "What did Anne include?", "info"),
    ("You will find these helpful.", "What did You find?", "these"),
    ("She makes every item fit.", "What did She make?", "item"),
    ("Transwestern will own and operate the interconnect.",
     "What did Transwestern own?", "interconnect"),
    ("You NEVER get a human.", "Who get human?", "you"),
))
def test_surface_binary_head_keeps_dative_determiner_and_modifier_readings_typed(
        source, question, expected):
    demand = compile_surface_binary_demand(question)
    checks = compile_surface_binary_checks(source, demand)
    assert {check.candidate for check in checks} == {expected}


def test_link_graph_span_tamper_fails_before_semantic_projection():
    source = "Aldren admired Fiora."
    graph = ConservedLinkGraph(source, (
        ConservedLinkWord(0, "", "LEFT-WALL", 0, 0),
        ConservedLinkWord(1, "Aldren", "Aldren", 1, 7),
    ), (), 0)
    assert not graph.verify()


def test_link_grammar_sat_projection_cannot_silently_enable_null_links():
    with pytest.raises(ValueError, match="SAT mode requires max_null=0"):
        LinkGrammarBridge("/does/not/matter", "/does/not/matter",
                          max_null=1, use_sat_parser=True)


def test_interrogative_focus_removes_address_without_losing_source_identity():
    question = ("I'm checking our earlier chat. Can you remind me what color was the "
                "scaly body of the Plesiosaur?")
    focused = focus_interrogative_clause(question)
    assert focused.parser_text == "what color was the scaly body of the Plesiosaur?"
    assert question[slice(*focused.source_span)] == focused.parser_text
    assert focused.verify()


def test_interrogative_focus_marks_the_only_synthetic_identity_frame():
    question = "Can you remind me of the website that had the free exercises?"
    focused = focus_interrogative_clause(question)
    assert focused.parser_text == "What is the website that had the free exercises?"
    assert question[slice(*focused.source_span)] == "the website that had the free exercises?"
    assert focused.rule == "assistant_of_identity_frame"


def test_link_forests_resolve_only_the_unique_witnessed_attribute_remainder():
    statement_source = "The Plesiosaur has a blue scaly body."
    statement_words = tuple(
        _link_word(statement_source, index, surface, grammar)
        for index, (surface, grammar) in enumerate((
            ("", "LEFT-WALL"), ("The", "the"),
            ("Plesiosaur", "Plesiosaur[!<CAPITALIZED-WORDS>]"), ("has", "has.v"),
            ("a", "a"), ("blue", "blue.a"), ("scaly", "scaly.a"),
            ("body", "body.n"), (".", "."), ("", "RIGHT-WALL"))))
    statement = ConservedLinkGraph(statement_source, statement_words, (
        ConservedLinkEdge(0, "WV", 3), ConservedLinkEdge(2, "Ss*s", 3),
        ConservedLinkEdge(3, "Os", 7), ConservedLinkEdge(5, "A", 7),
        ConservedLinkEdge(6, "A", 7)), 0)
    question_source = "What color was the scaly body of the Plesiosaur?"
    question_words = tuple(
        _link_word(question_source, index, surface, grammar)
        for index, (surface, grammar) in enumerate((
            ("", "LEFT-WALL"), ("What", "what"), ("color", "color.n-u"),
            ("was", "was.v-d"), ("the", "the"), ("scaly", "scaly.a"),
            ("body", "body.n"), ("of", "of"), ("Plesiosaur", "Plesiosaur"),
            ("?", "?"), ("", "RIGHT-WALL"))))
    question = ConservedLinkGraph(question_source, question_words, (
        ConservedLinkEdge(0, "WV", 3), ConservedLinkEdge(2, "Ss", 3),
        ConservedLinkEdge(3, "Ost", 6), ConservedLinkEdge(5, "A", 6),
        ConservedLinkEdge(6, "Mf", 7), ConservedLinkEdge(7, "Js", 8)), 0)
    answer = resolve_conserved_attribute(
        ConservedLinkForest(statement_source, (statement,), 1, False, False),
        ConservedLinkForest(question_source, (question,), 1, False, False))
    assert answer == ("blue", (21, 25))
    incomplete = ConservedLinkGraph(
        statement_source, statement_words,
        tuple(edge for edge in statement.edges if edge.left != 5), 0)
    assert resolve_conserved_attribute(
        ConservedLinkForest(statement_source, (statement, incomplete), 2, False, False),
        ConservedLinkForest(question_source, (question,), 1, False, False)) == (
            "blue", (21, 25))
    assert resolve_conserved_attribute(
        ConservedLinkForest(statement_source, (statement,), 100, True, False),
        ConservedLinkForest(question_source, (question,), 1, False, False)) is None
