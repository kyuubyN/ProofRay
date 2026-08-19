# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D84 lossless exact-claim rendering with out-of-band proof records -- promoted to core
(2026-08-18). Final stage of the `claim_composer` (D48) -> `proof_dossier` (D49) -> this (D84)
pipeline: takes an already-verified `ProofDossier` and renders it as a flat, natural-language
list of complete claim sentences within a final byte budget, dropping only whole claims (never
truncating one mid-sentence) and re-verifying every kept claim against its sealed source before
returning. Ported from `lab/lossless_proof_answer.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib

from .claim_composer import ClaimSource
from .proof_dossier import ProofDossier


RULE = "d84.lossless-proof-answer.v1"


@dataclass(frozen=True)
class LosslessProofAnswer:
    state: str
    text: str
    claim_ids: tuple[str, ...]
    output_bytes: int
    digest: str

    def verify(self, dossier: ProofDossier, sources: tuple[ClaimSource, ...],
               max_bytes: int) -> bool:
        source_map = {item.source_id: item for item in sources}
        claims = {item.claim_id: item for item in dossier.claims}
        selected = tuple(claims.get(identifier) for identifier in self.claim_ids)
        if self.state == "abstained":
            return not self.text and not self.claim_ids and self.output_bytes == 0
        return (self.state == "resolved" and bool(selected) and None not in selected
                and all(item.verify(source_map) for item in selected)
                and self.text == "\n".join(item.surface for item in selected)
                and self.output_bytes == len(self.text.encode("utf-8")) <= max_bytes
                and self.digest == _digest(self.state, self.text, self.claim_ids,
                                           self.output_bytes))


def _digest(state: str, text: str, claim_ids: tuple[str, ...], output_bytes: int) -> str:
    return hashlib.sha256(repr((RULE, state, text, claim_ids, output_bytes)).encode()).hexdigest()


def render_lossless_proof_answer(dossier: ProofDossier,
                                 sources: tuple[ClaimSource, ...], *,
                                 max_bytes: int = 24_576) -> LosslessProofAnswer:
    if max_bytes < 256 or not dossier.verify(sources, dossier.evidence_bytes):
        raise ValueError("render_lossless_proof_answer requires a verified dossier and bounded budget")
    selected = []
    rows = []
    for claim in dossier.claims:
        candidate = "\n".join((*rows, claim.surface))
        if len(candidate.encode("utf-8")) > max_bytes:
            continue
        selected.append(claim.claim_id)
        rows.append(claim.surface)
    if not selected:
        answer = LosslessProofAnswer("abstained", "", (), 0,
                                     _digest("abstained", "", (), 0))
    else:
        text = "\n".join(rows)
        ids = tuple(selected)
        size = len(text.encode("utf-8"))
        answer = LosslessProofAnswer("resolved", text, ids, size,
                                     _digest("resolved", text, ids, size))
    if not answer.verify(dossier, sources, max_bytes):
        raise ValueError("lossless answer failed exact authority verification")
    return answer


__all__ = ["LosslessProofAnswer", "RULE", "render_lossless_proof_answer"]
