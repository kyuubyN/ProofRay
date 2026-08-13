# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from horizon_memory.typed_causal_ingest import (
    CausalSourceEnvelope,
    DeterministicCausalCompiler,
    StructuredCausalDeclaration,
)


def _declaration(value="released"):
    return StructuredCausalDeclaration(
        1, "scope", "Ada", "status", value, (7, 7 + len(value)), 2, 2,
        event_id="status")


def test_exact_microcitation_compiles_and_round_trips_provenance():
    source = CausalSourceEnvelope.seal("event-1", "status=released")
    fact = DeterministicCausalCompiler.compile(source, _declaration())
    assert DeterministicCausalCompiler.verify(fact, source)
    assert fact.source_sha256 == source.sha256
    assert fact.source_span == (7, 15)


def test_tampered_source_digest_fails_closed():
    source = CausalSourceEnvelope("event-1", "status=released", "0" * 64)
    with pytest.raises(ValueError, match="digest mismatch"):
        DeterministicCausalCompiler.compile(source, _declaration())


def test_declaration_cannot_claim_text_outside_its_span():
    source = CausalSourceEnvelope.seal("event-1", "status=released")
    with pytest.raises(ValueError, match="microcitation"):
        DeterministicCausalCompiler.compile(source, _declaration("draft"))


def test_out_of_bounds_span_fails_closed():
    source = CausalSourceEnvelope.seal("event-1", "status=released")
    declaration = StructuredCausalDeclaration(
        1, "scope", "Ada", "status", "released", (7, 99), 2, 2)
    with pytest.raises(ValueError, match="outside"):
        DeterministicCausalCompiler.compile(source, declaration)
