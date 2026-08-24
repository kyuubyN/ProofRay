# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

from collections import Counter

import pytest

from horizon_memory import (
    AnswerContextIntent, Candidate, CandidateList, EngineProfile, HorizonAnswerEngine,
    QueryWitnessFrontloadConfig, RouteDocument, frontload_query_witnesses,
    frontload_text_lines, utf8_line_prefix,
)


def test_frontload_preserves_object_identity_and_multiplicity():
    first = {"text": "Noise about apples."}
    witness = {"text": "The Quantum Sentinel needs O(k^2) memory."}
    duplicate = {"text": "Noise about apples."}
    items = (first, witness, duplicate)
    result = frontload_query_witnesses(
        items, final_question="What memory does Quantum Sentinel need?",
        turn_queries=("Quantum Sentinel memory complexity",),
        text_of=lambda item: item["text"], config=QueryWitnessFrontloadConfig(1))
    assert result[0] is witness
    assert Counter(map(id, result)) == Counter(map(id, items))


def test_text_surface_preserves_every_exact_line():
    text = "Noise about apples.\nThe Quantum Sentinel needs O(k^2) memory.\nOther noise."
    result = frontload_text_lines(
        text, final_question="What memory does Quantum Sentinel need?",
        turn_queries=("Quantum Sentinel memory complexity",),
        config=QueryWitnessFrontloadConfig(1))
    assert result.splitlines()[0] == "The Quantum Sentinel needs O(k^2) memory."
    assert Counter(result.splitlines()) == Counter(text.splitlines())


def test_no_query_and_single_item_preserve_same_tuple_object():
    items = ("one", "two")
    assert frontload_query_witnesses(items, final_question=" ") is items
    single = ("one",)
    assert frontload_query_witnesses(single, final_question="one") is single


def test_utf8_prefix_never_splits_line_or_codepoint():
    text = "ação\nsecond line"
    assert utf8_line_prefix(text, len("ação".encode("utf-8"))) == "ação"
    with pytest.raises(ValueError):
        utf8_line_prefix(text, 0)


class _FixedGenerator:
    channel = "fixed"

    def generate(self, query, index, limit, same_session=True):
        documents = index.eligible(query, same_session)
        return CandidateList(tuple(Candidate(
            document.fact_id, 1.0 / rank, "fixed", rank,
            "scope_session" if same_session else "scope_fallback")
            for rank, document in enumerate(documents[:limit], 1)))


def test_answer_engine_public_path_frontloads_context_intent_witness():
    documents = (
        RouteDocument(1, "The final report covers launch readiness and budget.", 1, "s", 1, "a"),
        RouteDocument(2, "General background about the project team and schedule.", 1, "s", 1, "b"),
        RouteDocument(3, "Quantum Sentinel requires O(k^2) memory in the worst case.", 1, "s", 1, "c"),
    )
    profile = EngineProfile(name="witness-test", answer_render_mode="full_dossier")
    intent = AnswerContextIntent("turn:1", "Quantum Sentinel memory complexity", (1, 2, 3))
    common = dict(profile=profile, session_id="s", candidate_generator=_FixedGenerator())
    baseline = HorizonAnswerEngine(**common).answer(
        "What does the final report cover?", documents, context_intents=(intent,))
    promoted = HorizonAnswerEngine(
        **common, witness_frontload=QueryWitnessFrontloadConfig(1)).answer(
            "What does the final report cover?", documents, context_intents=(intent,))
    assert baseline.resolved and promoted.resolved
    assert Counter(line.source_id for line in promoted.answer_lines) == Counter(
        line.source_id for line in baseline.answer_lines)
    assert "final report" in promoted.answer_lines[0].text
    assert "O(k^2)" in promoted.answer_lines[1].text
    assert promoted.answer_lines != baseline.answer_lines


def test_answer_engine_frontload_is_off_by_default_and_type_checked():
    assert HorizonAnswerEngine().witness_frontload is None
    with pytest.raises(TypeError):
        HorizonAnswerEngine(witness_frontload=3)  # type: ignore[arg-type]
