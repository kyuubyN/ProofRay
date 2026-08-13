# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Crash-safe standalone causal memory with a hash-chained local ledger."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .causal_adapter_protocol import CausalAdapterBatch, CausalIngestAdapter
from .standalone_causal_memory import CausalIngestReceipt, StandaloneCausalMemory
from .typed_causal_program import TypedCausalFact, TypedCausalProgram, TypedCausalResult


_GENESIS = "0" * 64


@dataclass(frozen=True)
class CausalDeleteReceipt:
    state: str
    source_id: str
    removed_fact_ids: tuple[int, ...]
    previous_head_sha256: str
    new_head_sha256: str
    reason: str


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _fact_to_json(fact: TypedCausalFact) -> dict:
    return asdict(fact)


def _fact_from_json(value: dict) -> TypedCausalFact:
    data = dict(value)
    data["causes"] = tuple(data.get("causes", ()))
    data["source_span"] = tuple(data["source_span"])
    return TypedCausalFact(**data)


class _FrozenFactsAdapter:
    adapter_id = "durable-replay-v1"

    def compile_batch(self, batch: CausalAdapterBatch) -> tuple[TypedCausalFact, ...]:
        if any(not isinstance(item, TypedCausalFact) for item in batch.declarations):
            raise TypeError("durable replay declarations must be typed causal facts")
        return tuple(batch.declarations)


