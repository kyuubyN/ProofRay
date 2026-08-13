# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from types import SimpleNamespace

from horizon_memory.authority_closed_readout import AuthorityClosedReadout
from horizon_memory.typed_causal_program import TypedCausalProof, TypedCausalResult


PROOF = TypedCausalProof(7, "source", "a" * 64, (0, 4))


def _result(*, state="resolved", value="42", proofs=(PROOF,), pack_state="ready"):
    causal = TypedCausalResult(state, value if state == "resolved" else None, "bytes", (7,),
                               "test", proofs=proofs)
    return SimpleNamespace(
        state=state, value=value if state == "resolved" else None, unit="bytes", fact_ids=(7,),
        causal_result=causal,
        pack=SimpleNamespace(state=pack_state, fact_ids=(7,)),
        program_compilation=SimpleNamespace(state="compiled", program=object()),
    )


def test_model_cannot_change_verified_value_or_citation():
    membrane = AuthorityClosedReadout(lambda proof: proof == PROOF)
    output = membrane.render(_result(), "ANSWER: 999 [FAKE]", citation_labels={7: "E1"})
    assert output.state == "resolved"
    assert output.output_text == (
        '{"citations":["E1"],"state":"resolved","unit":"bytes","value":"42"}')
    assert not output.model_output_accepted
    assert output.reason == "deterministic_horizon_projection"


def test_exact_model_serialization_may_pass_but_gains_no_authority():
    membrane = AuthorityClosedReadout(lambda _proof: True)
    expected = '{"citations":["F7"],"state":"resolved","unit":"bytes","value":"42"}'
    output = membrane.render(_result(), expected)
    assert output.output_text == expected
    assert output.model_output_accepted
    assert output.reason == "model_exact"


def test_missing_or_invalid_proof_closes_before_model_output():
    membrane = AuthorityClosedReadout(lambda _proof: False)
    assert membrane.render(_result(), "anything").output_text == "ABSTAIN"
    assert membrane.render(_result(proofs=()), "anything").output_text == "ABSTAIN"
    assert membrane.render(_result(state="abstain"), "ANSWER: 42").output_text == "ABSTAIN"


def test_pack_and_citation_boundary_are_fail_closed():
    membrane = AuthorityClosedReadout(lambda _proof: True)
    assert membrane.render(_result(pack_state="insufficient")).state == "abstain"
    assert membrane.render(_result(), citation_labels={7: "bad]label"}).state == "abstain"
