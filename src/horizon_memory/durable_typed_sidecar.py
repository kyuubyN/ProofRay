# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Crash-safe baseline ledger for the authorized typed sidecar.

This deliberately simple v1 rewrites a hash-chained ledger with fsync+replace.  It is a
correctness baseline, not the claimed solution for multi-year write scale.  Crucially,
recovery revalidates authority manifests, fact attestations, lifecycle/update lineage and
completeness claims through the same live boundary before exposing a query index.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .causal_adapter_protocol import CausalAdapterBatch
from .typed_causal_program import TypedCausalFact, TypedCausalProgram, TypedCausalResult
from .typed_sidecar import (
    AttestedCompletenessClaim, AttestedSidecarFact, AuthorizedSidecarMemory,
    CompletenessCertificate, SidecarAuthority, SidecarCompilation, SidecarIngestAdapter,
    SidecarIngestReceipt, SidecarLifecycle,
    SidecarObservedIntent, SidecarRouteMetadata, _route_metadata_payload,
)


_GENESIS = "0" * 64


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _fact_from_json(value: dict[str, object]) -> TypedCausalFact:
    data = dict(value)
    data["causes"] = tuple(data.get("causes", ()))
    data["source_span"] = tuple(data["source_span"])
    return TypedCausalFact(**data)


def _attested_to_json(item: AttestedSidecarFact) -> dict[str, object]:
    result = {
        "fact": asdict(item.fact), "lifecycle": asdict(item.lifecycle),
        "authority_sha256": item.authority_sha256,
        "attestation_sha256": item.attestation_sha256,
    }
    # Omitting the optional field preserves the exact v1 JSON representation and attestation.
    if item.route_metadata is not None:
        result["route_metadata"] = _route_metadata_payload(item.route_metadata)
    return result


def _attested_from_json(value: dict[str, object]) -> AttestedSidecarFact:
    lifecycle_data = dict(value["lifecycle"])
    lifecycle_data["supersedes"] = tuple(lifecycle_data.get("supersedes", ()))
    metadata_value = value.get("route_metadata")
    if metadata_value is None:
        metadata = None
    else:
        metadata_data = dict(metadata_value)
        if metadata_data.get("span") is not None:
            metadata_data["span"] = tuple(metadata_data["span"])
        metadata_data["observed_intents"] = tuple(
            SidecarObservedIntent(
                str(item["intent_id"]), str(item["text"]),
                tuple(item["fact_ids"]), int(item["insertion_order"]),
                item.get("turn_index"), item.get("session_id"))
            for item in metadata_data.get("observed_intents", ()))
        metadata = SidecarRouteMetadata(**metadata_data)
    return AttestedSidecarFact(
        _fact_from_json(dict(value["fact"])), SidecarLifecycle(**lifecycle_data),
        str(value["authority_sha256"]), str(value["attestation_sha256"]), metadata)


def _claim_from_json(value: dict[str, object]) -> AttestedCompletenessClaim:
    data = dict(value)
    data["fact_ids"] = tuple(data.get("fact_ids", ()))
    return AttestedCompletenessClaim(**data)


class _ReplayAdapter:
    def __init__(self, authority: SidecarAuthority, compilation: SidecarCompilation):
        self.authority = authority
        self._compilation = compilation

    def compile_sidecar(self, batch: CausalAdapterBatch) -> SidecarCompilation:
        return self._compilation


