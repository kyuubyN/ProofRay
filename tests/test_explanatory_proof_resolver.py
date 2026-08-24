from __future__ import annotations

from dataclasses import replace

from horizon_memory import (
    AnswerContextIntent, AnsweredClaim, ExplanatoryProofResolver,
    HorizonAnswerEngine, ProofCascadeResolver, RouteDocument,
)


def _case():
    evidence = (
        AnsweredClaim(
            "Alpha supports Beta through a verified relay.", 1, "source:1", 1.0,
            "document", "episode-a", sequence=1),
        AnsweredClaim(
            "Beta enables Gamma through a stable bridge.", 2, "source:2", 1.0,
            "document", "episode-a", sequence=2),
        AnsweredClaim(
            "Gamma improves Delta through exact calibration.", 3, "source:3", 1.0,
            "document", "episode-a", sequence=3),
    )
    intents = (
        AnswerContextIntent(
            "i0", "How does Alpha support Beta?", (1,), 0, "episode-a"),
        AnswerContextIntent(
            "i1", "How does Beta enable Gamma?", (2,), 1, "episode-a"),
        AnswerContextIntent(
            "i2", "How does Gamma improve Delta?", (3,), 2, "episode-a"),
    )
    return "How does Gamma improve Delta?", evidence, intents


def test_contextual_resolver_returns_reopenable_exact_proof_packet():
    question, evidence, intents = _case()
    resolution = ExplanatoryProofResolver().resolve_contextual(
        question, evidence, intents)
    assert resolution is not None
    assert resolution.method == "explanatory_proof"
    assert resolution.text.splitlines() == [item.text for item in evidence]
    assert resolution.source_ids == tuple(item.source_id for item in evidence)
    blob = resolution.certificate.compact()
    assert len(blob) <= 65_536
    assert resolution.certificate.reopen_contextual_resolution(
        blob, question, evidence, intents, text=resolution.text,
        method=resolution.method, source_ids=resolution.source_ids)


def test_contextual_certificate_binds_intents_and_complete_authority_coordinates():
    question, evidence, intents = _case()
    resolution = ExplanatoryProofResolver().resolve_contextual(
        question, evidence, intents)
    assert resolution is not None
    blob = resolution.certificate.compact()
    changed_intents = (replace(intents[0], text="How does Alpha ignore Beta?"),) + intents[1:]
    changed_evidence = (replace(
        evidence[0], session_id="other-session"),) + evidence[1:]
    assert not resolution.certificate.reopen_contextual(
        blob, question, evidence, changed_intents)
    assert not resolution.certificate.reopen_contextual(
        blob, question, changed_evidence, intents)
    assert not resolution.certificate.reopen(blob, question, evidence)


def test_unrelated_history_outside_declared_fibers_does_not_perturb_proof_bytes():
    question, evidence, intents = _case()
    resolver = ExplanatoryProofResolver()
    baseline = resolver.resolve_contextual(question, evidence, intents)
    assert baseline is not None
    irrelevant = tuple(AnsweredClaim(
        f"A penguin note {index} is unrelated to the explanatory chain.",
        100 + index, f"irrelevant:{index}", 0.0, "document", "other")
        for index in range(16))
    replicated = resolver.resolve_contextual(question, evidence + irrelevant, intents)
    assert replicated is not None
    assert replicated.text == baseline.text
    assert replicated.source_ids == baseline.source_ids
    assert replicated.certificate.compact() == baseline.certificate.compact()
    assert baseline.certificate.reopen_contextual(
        baseline.certificate.compact(), question, evidence + irrelevant, intents)


def test_context_projection_fails_closed_on_missing_turn_or_reused_fact():
    question, evidence, intents = _case()
    resolver = ExplanatoryProofResolver()
    assert resolver.resolve_contextual(
        question, evidence, (replace(intents[0], turn_index=None),) + intents[1:]) is None
    reused = intents + (AnswerContextIntent(
        "i3", "How does Alpha support Beta?", (1,), 3, "episode-a"),)
    assert resolver.resolve_contextual(question, evidence, reused) is None


def test_assistant_observation_is_not_world_authority_unless_explicitly_enabled():
    question, evidence, intents = _case()
    observed = tuple(replace(item, role="assistant") for item in evidence)
    assert ExplanatoryProofResolver().resolve_contextual(
        question, observed, intents) is None
    enabled = ExplanatoryProofResolver(authoritative_roles=("assistant",))
    assert enabled.resolve_contextual(question, observed, intents) is not None


def test_answer_engine_reopens_opt_in_explanatory_resolution_and_preserves_default():
    question, _evidence, intents = _case()
    documents = (
        RouteDocument(1, "Alpha supports Beta through a verified relay.", 1,
                      "episode-a", 1, "source", sequence=1, role="user"),
        RouteDocument(2, "Beta enables Gamma through a stable bridge.", 1,
                      "episode-a", 1, "source", sequence=2, role="user"),
        RouteDocument(3, "Gamma improves Delta through exact calibration.", 1,
                      "episode-a", 1, "source", sequence=3, role="user"),
    )
    engine = HorizonAnswerEngine(
        scope_id=1, session_id="episode-a",
        direct_answer_resolver=ExplanatoryProofResolver())
    result = engine.answer(question, documents, context_intents=intents)
    assert result.resolved
    assert result.direct_answer.state == "resolved"
    assert result.direct_answer.method == "explanatory_proof"
    assert result.direct_answer.proof_closed
    assert result.final_answer_text == result.direct_answer.text

    default = HorizonAnswerEngine(scope_id=1, session_id="episode-a").answer(
        question, documents, context_intents=intents)
    assert default.direct_answer.method != "explanatory_proof"


def test_contested_or_incomplete_world_never_emits_a_direct_answer():
    question, evidence, intents = _case()
    incomplete = evidence[:2]
    assert ExplanatoryProofResolver().resolve_contextual(
        question, incomplete, intents) is None


def test_opt_in_cascade_is_scalar_first_then_explanatory():
    scalar_evidence = (
        AnsweredClaim("The first trip lasted 3 days.", 10, "trip:1", 1.0),
        AnsweredClaim("The second trip lasted 5 days.", 11, "trip:2", 1.0),
    )
    cascade = ProofCascadeResolver()
    scalar = cascade.resolve_contextual(
        "How many days did the trips last in total?", scalar_evidence, ())
    assert scalar is not None and scalar.method == "proof_convergent"

    question, evidence, intents = _case()
    explanatory = cascade.resolve_contextual(question, evidence, intents)
    assert explanatory is not None and explanatory.method == "explanatory_proof"
