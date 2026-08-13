# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import json
import stat

from horizon_memory.causal_adapter_protocol import CausalAdapterBatch
from horizon_memory.durable_causal_memory import DurableCausalMemory
from horizon_memory.json_causal_adapter import (
    JsonCausalMapping, JsonPointerCausalAdapter,
)
from horizon_memory.typed_causal_ingest import (
    CausalSourceEnvelope, DeterministicCausalCompiler, StructuredCausalDeclaration,
)
from horizon_memory.typed_causal_program import CausalSelector, TypedCausalProgram


def _batch(fact_id=1, value=42, source="event.json"):
    content = json.dumps({"value": value, "unit": "MB"}, separators=(",", ":"))
    mapping = JsonCausalMapping(fact_id, "/value", "gpu0", "memory", 10, 10,
                                "/unit", event_id=f"event:{fact_id}")
    return CausalAdapterBatch(source, content, "scope", (mapping,))


class _FactAdapter:
    adapter_id = "test-fact-v1"

    def compile_batch(self, batch):
        return tuple(batch.declarations)


class _CompileOnceAdapter(_FactAdapter):
    adapter_id = "test-compile-once-v1"

    def __init__(self):
        self.calls = 0

    def compile_batch(self, batch):
        self.calls += 1
        if self.calls != 1:
            raise AssertionError("adapter batch was compiled more than once")
        return tuple(batch.declarations)


def _causal_batch(fact_id, source_id, value, causes=()):
    source = CausalSourceEnvelope.seal(source_id, value)
    fact = DeterministicCausalCompiler.compile(source, StructuredCausalDeclaration(
        fact_id, "scope", "node", f"value_{fact_id}", value, (0, len(value)),
        fact_id, fact_id, event_id=f"event:{fact_id}", causes=causes))
    return CausalAdapterBatch(source_id, value, "scope", (fact,))


def test_durable_restart_reopens_query_and_proof(tmp_path):
    path = tmp_path / "memory.horizon.jsonl"
    adapter = JsonPointerCausalAdapter()
    memory = DurableCausalMemory("scope", path)
    receipt = memory.ingest(adapter, _batch())
    assert receipt.state == "APPLIED" and memory.record_count == 1
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    restarted = DurableCausalMemory("scope", path)
    result = restarted.query(TypedCausalProgram("LOOKUP", CausalSelector("gpu0", "memory")))
    assert (result.state, result.value, result.unit) == ("resolved", "42", "MB")
    assert len(result.proofs) == 1 and restarted.verify_proof(result.proofs[0])
    assert restarted.ledger_head_sha256 == memory.ledger_head_sha256


def test_idempotent_batch_does_not_grow_ledger(tmp_path):
    path = tmp_path / "memory.horizon.jsonl"
    adapter = JsonPointerCausalAdapter()
    memory = DurableCausalMemory("scope", path)
    assert memory.ingest(adapter, _batch()).state == "APPLIED"
    size = path.stat().st_size
    assert memory.ingest(adapter, _batch()).state == "IDEMPOTENT"
    assert memory.record_count == 1 and path.stat().st_size == size


def test_adapter_is_compiled_exactly_once_before_commit(tmp_path):
    path = tmp_path / "memory.horizon.jsonl"
    adapter = _CompileOnceAdapter()
    memory = DurableCausalMemory("scope", path)
    receipt = memory.ingest(adapter, _causal_batch(1, "once.txt", "value"))
    assert receipt.state == "APPLIED" and receipt.adapter_id == adapter.adapter_id
    assert adapter.calls == 1 and memory.fact_count == 1


def test_tampered_and_truncated_ledgers_fail_closed(tmp_path):
    path = tmp_path / "memory.horizon.jsonl"
    memory = DurableCausalMemory("scope", path)
    assert memory.ingest(JsonPointerCausalAdapter(), _batch()).state == "APPLIED"
    original = path.read_text()
    for corrupt in (original.replace('"42"', '"43"'), original[:-5]):
        path.write_text(corrupt)
        try:
            DurableCausalMemory("scope", path)
        except ValueError:
            pass
        else:
            raise AssertionError("corrupt durable ledger was accepted")
        path.write_text(original)


