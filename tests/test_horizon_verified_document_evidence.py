# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from dataclasses import replace
import json

from horizon_memory.verified_document_evidence import (
    VerifiedJsonlDocumentCorpus,
)


CONTENT = "\n".join((
    json.dumps({"_id": "b", "title": "Beta study",
                "text": "Unrelated preface. Cardiac injury raises troponin after infarction."}),
    json.dumps({"_id": "a", "title": "Alpha cohort",
                "text": "The cohort measured cardiac injury biomarkers and mortality."}),
)) + "\n"


def test_verified_snippets_are_factid_canonical_exact_and_budgeted():
    corpus = VerifiedJsonlDocumentCorpus("corpus.jsonl", CONTENT)
    assert [item.external_id for item in corpus.documents] == ["a", "b"]
    pack = corpus.pack("cardiac injury biomarker", (1, 2), max_bytes=80,
                       per_document_bytes=40)
    assert pack.state == "ready" and pack.fact_ids == (1, 2)
    assert pack.evidence_bytes <= 80 and pack.proof_sidecar_bytes > 0
    assert all(corpus.verify(item) for item in pack.citations)
    sidecar = corpus.compact_proof_sidecar(pack.citations)
    assert len(sidecar) == pack.proof_sidecar_bytes
    assert corpus.verify_compact_proof_sidecar(sidecar, pack.citations)
    assert not corpus.verify_compact_proof_sidecar(sidecar[:-1], pack.citations)
    assert any("cardiac" in item.text.casefold() for item in pack.citations)


def test_document_microcitation_corruption_fails_closed():
    corpus = VerifiedJsonlDocumentCorpus("corpus.jsonl", CONTENT)
    citation = corpus.pack("troponin infarction", (2,), max_bytes=64).citations[0]
    assert corpus.verify(citation)
    assert not corpus.verify(replace(citation, text=citation.text + "x"))
    assert not corpus.verify(replace(citation, source_sha256="0" * 64))


def test_unknown_duplicate_and_impossible_budget_fail():
    corpus = VerifiedJsonlDocumentCorpus("corpus.jsonl", CONTENT)
    for fact_ids in ((1, 1), (9,)):
        try:
            corpus.pack("query", fact_ids)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid FactIds entered the snippet pack")
    pack = corpus.pack("query", (1,), max_bytes=1, per_document_bytes=1)
    assert pack.evidence_bytes <= 1
