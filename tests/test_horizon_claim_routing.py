# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""FH-06.1 -- claim-level (sub-document) evidence: Candidate.claim_span, HorizonVerifier exact-
substring extraction, and EvidencePack support for multiple claims sharing one parent fact_id."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from horizon_memory import (
    Candidate, CandidateList, EvidencePack, HorizonConfig, HorizonMemory, HorizonVerifier,
    QueryEnvelope, RouteDocument, RouteState, RoutingIndex, SemanticRouter,
)
from horizon_memory.routing import CandidateGenerator

KEY = b"claim-routing-contract-key-32by!"
SCOPE = 9


def _memory():
    root = Path(tempfile.mkdtemp()) / "hm"
    memory = HorizonMemory.create(HorizonConfig(str(root), SCOPE, KEY))
    memory.put(SCOPE, 1, 1, 10)
    return memory


def _index(memory):
    generation = memory.get(SCOPE, 1).generation_id
    return RoutingIndex((
        RouteDocument(1, "Aldren activates Zephyra. Meridian reduces errors by 18 percent.",
                      SCOPE, "s1", 1, "note-a", generation),
    ))


def _query(text="Aldren Zephyra", scope=SCOPE, session="s1"):
    return QueryEnvelope("q1", text, scope, session, 10)


class CandidateClaimSpanTests(unittest.TestCase):
    def test_claim_span_defaults_to_whole_document(self):
        candidate = Candidate(1, 1.0, "test", 1, "scope_session")
        self.assertIsNone(candidate.claim_span)

    def test_invalid_claim_span_rejected(self):
        for bad_span in ((5, 5), (5, 3), (-1, 5), (0, 1, 2)):
            with self.assertRaises(ValueError):
                Candidate(1, 1.0, "test", 1, "scope_session", claim_span=bad_span)

    def test_candidate_list_allows_multiple_claims_from_same_fact_id(self):
        candidates = CandidateList((
            Candidate(1, 1.0, "test", 1, "scope_session", claim_span=(0, 10)),
            Candidate(1, 0.8, "test", 2, "scope_session", claim_span=(20, 30)),
        ))
        self.assertEqual(len(candidates.candidates), 2)

    def test_candidate_list_still_rejects_true_duplicates(self):
        with self.assertRaises(ValueError):
            CandidateList((
                Candidate(1, 1.0, "test", 1, "scope_session", claim_span=(0, 10)),
                Candidate(1, 0.9, "test", 2, "scope_session", claim_span=(0, 10)),
            ))
        with self.assertRaises(ValueError):
            CandidateList((
                Candidate(1, 1.0, "test", 1, "scope_session"),
                Candidate(1, 0.9, "test", 2, "scope_session"),
            ))


class HorizonVerifierClaimTests(unittest.TestCase):
    def test_verifier_returns_exact_claim_substring(self):
        memory = _memory()
        index = _index(memory)
        verifier = HorizonVerifier(memory, index)
        # "Aldren activates Zephyra." is document text [0:25]
        candidate = Candidate(1, 1.0, "claim", 1, "scope_session", claim_span=(0, 25))
        item = verifier.verify(_query(), candidate)
        self.assertIsNotNone(item)
        self.assertEqual(item.content, "Aldren activates Zephyra.")
        self.assertEqual(item.content_span, (0, 25))
        self.assertIsNotNone(item.parent_sha256)
        self.assertEqual(len(item.parent_sha256), 64)
        memory.close()

    def test_verifier_rejects_out_of_bounds_claim_span(self):
        memory = _memory()
        index = _index(memory)
        verifier = HorizonVerifier(memory, index)
        candidate = Candidate(1, 1.0, "claim", 1, "scope_session", claim_span=(0, 10_000))
        self.assertIsNone(verifier.verify(_query(), candidate))
        memory.close()

    def test_verifier_still_rejects_deleted_or_version_mismatched_parent_for_claims(self):
        memory = _memory()
        index = _index(memory)
        memory.delete(SCOPE, 1, 2)
        verifier = HorizonVerifier(memory, index)
        candidate = Candidate(1, 1.0, "claim", 1, "scope_session", claim_span=(0, 10))
        self.assertIsNone(verifier.verify(_query(), candidate))
        memory.close()


class EvidencePackClaimTests(unittest.TestCase):
    def test_evidence_pack_allows_multiple_claims_same_fact_id(self):
        memory = _memory()
        index = _index(memory)
        verifier = HorizonVerifier(memory, index)
        item_a = verifier.verify(_query(), Candidate(1, 1.0, "claim", 1, "scope_session",
                                                      claim_span=(0, 25)))
        item_b = verifier.verify(_query(), Candidate(1, 0.5, "claim", 2, "scope_session",
                                                      claim_span=(26, 64)))
        pack = EvidencePack.build("q1", [item_a, item_b], generation_id=None,
                                  recovery_reason="bulk")
        self.assertEqual(len(pack.items), 2)
        self.assertEqual(len(set(pack.citations)), 2)
        memory.close()

    def test_budgeted_items_treats_claims_from_same_fact_id_independently(self):
        memory = _memory()
        index = _index(memory)
        verifier = HorizonVerifier(memory, index)
        item_a = verifier.verify(_query(), Candidate(1, 1.0, "claim", 1, "scope_session",
                                                      claim_span=(0, 25)))
        item_b = verifier.verify(_query(), Candidate(1, 0.5, "claim", 2, "scope_session",
                                                      claim_span=(26, 64)))
        pack = EvidencePack.build("q1", [item_a, item_b], generation_id=None,
                                  recovery_reason="bulk")
        # Budget only large enough for the first (higher-ranked) claim's own block (46 chars),
        # not both (108 chars combined).
        selected = pack.budgeted_items(max_chars=50)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].content_span, (0, 25))
        memory.close()


class FixedClaimGenerator(CandidateGenerator):
    """Test double: emits fixed claim-level candidates instead of scoring anything."""
    channel = "fixed_claim"

    def __init__(self, spans: tuple[tuple[int, int], ...]):
        self.spans = spans

    def generate(self, query, index, limit, same_session=True):
        return CandidateList(tuple(
            Candidate(1, 1.0 / (rank + 1), self.channel, rank + 1, "scope_session", claim_span=span)
            for rank, span in enumerate(self.spans[:limit])))


class SemanticRouterClaimTests(unittest.TestCase):
    def test_router_delivers_claim_level_evidence_end_to_end(self):
        memory = _memory()
        index = _index(memory)
        generator = FixedClaimGenerator(((0, 25), (26, 64)))
        result = SemanticRouter(index, generator, HorizonVerifier(memory, index)).route(
            _query(), 2, allow_scope_fallback=False)
        self.assertEqual(result.state, RouteState.EVIDENCE)
        self.assertEqual(len(result.evidence.items), 2)
        contents = {item.content for item in result.evidence.items}
        self.assertIn("Aldren activates Zephyra.", contents)
        self.assertIn("Meridian reduces errors by 18 percent.", contents)
        memory.close()


if __name__ == "__main__":
    unittest.main()