def test_rechained_unsupported_schema_and_bad_source_digest_fail_closed(tmp_path):
    path = tmp_path / "memory.horizon.jsonl"
    memory = DurableCausalMemory("scope", path)
    assert memory.ingest(JsonPointerCausalAdapter(), _batch()).state == "APPLIED"
    record = json.loads(path.read_text())
    for key, value in (("schema", "attacker.v1"), ("source_sha256", "0" * 64)):
        corrupt = dict(record)
        corrupt[key] = value
        corrupt.pop("record_sha256")
        corrupt["record_sha256"] = DurableCausalMemory._record_hash(corrupt)
        path.write_text(json.dumps(corrupt, sort_keys=True, separators=(",", ":")) + "\n")
        try:
            DurableCausalMemory("scope", path)
        except ValueError:
            pass
        else:
            raise AssertionError(f"rehashed invalid {key} was accepted")


def test_collision_rejection_survives_without_disk_mutation(tmp_path):
    path = tmp_path / "memory.horizon.jsonl"
    adapter = JsonPointerCausalAdapter()
    memory = DurableCausalMemory("scope", path)
    assert memory.ingest(adapter, _batch()).state == "APPLIED"
    original = path.read_bytes()
    receipt = memory.ingest(adapter, _batch(value=99, source="other.json"))
    assert receipt.state == "REJECTED_COLLISION"
    assert path.read_bytes() == original and memory.fact_count == 1


def test_failed_atomic_commit_never_publishes_index(tmp_path, monkeypatch):
    path = tmp_path / "memory.horizon.jsonl"
    memory = DurableCausalMemory("scope", path)
    monkeypatch.setattr(memory, "_write_records",
                        lambda records: (_ for _ in ()).throw(OSError("injected crash")))
    receipt = memory.ingest(JsonPointerCausalAdapter(), _batch())
    assert receipt.state == "REJECTED_DURABILITY"
    assert memory.fact_count == 0 and memory.record_count == 0 and not path.exists()


def test_source_purge_erases_payload_and_invalidates_old_proof(tmp_path):
    path = tmp_path / "memory.horizon.jsonl"
    adapter = JsonPointerCausalAdapter()
    memory = DurableCausalMemory("scope", path)
    batch = _batch(value=424242)
    assert memory.ingest(adapter, batch).state == "APPLIED"
    result = memory.query(TypedCausalProgram("LOOKUP", CausalSelector("gpu0", "memory")))
    proof = result.proofs[0]
    previous = memory.ledger_head_sha256
    receipt = memory.purge_source("event.json")
    assert receipt.state == "PURGED" and receipt.previous_head_sha256 == previous
    assert receipt.removed_fact_ids == (1,) and b"424242" not in path.read_bytes()
    assert memory.fact_count == 0 and not memory.verify_proof(proof)
    restarted = DurableCausalMemory("scope", path)
    assert restarted.query(TypedCausalProgram(
        "LOOKUP", CausalSelector("gpu0", "memory"))).state == "abstain"


def test_failed_atomic_purge_preserves_active_ledger_and_index(tmp_path, monkeypatch):
    path = tmp_path / "memory.horizon.jsonl"
    memory = DurableCausalMemory("scope", path)
    assert memory.ingest(JsonPointerCausalAdapter(), _batch()).state == "APPLIED"
    original = path.read_bytes()
    monkeypatch.setattr(memory, "_write_records",
                        lambda records: (_ for _ in ()).throw(OSError("injected crash")))
    receipt = memory.purge_source("event.json")
    assert receipt.state == "REJECTED_DURABILITY"
    assert path.read_bytes() == original and memory.fact_count == 1
    result = memory.query(TypedCausalProgram("LOOKUP", CausalSelector("gpu0", "memory")))
    assert result.state == "resolved" and result.value == "42"


def test_unknown_purge_is_noop(tmp_path):
    path = tmp_path / "memory.horizon.jsonl"
    memory = DurableCausalMemory("scope", path)
    receipt = memory.purge_source("absent")
    assert receipt.state == "REJECTED_NOT_FOUND"
    assert not path.exists() and memory.fact_count == 0


def test_purge_rejects_when_retained_fact_depends_on_source(tmp_path):
    path = tmp_path / "memory.horizon.jsonl"
    memory = DurableCausalMemory("scope", path)
    adapter = _FactAdapter()
    assert memory.ingest(adapter, _causal_batch(1, "cause.txt", "cause")).state == "APPLIED"
    assert memory.ingest(adapter, _causal_batch(2, "effect.txt", "effect", (1,))).state == "APPLIED"
    original = path.read_bytes()
    receipt = memory.purge_source("cause.txt")
    assert receipt.state == "REJECTED_CAUSAL"
    assert path.read_bytes() == original and memory.fact_count == 2