class DurableAuthorizedSidecarMemory:
    """Durably publish strict sidecar batches before exposing their new index."""

    def __init__(self, scope: str, ledger_path: str | Path,
                 authorities: tuple[SidecarAuthority, ...]):
        self.scope = scope
        self.ledger_path = Path(ledger_path)
        self._authorities = {item.adapter_id: item for item in authorities}
        if (not scope or not authorities or len(self._authorities) != len(authorities) or
                self.ledger_path.exists() and not self.ledger_path.is_file()):
            raise ValueError("durable sidecar needs scope, unique authorities and a ledger file")
        self._records = self._read_records()
        self._memory, _ = self._recover(self._records)

    @staticmethod
    def _record_hash(record: dict[str, object]) -> str:
        return hashlib.sha256(_canonical(record)).hexdigest()

    def _read_records(self) -> tuple[dict[str, object], ...]:
        if not self.ledger_path.exists():
            return ()
        try:
            lines = self.ledger_path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError) as error:
            raise ValueError("durable sidecar ledger is unreadable") from error
        records = []
        previous = _GENESIS
        for sequence, line in enumerate(lines, 1):
            if not line:
                raise ValueError("durable sidecar ledger contains a truncated record")
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError("durable sidecar ledger contains invalid JSON") from error
            if not isinstance(record, dict):
                raise ValueError("durable sidecar record must be an object")
            claimed = record.pop("record_sha256", None)
            if (record.get("schema") != "horizon.authorized-sidecar-batch.v1" or
                    record.get("sequence") != sequence or
                    record.get("previous_sha256") != previous or
                    claimed != self._record_hash(record)):
                raise ValueError("durable sidecar ledger chain or digest is invalid")
            record["record_sha256"] = claimed
            records.append(record)
            previous = claimed
        return tuple(records)

    def _recover(self, records: tuple[dict[str, object], ...]) \
            -> tuple[AuthorizedSidecarMemory, SidecarIngestReceipt | None]:
        memory = AuthorizedSidecarMemory(self.scope, tuple(self._authorities.values()))
        last = None
        for record in records:
            adapter_id = str(record.get("adapter_id", ""))
            authority = self._authorities.get(adapter_id)
            if (authority is None or record.get("scope") != self.scope or
                    record.get("authority_sha256") != authority.authority_sha256):
                raise ValueError("durable sidecar authority registry differs from ledger")
            try:
                facts = tuple(_attested_from_json(dict(item)) for item in record["facts"])
                claims = tuple(_claim_from_json(dict(item))
                               for item in record["completeness_claims"])
                compilation = SidecarCompilation(facts, claims)
                batch = CausalAdapterBatch(
                    str(record["source_id"]), str(record["content"]), self.scope, ())
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("durable sidecar record payload is invalid") from error
            if record.get("source_sha256") != batch.source_sha256:
                raise ValueError("durable sidecar source digest mismatch")
            last = memory.ingest(_ReplayAdapter(authority, compilation), batch)
            if last.state not in ("APPLIED", "IDEMPOTENT"):
                raise ValueError(f"durable sidecar replay failed: {last.state}: {last.reason}")
        return memory, last

    def _write_records(self, records: tuple[dict[str, object], ...]) -> None:
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        payload = b"".join(_canonical(record) + b"\n" for record in records)
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

    def ingest(self, adapter: SidecarIngestAdapter,
               batch: CausalAdapterBatch) -> SidecarIngestReceipt:
        supplied = getattr(adapter, "authority", None)
        adapter_id = getattr(supplied, "adapter_id", "")
        authority_sha = getattr(supplied, "authority_sha256", "")
        if not isinstance(supplied, SidecarAuthority):
            return SidecarIngestReceipt("REJECTED_ADAPTER", adapter_id, authority_sha, (),
                                        batch.source_sha256, "missing sidecar authority")
        trusted = self._authorities.get(adapter_id)
        if trusted is None or trusted != supplied:
            return SidecarIngestReceipt("REJECTED_AUTHORITY", adapter_id, authority_sha, (),
                                        batch.source_sha256,
                                        "authority is absent or differs from durable registry")
        try:
            compiled = adapter.compile_sidecar(batch)
            compilation = compiled if isinstance(compiled, SidecarCompilation) else \
                SidecarCompilation(tuple(compiled), ())
        except Exception as error:
            return SidecarIngestReceipt("REJECTED_ADAPTER", adapter_id, authority_sha, (),
                                        batch.source_sha256, str(error))
        # Validate the frozen output against a fresh replay of the current durable state.
        # The external adapter is never invoked a second time.
        try:
            probe, _ = self._recover(self._records)
        except ValueError as error:
            return SidecarIngestReceipt("REJECTED_DURABILITY", adapter_id, authority_sha, (),
                                        batch.source_sha256, str(error))
        probe_receipt = probe.ingest(_ReplayAdapter(trusted, compilation), batch)
        if probe_receipt.state == "IDEMPOTENT":
            return probe_receipt
        if probe_receipt.state != "APPLIED":
            return probe_receipt
        record = {
            "schema": "horizon.authorized-sidecar-batch.v1",
            "sequence": len(self._records) + 1,
            "previous_sha256": self.ledger_head_sha256,
            "scope": self.scope, "adapter_id": adapter_id,
            "authority_sha256": authority_sha,
            "source_id": batch.source_id, "content": batch.content,
            "source_sha256": batch.source_sha256,
            "facts": [_attested_to_json(item) for item in compilation.facts],
            "completeness_claims": [asdict(item)
                                    for item in compilation.completeness_claims],
        }
        record["record_sha256"] = self._record_hash(record)
        candidate = self._records + (record,)
        try:
            staged, receipt = self._recover(candidate)
        except (TypeError, ValueError) as error:
            return SidecarIngestReceipt("REJECTED_DURABILITY", adapter_id, authority_sha, (),
                                        batch.source_sha256, str(error))
        if receipt is None:
            raise AssertionError("candidate replay must produce a receipt")
        if receipt.state != "APPLIED":
            return SidecarIngestReceipt("REJECTED_DURABILITY", adapter_id, authority_sha, (),
                                        batch.source_sha256,
                                        "serialized replay disagreed with validated publication")
        try:
            self._write_records(candidate)
        except OSError as error:
            return SidecarIngestReceipt("REJECTED_DURABILITY", adapter_id, authority_sha, (),
                                        batch.source_sha256,
                                        f"atomic sidecar commit failed: {error}")
        self._records = candidate
        self._memory = staged
        return receipt

    def query(self, program: TypedCausalProgram, *,
              as_of: int | None = None) -> TypedCausalResult:
        return self._memory.query(program, as_of=as_of)

    def completeness_certificate(self, subject: str, predicate: str, *,
                                 as_of: int | None = None) -> CompletenessCertificate | None:
        return self._memory.completeness_certificate(subject, predicate, as_of=as_of)

    def query_certified(self, program: TypedCausalProgram,
                        certificate: CompletenessCertificate, *,
                        as_of: int | None = None) -> TypedCausalResult:
        return self._memory.query_certified(program, certificate, as_of=as_of)

    def verify_attestation(self, fact_id: int) -> bool:
        return self._memory.verify_attestation(fact_id)

    def attested_facts(self) -> tuple[AttestedSidecarFact, ...]:
        return self._memory.attested_facts()

    @property
    def fact_count(self) -> int:
        return self._memory.fact_count

    @property
    def record_count(self) -> int:
        return len(self._records)

    @property
    def ledger_head_sha256(self) -> str:
        return str(self._records[-1]["record_sha256"]) if self._records else _GENESIS
