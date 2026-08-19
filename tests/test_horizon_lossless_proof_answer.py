from horizon_memory.claim_composer import ClaimSource, ContextIntent
from horizon_memory.lossless_proof_answer import render_lossless_proof_answer
from horizon_memory.proof_dossier import build_proof_dossier


def test_lossless_renderer_reopens_complete_claim_surfaces():
    source = ClaimSource.seal(
        "s:1", "The method is faster than the baseline. It improves accuracy by 12 percent.")
    intent = ContextIntent.seal("q:1", "How does the method compare?", (source.source_id,))
    dossier = build_proof_dossier(sources=(source,), intents=(intent,), strategy="horizon",
                                  per_fiber=8, max_bytes=4096)
    answer = render_lossless_proof_answer(dossier, (source,), max_bytes=4096)
    assert answer.state == "resolved"
    assert answer.verify(dossier, (source,), 4096)
    assert all(claim.surface in answer.text for claim in dossier.claims)


def test_lossless_renderer_respects_exact_output_budget():
    source = ClaimSource.seal("s:1", "A sufficiently long authorized sentence remains exact.")
    intent = ContextIntent.seal("q:1", "What remains exact?", (source.source_id,))
    dossier = build_proof_dossier(sources=(source,), intents=(intent,), strategy="horizon",
                                  max_bytes=4096)
    answer = render_lossless_proof_answer(dossier, (source,), max_bytes=256)
    assert answer.output_bytes <= 256
