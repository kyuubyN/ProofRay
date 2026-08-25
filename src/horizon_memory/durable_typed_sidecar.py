# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Crash-safe baseline ledger for the authorized typed sidecar.

This deliberately simple v1 rewrites a hash-chained ledger with fsync+replace.  It is a
correctness baseline, not the claimed solution for multi-year write scale.  Crucially,
recovery revalidates authority manifests, fact attestations, lifecycle/update lineage and
completeness claims through the same live boundary before exposing a query index.
"""
from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Protocol, runtime_checkable

from .causal_adapter_protocol import CausalAdapterBatch
from .durable_causal_memory import CausalDeleteReceipt
from .typed_causal_ingest import CausalSourceEnvelope
from .typed_causal_program import TypedCausalFact, TypedCausalProgram, TypedCausalResult
from .typed_sidecar import (
    AttestedCompletenessClaim, AttestedSidecarFact, AuthorizedSidecarMemory,
    CompletenessCertificate, SidecarAuthority, SidecarCompilation, SidecarIngestAdapter,
    SidecarIngestReceipt, SidecarLifecycle,
    SidecarObservedIntent, SidecarRouteMetadata, _route_metadata_payload,
)


_GENESIS = "0" * 64


@runtime_checkable
class AuthorizedSidecarRecordStore(Protocol):
    """Durable replace-all boundary for canonical sidecar records.

    The sidecar validates and stages a complete candidate ledger before invoking
    ``replace``.  A store must not return until the replacement is durable.  Only
    after that acknowledgement does the sidecar publish the staged query index.
    Records contain no trailing newline; framing is owned by the store.
    """

    def load(self) -> tuple[bytes, ...]: ...

    def replace(self, records: tuple[bytes, ...]) -> None: ...


class FileAuthorizedSidecarRecordStore:
    """The historical fsync+replace JSONL backend."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if self.path.exists() and not self.path.is_file():
            raise ValueError("durable sidecar ledger path must be a file")

    def load(self) -> tuple[bytes, ...]:
        if not self.path.exists():
            return ()
        try:
            payload = self.path.read_bytes()
        except OSError as error:
            raise ValueError("durable sidecar ledger is unreadable") from error
        if not payload:
            return ()
        if not payload.endswith(b"\n"):
            raise ValueError("durable sidecar ledger contains a truncated record")
        return tuple(payload[:-1].split(b"\n"))

    def replace(self, records: tuple[bytes, ...]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = b"".join(record + b"\n" for record in records)
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
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


class MemoryAuthorizedSidecarRecordStore:
    """Deterministic host/test store with commit-failure injection."""

    def __init__(self, records: tuple[bytes, ...] = ()):
        self._records = tuple(bytes(record) for record in records)
        self.fail_next_replace = False

    def load(self) -> tuple[bytes, ...]:
        return self._records

    def replace(self, records: tuple[bytes, ...]) -> None:
        if self.fail_next_replace:
            self.fail_next_replace = False
            raise OSError("injected record-store failure")
        self._records = tuple(bytes(record) for record in records)


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

    def __init__(self, scope: str, ledger_path: str | Path | None = None,
                 authorities: tuple[SidecarAuthority, ...] = (), *,
                 record_store: AuthorizedSidecarRecordStore | None = None):
        self.scope = scope
        if (ledger_path is None) == (record_store is None):
            raise ValueError("durable sidecar needs exactly one ledger path or record store")
        self.ledger_path = None if ledger_path is None else Path(ledger_path)
        self._record_store = (record_store if record_store is not None else
                              FileAuthorizedSidecarRecordStore(self.ledger_path))
        self._authorities = {item.adapter_id: item for item in authorities}
        if (not scope or not authorities or len(self._authorities) != len(authorities) or
                not isinstance(self._record_store, AuthorizedSidecarRecordStore)):
            raise ValueError("durable sidecar needs scope, unique authorities and a record store")
        self._records = self._read_records()
        # Canonical bytes are immutable for an unchanged prefix.  Host-backed
        # append can now compare/transmit the suffix without re-encoding every
        # historical record on every user message.
        self._record_bytes = tuple(_canonical(record) for record in self._records)
        self._memory, _ = self._recover(self._records)

    @staticmethod
    def _record_hash(record: dict[str, object]) -> str:
        return hashlib.sha256(_canonical(record)).hexdigest()

    def _read_records(self) -> tuple[dict[str, object], ...]:
        try:
            lines = self._record_store.load()
        except (TypeError, ValueError, OSError) as error:
            raise ValueError("durable sidecar ledger is unreadable") from error
        records = []
        previous = _GENESIS
        for sequence, raw in enumerate(lines, 1):
            if not isinstance(raw, bytes):
                raise ValueError("durable sidecar record store returned a non-byte record")
            if not raw:
                raise ValueError("durable sidecar ledger contains a truncated record")
            try:
                line = raw.decode("utf-8")
                record = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
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
        memory = AuthorizedSidecarMemory(
            self.scope, tuple(self._authorities.values()), _defer_publication=True)
        last = None
        for record in records:
            authority, compilation, batch = self._decode_record(record)
            last = memory.ingest(_ReplayAdapter(authority, compilation), batch)
            if last.state not in ("APPLIED", "IDEMPOTENT"):
                raise ValueError(f"durable sidecar replay failed: {last.state}: {last.reason}")
        memory._finalize_publication()
        return memory, last

    def _decode_record(self, record: dict[str, object]) \
            -> tuple[SidecarAuthority, SidecarCompilation, CausalAdapterBatch]:
        """Reopen exactly the serialized authority payload used for publication."""
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
        return authority, compilation, batch

    def _write_records(
        self,
        records: tuple[dict[str, object], ...],
        encoded: tuple[bytes, ...] | None = None,
    ) -> tuple[bytes, ...]:
        frozen = (tuple(_canonical(record) for record in records)
                  if encoded is None else encoded)
        if len(frozen) != len(records):
            raise ValueError("encoded sidecar record count differs from candidate ledger")
        self._record_store.replace(frozen)
        return frozen

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
        try:
            record["record_sha256"] = self._record_hash(record)
            # JSON round-trip before validation: the candidate index is built
            # from the exact value the host will persist, never from the external
            # adapter's in-memory objects.
            persisted = json.loads(_canonical(record))
            if not isinstance(persisted, dict):
                raise ValueError("serialized sidecar record must remain an object")
            claimed = persisted.pop("record_sha256", None)
            if claimed != self._record_hash(persisted):
                raise ValueError("serialized sidecar record digest changed")
            persisted["record_sha256"] = claimed
            decoded_authority, decoded_compilation, decoded_batch = \
                self._decode_record(persisted)
            staged = self._memory._fork_for_atomic_update()
            receipt = staged.ingest(
                _ReplayAdapter(decoded_authority, decoded_compilation), decoded_batch)
        except (TypeError, ValueError) as error:
            return SidecarIngestReceipt("REJECTED_DURABILITY", adapter_id, authority_sha, (),
                                        batch.source_sha256, str(error))
        if receipt.state == "IDEMPOTENT":
            return receipt
        if receipt.state != "APPLIED":
            return receipt
        candidate = self._records + (persisted,)
        candidate_bytes = self._record_bytes + (_canonical(persisted),)
        try:
            committed_bytes = self._write_records(candidate, candidate_bytes)
        except Exception as error:
            return SidecarIngestReceipt("REJECTED_DURABILITY", adapter_id, authority_sha, (),
                                        batch.source_sha256,
                                        f"atomic sidecar commit failed: {type(error).__name__}")
        self._records = candidate
        self._record_bytes = committed_bytes
        self._memory = staged
        return receipt

    def replace_fact_ids(self, adapter: SidecarIngestAdapter,
                         batch: CausalAdapterBatch,
                         remove_fact_ids: tuple[int, ...]) -> SidecarIngestReceipt:
        """Atomically replace selected active facts with one newer batch.

        This is an active-field replacement, not an append-only history claim.
        The caller must retain any external deletion/update audit receipt it
        needs.  No intermediate index without the old or new facts is exposed.
        """
        if (not remove_fact_ids or remove_fact_ids != tuple(sorted(set(remove_fact_ids)))
                or any(isinstance(item, bool) or not isinstance(item, int) or item < 0
                       for item in remove_fact_ids)):
            raise ValueError("sidecar replacement needs canonical FactIds")
        supplied = getattr(adapter, "authority", None)
        adapter_id = getattr(supplied, "adapter_id", "")
        authority_sha = getattr(supplied, "authority_sha256", "")
        trusted = self._authorities.get(adapter_id)
        if not isinstance(supplied, SidecarAuthority) or trusted != supplied:
            return SidecarIngestReceipt(
                "REJECTED_AUTHORITY", adapter_id, authority_sha, (), batch.source_sha256,
                "authority is absent or differs from durable registry")
        known = {item.fact.fact_id for item in self._memory.attested_facts()}
        if not set(remove_fact_ids) <= known:
            return SidecarIngestReceipt(
                "REJECTED_UPDATE", adapter_id, authority_sha, (), batch.source_sha256,
                "replacement FactIds are absent from the active field")
        try:
            compiled = adapter.compile_sidecar(batch)
            compilation = compiled if isinstance(compiled, SidecarCompilation) else \
                SidecarCompilation(tuple(compiled), ())
        except Exception as error:
            return SidecarIngestReceipt(
                "REJECTED_ADAPTER", adapter_id, authority_sha, (), batch.source_sha256,
                str(error))
        retained = self._records_without_fact_ids(set(remove_fact_ids))
        try:
            probe, _ = self._recover(retained)
            validated = probe.ingest(_ReplayAdapter(trusted, compilation), batch)
        except (TypeError, ValueError) as error:
            return SidecarIngestReceipt(
                "REJECTED_DURABILITY", adapter_id, authority_sha, (), batch.source_sha256,
                str(error))
        if validated.state != "APPLIED":
            return validated
        record = {
            "schema": "horizon.authorized-sidecar-batch.v1",
            "sequence": len(retained) + 1,
            "previous_sha256": (str(retained[-1]["record_sha256"])
                                if retained else _GENESIS),
            "scope": self.scope, "adapter_id": adapter_id,
            "authority_sha256": authority_sha,
            "source_id": batch.source_id, "content": batch.content,
            "source_sha256": batch.source_sha256,
            "facts": [_attested_to_json(item) for item in compilation.facts],
            "completeness_claims": [asdict(item)
                                    for item in compilation.completeness_claims],
        }
        record["record_sha256"] = self._record_hash(record)
        candidate = retained + (record,)
        try:
            staged, receipt = self._recover(candidate)
        except (TypeError, ValueError) as error:
            return SidecarIngestReceipt(
                "REJECTED_DURABILITY", adapter_id, authority_sha, (), batch.source_sha256,
                str(error))
        if receipt is None or receipt.state != "APPLIED":
            return SidecarIngestReceipt(
                "REJECTED_DURABILITY", adapter_id, authority_sha, (), batch.source_sha256,
                "serialized replacement disagreed with staged publication")
        try:
            committed_bytes = self._write_records(candidate)
        except Exception as error:
            return SidecarIngestReceipt(
                "REJECTED_DURABILITY", adapter_id, authority_sha, (), batch.source_sha256,
                f"atomic sidecar replacement failed: {type(error).__name__}")
        self._records = candidate
        self._record_bytes = committed_bytes
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

    @staticmethod
    def _rechain(records: tuple[dict[str, object], ...]) \
            -> tuple[dict[str, object], ...]:
        result = []
        previous = _GENESIS
        for sequence, source in enumerate(records, 1):
            record = {key: value for key, value in source.items()
                      if key != "record_sha256"}
            record["sequence"] = sequence
            record["previous_sha256"] = previous
            record["record_sha256"] = DurableAuthorizedSidecarMemory._record_hash(record)
            result.append(record)
            previous = str(record["record_sha256"])
        return tuple(result)

    def purge_fact_ids(self, fact_ids: tuple[int, ...], *,
                       source_id: str = "fact-id-selection") -> CausalDeleteReceipt:
        """Atomically remove FactIds, rechain, revalidate, then publish.

        Intent fibers are indivisible: if one member is removed every FactId in
        that observed intent is removed.  Completeness claims touching removed
        facts are dropped rather than silently rewritten under the old attestation.
        """
        if (not source_id or not fact_ids or fact_ids != tuple(sorted(set(fact_ids)))
                or any(isinstance(item, bool) or not isinstance(item, int) or item < 0
                       for item in fact_ids)):
            raise ValueError("sidecar purge needs canonical FactIds and source identity")
        previous_head = self.ledger_head_sha256
        known = {item.fact.fact_id: item for item in self._memory.attested_facts()}
        selected = set(fact_ids) & set(known)
        if not selected:
            return CausalDeleteReceipt(
                "REJECTED_NOT_FOUND", source_id, (), previous_head, previous_head,
                "FactIds are absent from the active durable sidecar")
        changed = True
        while changed:
            changed = False
            for item in known.values():
                metadata = item.route_metadata
                if metadata is None:
                    continue
                for intent in metadata.observed_intents:
                    members = set(intent.fact_ids)
                    if selected & members and not members <= selected:
                        selected.update(members)
                        changed = True
        candidate = self._records_without_fact_ids(selected)
        try:
            staged, _ = self._recover(candidate)
        except (TypeError, ValueError) as error:
            return CausalDeleteReceipt(
                "REJECTED_CAUSAL", source_id, tuple(sorted(selected)), previous_head,
                previous_head, f"remaining sidecar field failed revalidation: {error}")
        try:
            committed_bytes = self._write_records(candidate)
        except Exception as error:
            return CausalDeleteReceipt(
                "REJECTED_DURABILITY", source_id, tuple(sorted(selected)), previous_head,
                previous_head, f"atomic sidecar purge failed: {type(error).__name__}")
        self._records = candidate
        self._record_bytes = committed_bytes
        self._memory = staged
        return CausalDeleteReceipt(
            "PURGED", source_id, tuple(sorted(selected)), previous_head,
            self.ledger_head_sha256,
            "sidecar facts removed; remaining ledger rechained and revalidated")

    def _records_without_fact_ids(self, selected: set[int]) \
            -> tuple[dict[str, object], ...]:
        retained_records = []
        for source in self._records:
            record = dict(source)
            original_facts = list(record["facts"])
            facts = [item for item in original_facts
                     if int(item["fact"]["fact_id"]) not in selected]
            claims = [item for item in record["completeness_claims"]
                      if not selected.intersection(int(value) for value in item["fact_ids"])]
            if original_facts and not facts:
                # A claim sourced from a batch whose factual microcitations were
                # fully deleted cannot retain that deleted source content.
                claims = []
            if facts or claims:
                if len(facts) != len(original_facts):
                    record = self._redact_partial_record(record, facts, claims)
                else:
                    record["completeness_claims"] = claims
                retained_records.append(record)
        return self._rechain(tuple(retained_records))

    def _redact_partial_record(
        self,
        record: dict[str, object],
        retained_fact_values: list[dict[str, object]],
        retained_claim_values: list[dict[str, object]],
    ) -> dict[str, object]:
        """Rebuild a partially retained batch without bytes from removed facts."""
        authority = self._authorities.get(str(record.get("adapter_id", "")))
        if authority is None:
            raise ValueError("partial sidecar purge lost its authority manifest")
        source_id = str(record.get("source_id", ""))
        original = CausalSourceEnvelope.seal(source_id, str(record.get("content", "")))
        retained = tuple(_attested_from_json(item) for item in retained_fact_values)
        if any(not item.verify(authority, original) for item in retained):
            raise ValueError("partial sidecar purge cannot reopen retained microcitations")

        ordered = tuple(sorted(
            retained,
            key=lambda item: (item.fact.source_span[0], item.fact.fact_id),
        ))
        chunks: list[str] = []
        spans: dict[int, tuple[int, int]] = {}
        offset = 0
        for item in ordered:
            if chunks:
                chunks.append("\n")
                offset += 1
            start = offset
            chunks.append(item.fact.value)
            offset += len(item.fact.value)
            spans[item.fact.fact_id] = (start, offset)
        content = "".join(chunks)
        redacted_source = CausalSourceEnvelope.seal(source_id, content)

        resealed = tuple(sorted((AttestedSidecarFact.seal(
            replace(
                item.fact,
                source_sha256=redacted_source.sha256,
                source_span=spans[item.fact.fact_id],
            ),
            authority,
            item.lifecycle,
            item.route_metadata,
        ) for item in ordered), key=lambda item: item.fact.fact_id))
        claims = tuple(_claim_from_json(item) for item in retained_claim_values)
        resealed_claims = tuple(AttestedCompletenessClaim.seal_for_scope(
            authority,
            redacted_source,
            item.scope,
            item.subject,
            item.predicate,
            item.fact_ids,
        ) for item in claims)
        result = dict(record)
        result["content"] = content
        result["source_sha256"] = redacted_source.sha256
        result["facts"] = [_attested_to_json(item) for item in resealed]
        result["completeness_claims"] = [asdict(item) for item in resealed_claims]
        return result

    def purge_source(self, source_id: str) -> CausalDeleteReceipt:
        """Remove facts whose sealed batch source identity exactly matches."""
        if not source_id:
            raise ValueError("source purge requires an exact source identity")
        fact_ids = tuple(sorted(
            item.fact.fact_id for item in self._memory.attested_facts()
            if item.fact.source_id == source_id))
        if not fact_ids:
            head = self.ledger_head_sha256
            return CausalDeleteReceipt(
                "REJECTED_NOT_FOUND", source_id, (), head, head,
                "source identity is absent from the active durable sidecar")
        return self.purge_fact_ids(fact_ids, source_id=source_id)

    @property
    def fact_count(self) -> int:
        return self._memory.fact_count

    @property
    def record_count(self) -> int:
        return len(self._records)

    @property
    def ledger_head_sha256(self) -> str:
        return str(self._records[-1]["record_sha256"]) if self._records else _GENESIS
