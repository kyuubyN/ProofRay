# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from dataclasses import replace
import hashlib
import json

import pytest

from horizon_memory import (
    AttestedSidecarFact, CausalAdapterBatch, CausalSelector, CausalSourceEnvelope,
    DeterministicCausalCompiler, DurableAuthorizedSidecarMemory, SidecarAuthority,
    SidecarLifecycle, StructuredCausalDeclaration, TypedCausalProgram,
)


AUTHORITY = SidecarAuthority(
    "durable-sidecar", "schema", 1, hashlib.sha256(b"schema").hexdigest(),
    ("scope",), ("state",), "durable state memory")


class Adapter:
    authority = AUTHORITY

    def __init__(self, lifecycle=None):
        self.lifecycle = lifecycle

    def compile_sidecar(self, batch):
        source = CausalSourceEnvelope.seal(batch.source_id, batch.content)
        fact = DeterministicCausalCompiler.compile(source, batch.declarations[0])
        return (AttestedSidecarFact.seal(fact, self.authority, self.lifecycle),)


def _batch(fid=1, value="ready", version=1, source_id="s", asserted=True):
    return CausalAdapterBatch(source_id, value, "scope", (
        StructuredCausalDeclaration(
            fid, "scope", "device", "state", value, (0, len(value)), version,
            version, version=version, asserted=asserted, event_id="device-state"),))


def test_durable_sidecar_reopens_authority_lifecycle_and_query_index(tmp_path):
    path = tmp_path / "sidecar.jsonl"
    lifecycle = SidecarLifecycle(1, 10, AUTHORITY.purpose, "consent:1")
    memory = DurableAuthorizedSidecarMemory("scope", path, (AUTHORITY,))
    receipt = memory.ingest(Adapter(lifecycle), _batch())
    assert receipt.state == "APPLIED" and memory.record_count == 1
    reopened = DurableAuthorizedSidecarMemory("scope", path, (AUTHORITY,))
    program = TypedCausalProgram("LOOKUP", CausalSelector("device", "state"))
    assert reopened.query(program, as_of=5).value == "ready"
    assert reopened.query(program, as_of=10).reason == "sidecar_fact_expired"
    assert reopened.fact_count == 1 and reopened.ledger_head_sha256 == memory.ledger_head_sha256


def test_idempotent_retry_does_not_append_a_second_record(tmp_path):
    memory = DurableAuthorizedSidecarMemory("scope", tmp_path / "s.jsonl", (AUTHORITY,))
    assert memory.ingest(Adapter(), _batch()).state == "APPLIED"
    assert memory.ingest(Adapter(), _batch()).state == "IDEMPOTENT"
    assert memory.record_count == 1


def test_invalid_update_is_never_written(tmp_path):
    path = tmp_path / "s.jsonl"
    memory = DurableAuthorizedSidecarMemory("scope", path, (AUTHORITY,))
    assert memory.ingest(Adapter(), _batch()).state == "APPLIED"
    assert memory.ingest(Adapter(), _batch(2, "online", 2, "s2")).state == \
        "REJECTED_UPDATE"
    assert memory.record_count == 1
    assert DurableAuthorizedSidecarMemory("scope", path, (AUTHORITY,)).fact_count == 1


def test_tampered_ledger_and_changed_manifest_fail_closed_on_recovery(tmp_path):
    path = tmp_path / "s.jsonl"
    memory = DurableAuthorizedSidecarMemory("scope", path, (AUTHORITY,))
    assert memory.ingest(Adapter(), _batch()).state == "APPLIED"
    raw = bytearray(path.read_bytes())
    raw[20] ^= 1
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        DurableAuthorizedSidecarMemory("scope", path, (AUTHORITY,))

    clean = tmp_path / "clean.jsonl"
    memory = DurableAuthorizedSidecarMemory("scope", clean, (AUTHORITY,))
    assert memory.ingest(Adapter(), _batch()).state == "APPLIED"
    changed = replace(AUTHORITY, rule_version=2)
    with pytest.raises(ValueError):
        DurableAuthorizedSidecarMemory("scope", clean, (changed,))


def test_route_metadata_tampering_fails_even_after_record_chain_is_rehashed(tmp_path):
    from horizon_memory import SidecarRouteMetadata
    from horizon_memory.durable_typed_sidecar import _canonical

    class MetadataAdapter(Adapter):
        def compile_sidecar(self, batch):
            source = CausalSourceEnvelope.seal(batch.source_id, batch.content)
            fact = DeterministicCausalCompiler.compile(source, batch.declarations[0])
            metadata = SidecarRouteMetadata(
                1, "session:1", fact.version, generation_id=4, sequence=7,
                event_time=8, role="user", speaker="Alice")
            return (AttestedSidecarFact.seal(
                fact, self.authority, self.lifecycle, metadata),)

    path = tmp_path / "metadata.jsonl"
    memory = DurableAuthorizedSidecarMemory("scope", path, (AUTHORITY,))
    assert memory.ingest(MetadataAdapter(), _batch()).state == "APPLIED"
    record = json.loads(path.read_text(encoding="utf-8"))
    assert "observed_intents" not in record["facts"][0]["route_metadata"]
    record["facts"][0]["route_metadata"]["speaker"] = "Mallory"
    record.pop("record_sha256")
    record["record_sha256"] = hashlib.sha256(_canonical(record)).hexdigest()
    path.write_bytes(_canonical(record) + b"\n")
    with pytest.raises(ValueError, match="replay failed"):
        DurableAuthorizedSidecarMemory("scope", path, (AUTHORITY,))


def test_v1_attested_fact_serialization_remains_metadata_free(tmp_path):
    path = tmp_path / "v1.jsonl"
    memory = DurableAuthorizedSidecarMemory("scope", path, (AUTHORITY,))
    assert memory.ingest(Adapter(), _batch()).state == "APPLIED"
    record = json.loads(path.read_text(encoding="utf-8"))
    assert "route_metadata" not in record["facts"][0]
    reopened = DurableAuthorizedSidecarMemory("scope", path, (AUTHORITY,))
    assert reopened.attested_facts()[0].route_metadata is None
