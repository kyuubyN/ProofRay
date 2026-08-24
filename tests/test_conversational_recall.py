# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

from datetime import date

import pytest

from horizon_memory import (
    ConversationalRecallConfig, ConversationalRecallGenerator, HorizonAnswerEngine,
    QueryEnvelope, RouteDocument, RoutingIndex, compile_explicit_calendar_interval,
    expand_session_neighbors, protected_rank_merge, reciprocal_rank_fusion,
    stable_speaker_partition,
)


def _doc(fact_id: int, text: str, session: str, sequence: int, speaker: str,
         event_time: int | None = None) -> RouteDocument:
    return RouteDocument(
        fact_id, text, 1, session, 1, f"source:{fact_id}",
        sequence=sequence, event_time=event_time, speaker=speaker,
    )


def test_rrf60_is_deterministic_deduplicated_and_uses_stable_ties():
    assert reciprocal_rank_fusion((3, 1, 2), (2, 1, 4)) == (2, 1, 3, 4)
    assert reciprocal_rank_fusion((3, 1, 2), (2, 1, 4)) == reciprocal_rank_fusion(
        (3, 1, 2), (2, 1, 4))
    with pytest.raises(ValueError):
        reciprocal_rank_fusion((1, 1), (2,))


def test_protected_merge_conserves_primary_and_reserved_endpoints():
    assert protected_rank_merge(
        tuple(range(1, 11)), (9, 8, 20, 21), quota=8, reserve=2,
    )[:8] == (1, 2, 3, 4, 5, 6, 9, 8)
    # A duplicate reserve cannot consume the protected capacity.
    assert protected_rank_merge((1, 2, 3), (1, 2, 4), quota=3, reserve=1)[:3] == (1, 2, 4)


def test_speaker_partition_uses_metadata_not_source_or_document_text():
    documents = (
        _doc(1, "Bob wrote about a bicycle", "s1", 1, "Alice"),
        _doc(2, "Unrelated text", "s2", 2, "Bob"),
        _doc(3, "Alice wrote about a bicycle", "s3", 3, "Carol"),
    )
    assert stable_speaker_partition((1, 2, 3), "What did Bob buy?", documents) == (2, 1, 3)
    assert stable_speaker_partition((1, 2, 3), "What was purchased?", documents) == (1, 2, 3)


def test_neighbors_are_same_session_only_and_use_adjacency_not_numeric_sequence():
    documents = (
        _doc(1, "one", "a", 10, "A"),
        _doc(2, "two", "a", 40, "B"),
        _doc(3, "three", "a", 90, "A"),
        _doc(4, "other session", "b", 40, "B"),
        _doc(5, "refill", "b", 100, "A"),
    )
    result = expand_session_neighbors(
        (2, 5, 1, 3, 4), documents, seed_width=1, radius=2, max_results=5)
    assert result[:3] == (2, 1, 3)
    assert result == (2, 1, 3, 5, 4)


def test_calendar_compiler_fails_closed_on_ambiguity_and_relative_history():
    may = date(2025, 5, 12).toordinal()
    interval = compile_explicit_calendar_interval("What happened in May?", (may,))
    assert interval is not None
    assert interval.precision == "month"
    assert interval.start_day == date(2025, 5, 1).toordinal()
    assert compile_explicit_calendar_interval(
        "What happened in May?", (may, date(2024, 5, 1).toordinal())) is None
    assert compile_explicit_calendar_interval("What happened since May 2025?", (may,)) is None
    assert compile_explicit_calendar_interval(
        "What happened on May 2, 2025 and June 3, 2025?", (may,)) is None


def test_generator_is_repeatable_under_input_shuffle_and_preserves_duplicate_fact_ids():
    march = date(2025, 3, 5).toordinal()
    documents = (
        _doc(10, "Alice bought a red bicycle.", "s1", 1, "Alice", march),
        _doc(11, "She rode it through the park.", "s1", 2, "Bob", march),
        _doc(12, "Alice bought a red bicycle.", "s2", 3, "Alice", march + 30),
        _doc(13, "Bob discussed a telescope.", "s2", 4, "Bob", march + 30),
    )
    query = QueryEnvelope("q", "What bicycles did Alice buy in March 2025?", 1, "current", 5)
    generator = ConversationalRecallGenerator()
    first, first_trace = generator.rank(query, RoutingIndex(documents), same_session=False)
    second, second_trace = generator.rank(
        query, RoutingIndex(tuple(reversed(documents))), same_session=False)
    assert first == second
    assert first_trace == second_trace
    assert {10, 12}.issubset(first)
    assert len(first) == len(set(first))
    assert first_trace.calendar_applied


def test_person_topic_stage_is_explicitly_disabled_by_the_frozen_default():
    assert ConversationalRecallConfig().person_topic_reserve == 0


def test_real_answer_engine_can_opt_into_cross_session_conversational_recall():
    documents = (
        _doc(
            1,
            "Alice bought the cobalt bicycle after saving for it throughout the summer.",
            "history-1", 1, "Alice"),
        _doc(
            2,
            "Bob bought a telescope and stored it in the upstairs study for astronomy nights.",
            "history-2", 2, "Bob"),
    )
    engine = HorizonAnswerEngine(
        scope_id=1, session_id="current",
        candidate_generator=ConversationalRecallGenerator(),
        allow_scope_fallback=True,
    )
    result = engine.answer("What bicycle did Alice buy?", documents)
    assert result.resolved
    assert "cobalt bicycle" in result.answer_text
    assert any(source.source_id.startswith("source:1:") for source in result.sources)
    assert result.verified_candidates >= 1


def test_answer_engine_scope_fallback_remains_opt_in_and_type_checked():
    assert HorizonAnswerEngine().allow_scope_fallback is False
    with pytest.raises(TypeError):
        HorizonAnswerEngine(allow_scope_fallback=1)  # type: ignore[arg-type]


def test_route_document_speaker_is_optional_and_validated_at_the_metadata_boundary():
    legacy = RouteDocument(1, "text", 1, "s", 1, "source")
    assert legacy.speaker is None
    with pytest.raises(ValueError):
        RouteDocument(1, "text", 1, "s", 1, "source", speaker=" ")
