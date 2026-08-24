from __future__ import annotations

from dataclasses import replace
import pytest

from horizon_memory import (
    AnsweredClaim, HorizonAnswerEngine, ProofConvergentResolver, RouteDocument,
)


def evidence(*texts: str) -> tuple[AnsweredClaim, ...]:
    return tuple(AnsweredClaim(text, index, f"source:{index}", 1.0)
                 for index, text in enumerate(texts, 1))


def test_resolver_returns_question_bound_reopenable_proof() -> None:
    rows = evidence(
        "My camping trip to Big Sur lasted 3 days.",
        "The camping trip in Yosemite lasted 5 days.",
    )
    question = "How many days did I spend camping in total?"
    resolution = ProofConvergentResolver().resolve(question, rows)
    assert resolution is not None
    assert (resolution.text, resolution.method) == ("8 days", "proof_convergent")
    assert resolution.source_ids == ("source:1", "source:2")
    blob = resolution.certificate.compact()
    assert resolution.certificate.reopen(blob, question, rows)
    assert not resolution.certificate.reopen(blob, "How much did I spend?", rows)
    assert not resolution.certificate.reopen(blob, question, (
        replace(rows[0], text="My camping trip lasted 30 days."), rows[1]))


def test_resolver_fails_closed_when_operator_worlds_disagree() -> None:
    rows = evidence(
        "My road trip to York lasted 5 days.",
        "My trip to New York lasted 10 days.",
    )
    assert ProofConvergentResolver().resolve(
        "How many days in total was my trip to New York?", rows) is None


def test_resolver_accepts_duplicate_parent_fact_ids_but_preserves_source_identity() -> None:
    rows = (
        AnsweredClaim("I drove for four hours to A.", 7, "source:7:a", 1.0),
        AnsweredClaim("I drove for six hours to B.", 7, "source:7:b", 1.0),
    )
    resolution = ProofConvergentResolver().resolve(
        "How many hours did I spend driving in total?", rows)
    assert resolution is not None
    assert resolution.text == "10 hours"
    assert resolution.source_ids == ("source:7:a", "source:7:b")


def test_assistant_utterance_is_observed_but_not_world_authority_by_default() -> None:
    rows = (
        AnsweredClaim("I drove for four hours to A.", 1, "assistant:1", 1.0, "assistant"),
        AnsweredClaim("I drove for six hours to B.", 2, "assistant:2", 1.0, "assistant"),
    )
    question = "How many hours did I spend driving in total?"
    assert ProofConvergentResolver().resolve(question, rows) is None
    observed_utterance_resolver = ProofConvergentResolver(("assistant",))
    assert observed_utterance_resolver.resolve(question, rows) is not None


def test_public_answer_engine_reopens_proof_from_complete_verified_pool() -> None:
    documents = (
        RouteDocument(1, "My camping trip to Big Sur lasted 3 days.", 1, "s1", 1,
                      "chat:1", role="user"),
        RouteDocument(2, "The camping trip in Yosemite lasted 5 days.", 1, "s1", 1,
                      "chat:2", role="user"),
    )
    result = HorizonAnswerEngine().answer(
            "How many days did I spend camping in total?", documents)
    assert result.resolved
    assert result.direct_answer.state == "resolved"
    assert result.direct_answer.text == "8 days"
    assert result.direct_answer.method == "proof_convergent"
    assert result.direct_answer.proof_closed
    assert result.final_answer_text == "8 days"
    assert result.evidence_text == result.answer_text

    evidence_only = HorizonAnswerEngine(direct_answer_resolver=None).answer(
        "How many days did I spend camping in total?", documents)
    assert evidence_only.direct_answer.state == "not_attempted"
    assert evidence_only.final_answer_text == evidence_only.answer_text


def test_resolver_preserves_session_groups_for_causal_binding() -> None:
    rows = (
        AnsweredClaim("I just got back from an island-hopping trip to Hawaii with my family.",
                      1, "s1:1", 1.0, "user", "s1"),
        AnsweredClaim("With my family, we planned everything for the 10-day trip far in advance.",
                      2, "s1:2", 1.0, "user", "s1"),
        AnsweredClaim("I got back from New York City after five days.",
                      3, "s2:1", 1.0, "user", "s2"),
        AnsweredClaim("I am thinking of spending 4 days in Paris.",
                      4, "s3:1", 1.0, "user", "s3"),
    )
    resolution = ProofConvergentResolver().resolve(
        "How many days did I spend in total traveling in Hawaii and in New York City?", rows)
    assert resolution is not None
    assert resolution.text == "15 days"
    assert resolution.source_ids == ("s1:2", "s1:1", "s2:1")


def test_distributive_modifier_is_inside_reopened_source_span() -> None:
    rows = (AnsweredClaim(
        "My daily commute takes 45 minutes each way.", 1, "chat:1", 1.0,
        "user", "s1"),)
    question = "How long is my daily commute to work?"
    resolution = ProofConvergentResolver().resolve(question, rows)
    assert resolution is not None
    assert resolution.text == "45 minutes each way"
    blob = resolution.certificate.compact()
    assert resolution.certificate.reopen(blob, question, rows)
    changed = (replace(rows[0], text="My daily commute takes 45 minutes each day."),)
    assert not resolution.certificate.reopen(blob, question, changed)


def test_default_resolver_can_be_disabled_and_invalid_resolvers_fail_early() -> None:
    assert isinstance(HorizonAnswerEngine().direct_answer_resolver, ProofConvergentResolver)
    assert HorizonAnswerEngine(direct_answer_resolver=None).direct_answer_resolver is None
    with pytest.raises(TypeError):
        HorizonAnswerEngine(direct_answer_resolver=object())
