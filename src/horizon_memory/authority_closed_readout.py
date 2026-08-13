# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Authority-closed readout: a probabilistic renderer cannot change causal truth."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Callable, Mapping

from .standalone_hssd_engine import StandaloneHSSDResult
from .typed_causal_program import TypedCausalProof


_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


@dataclass(frozen=True)
class AuthorityClosedOutput:
    state: str
    output_text: str
    value: str | None
    unit: str
    fact_ids: tuple[int, ...]
    citations: tuple[str, ...]
    model_output_accepted: bool
    reason: str


class AuthorityClosedReadout:
    """Revalidate an HSSD result and expose one canonical, model-independent output.

    Models may propose the canonical serialization, but they cannot create authority,
    weaken abstention, change a value, or alter its citations.  A missing/invalid proof
    closes before the model boundary.  A malformed model draft falls back to Horizon's
    deterministic serialization of the already-proved result.
    """

    def __init__(self, proof_verifier: Callable[[TypedCausalProof], bool]):
        if not callable(proof_verifier):
            raise TypeError("proof_verifier must be callable")
        self._verify = proof_verifier

    @staticmethod
    def _abstain(reason: str) -> AuthorityClosedOutput:
        return AuthorityClosedOutput(
            "abstain", "ABSTAIN", None, "", (), (), False, reason)

    @staticmethod
    def _canonical(value: str, unit: str, citations: tuple[str, ...]) -> str:
        return json.dumps({
            "citations": list(citations), "state": "resolved",
            "unit": unit, "value": value,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def render(self, result: StandaloneHSSDResult, model_output: str | None = None, *,
               citation_labels: Mapping[int, str] | None = None) -> AuthorityClosedOutput:
        if result.state != "resolved" or result.value is None or not result.fact_ids:
            return self._abstain("hssd_not_resolved")
        causal = result.causal_result
        if causal is None or causal.state != "resolved":
            return self._abstain("causal_result_absent")
        if (causal.value != result.value or causal.unit != result.unit or
                causal.fact_ids != result.fact_ids):
            return self._abstain("causal_projection_mismatch")
        if result.pack.state != "ready" or not set(result.fact_ids).issubset(result.pack.fact_ids):
            return self._abstain("hssd_pack_not_authorizing_result")
        if (result.program_compilation.program is None or
                result.program_compilation.state not in ("compiled", "ready")):
            return self._abstain("program_not_compiled")
        proofs = causal.proofs
        if (not proofs or tuple(proof.fact_id for proof in proofs) != result.fact_ids or
                not all(self._verify(proof) for proof in proofs)):
            return self._abstain("proof_revalidation_failed")

        labels = citation_labels or {}
        citations = tuple(labels.get(fact_id, f"F{fact_id}") for fact_id in result.fact_ids)
        if any(not _LABEL.fullmatch(label) for label in citations):
            return self._abstain("invalid_citation_label")
        canonical = self._canonical(result.value, result.unit, citations)
        accepted = model_output is not None and model_output.strip() == canonical
        return AuthorityClosedOutput(
            "resolved", canonical, result.value, result.unit, result.fact_ids,
            citations, accepted,
            "model_exact" if accepted else "deterministic_horizon_projection",
        )