class DurableCausalMemory:
    """Persist whole verified batches before publishing a new in-memory index.

    The ledger is rewritten through fsync+replace.  This favors auditability and crash
    semantics over write throughput; compaction/checkpointing is a later independent gate.
    """

    def __init__(self, scope: str, ledger_path: str | Path):
        self.scope = scope
        self.ledger_path = Path(ledger_path)
        if not scope or self.ledger_path.exists() and not self.ledger_path.is_file():
            raise ValueError("durable memory needs scope and a regular ledger path")
        self._records = self._read_records()
        self._memory = self._recover(self._records)

    @staticmethod
    def _record_hash(payload: dict) -> str:
        return hashlib.sha256(_canonical(payload)).hexdigest()

    def _read_records(self) -> tuple[dict, ...]:
        if not self.ledger_path.exists():
            return ()
        try:
            lines = self.ledger_path.read_text().splitlines()
        except UnicodeDecodeError as error:
            raise ValueError("durable ledger is not canonical UTF-8") from error
        records = []
        previous = _GENESIS
        for sequence, line in enumerate(lines, 1):
            if not line:
                raise ValueError("durable ledger contains an empty/truncated record")
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError("durable ledger contains invalid/truncated JSON") from error
            if (record.get("schema") != "horizon.durable-causal-batch.v1"
                    or record.get("kind") != "batch"):
                raise ValueError("durable ledger record schema/kind is unsupported")
            claimed = record.pop("record_sha256", None)
            if record.get("sequence") != sequence or record.get("previous_sha256") != previous:
                raise ValueError("durable ledger sequence/hash chain is broken")
            actual = self._record_hash(record)
            if claimed != actual:
                raise ValueError("durable ledger record digest mismatch")
            record["record_sha256"] = claimed
            records.append(record)
            previous = claimed
        return tuple(records)

    def _recover(self, records: tuple[dict, ...]) -> StandaloneCausalMemory:
        memory = StandaloneCausalMemory(self.scope)
        adapter = _FrozenFactsAdapter()
        for record in records:
            if record.get("scope") != self.scope:
                raise ValueError("durable ledger scope differs from requested memory")
            expected_source_sha = hashlib.sha256(record["content"].encode()).hexdigest()
            if record.get("source_sha256") != expected_source_sha:
                raise ValueError("durable ledger source digest mismatch")
            facts = tuple(_fact_from_json(value) for value in record["facts"])
            batch = CausalAdapterBatch(
                record["source_id"], record["content"], self.scope, facts)
            receipt = memory.ingest(adapter, batch)
            if receipt.state not in ("APPLIED", "IDEMPOTENT"):
                raise ValueError(f"durable ledger replay failed: {receipt.state}: {receipt.reason}")
        return memory

    def _write_records(self, records: tuple[dict, ...]) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        payload = "".join(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n"
                          for record in records).encode()
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.ledger_path.name}.", dir=self.ledger_path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.ledger_path)
            directory = os.open(self.ledger_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    def ingest(self, adapter: CausalIngestAdapter,
               batch: CausalAdapterBatch) -> CausalIngestReceipt:
        adapter_id = getattr(adapter, "adapter_id", "")
        if not adapter_id or not isinstance(adapter, CausalIngestAdapter):
            raise TypeError("adapter must implement the causal ingest protocol")
        try:
            facts = adapter.compile_batch(batch)
        except (TypeError, ValueError) as error:
            return CausalIngestReceipt(
                "REJECTED_ADAPTER", adapter_id, (), batch.source_sha256, str(error))
        frozen_batch = CausalAdapterBatch(
            batch.source_id, batch.content, batch.scope, tuple(facts))
        frozen_adapter = _FrozenFactsAdapter()

        def original_adapter_receipt(receipt: CausalIngestReceipt) -> CausalIngestReceipt:
            return CausalIngestReceipt(
                receipt.state, adapter_id, receipt.fact_ids,
                receipt.source_sha256, receipt.reason)

        staged_record = {
            "schema": "horizon.durable-causal-batch.v1",
            "kind": "batch",
            "sequence": len(self._records) + 1,
            "previous_sha256": self._records[-1]["record_sha256"] if self._records else _GENESIS,
            "scope": self.scope, "adapter_id": adapter_id,
            "source_id": batch.source_id, "content": batch.content,
            "source_sha256": batch.source_sha256,
            "facts": [_fact_to_json(fact) for fact in facts],
        }
        staged_record["record_sha256"] = self._record_hash(staged_record)
        candidate_records = self._records + (staged_record,)
        try:
            staged_memory = self._recover(candidate_records)
        except ValueError as error:
            # Probe the exact already-compiled facts; adapters are never invoked twice.
            receipt = self._memory.ingest(frozen_adapter, frozen_batch)
            self._memory = self._recover(self._records)
            return original_adapter_receipt(receipt) if receipt.state != "APPLIED" else CausalIngestReceipt(
                "REJECTED_DURABILITY", adapter_id, (), batch.source_sha256, str(error))
        current_probe = self._memory.ingest(frozen_adapter, frozen_batch)
        if current_probe.state == "IDEMPOTENT":
            return original_adapter_receipt(current_probe)
        if current_probe.state != "APPLIED":
            # The staged replay and live boundary must agree; disagreement fails closed.
            self._memory = self._recover(self._records)
            return original_adapter_receipt(current_probe)
        novel_fact_ids = current_probe.fact_ids
        # Undo the probe publication by retaining only the staged instance after disk commit.
        self._memory = self._recover(self._records)
        try:
            self._write_records(candidate_records)
        except OSError as error:
            return CausalIngestReceipt(
                "REJECTED_DURABILITY", adapter_id, (), batch.source_sha256,
                f"atomic ledger commit failed: {error}")
        self._records = candidate_records
        self._memory = staged_memory
        return CausalIngestReceipt(
            "APPLIED", adapter_id, novel_fact_ids,
            batch.source_sha256, "batch durably committed before index publication")

    def query(self, program: TypedCausalProgram) -> TypedCausalResult:
        return self._memory.query(program)

    @staticmethod
    def _rechain(records: tuple[dict, ...]) -> tuple[dict, ...]:
        result = []
        previous = _GENESIS
        for sequence, source in enumerate(records, 1):
            record = {key: value for key, value in source.items()
                      if key != "record_sha256"}
            record["sequence"] = sequence
            record["previous_sha256"] = previous
            record["record_sha256"] = DurableCausalMemory._record_hash(record)
            result.append(record)
            previous = record["record_sha256"]
        return tuple(result)

    def purge_source(self, source_id: str) -> CausalDeleteReceipt:
        """Remove one source payload from the active ledger, then rebuild the index.

        The returned old/new heads let an outer audit ledger attest that a deletion
        occurred without retaining the deleted plaintext or facts in the active file.
        This is not a secure-erasure guarantee for the underlying storage medium.
        """
        if not source_id:
            raise ValueError("source purge requires an exact source identity")
        matched = tuple(record for record in self._records
                        if record.get("source_id") == source_id)
        previous_head = self.ledger_head_sha256
        if not matched:
            return CausalDeleteReceipt(
                "REJECTED_NOT_FOUND", source_id, (), previous_head, previous_head,
                "source identity is absent from the active durable ledger")
        removed = tuple(sorted({fact["fact_id"] for record in matched
                                for fact in record["facts"]}))
        retained = tuple(record for record in self._records
                         if record.get("source_id") != source_id)
        candidate = self._rechain(retained)
        try:
            staged_memory = self._recover(candidate)
        except ValueError as error:
            return CausalDeleteReceipt(
                "REJECTED_CAUSAL", source_id, removed, previous_head, previous_head,
                f"remaining facts would lose causal closure: {error}")
        try:
            self._write_records(candidate)
        except OSError as error:
            return CausalDeleteReceipt(
                "REJECTED_DURABILITY", source_id, removed, previous_head, previous_head,
                f"atomic purge commit failed: {error}")
        self._records = candidate
        self._memory = staged_memory
        return CausalDeleteReceipt(
            "PURGED", source_id, removed, previous_head, self.ledger_head_sha256,
            "source payload and facts removed from active ledger; remaining field revalidated")

    def verify_proof(self, proof) -> bool:
        return self._memory.verify_proof(proof)

    @property
    def fact_count(self) -> int:
        return self._memory.fact_count

    @property
    def record_count(self) -> int:
        return len(self._records)

    @property
    def ledger_head_sha256(self) -> str:
        return self._records[-1]["record_sha256"] if self._records else _GENESIS
