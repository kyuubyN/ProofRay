# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Multi-process serialization layer for the frozen durable causal ledger."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path

from .causal_adapter_protocol import CausalAdapterBatch, CausalIngestAdapter
from .durable_causal_memory import CausalDeleteReceipt, DurableCausalMemory
from .standalone_causal_memory import CausalIngestReceipt
from .typed_causal_program import TypedCausalProgram, TypedCausalResult


class ConcurrentDurableCausalMemory:
    """Serialize writers across processes and refresh state under the same lock.

    `DurableCausalMemory` remains the commit/replay authority.  This wrapper never
    caches a mutable index across operations, so two independently created instances
    cannot overwrite each other's last committed record with a stale snapshot.
    """

    def __init__(self, scope: str, ledger_path: str | Path,
                 lock_path: str | Path | None = None):
        self.scope = scope
        self.ledger_path = Path(ledger_path)
        self.lock_path = (Path(lock_path) if lock_path is not None else
                          self.ledger_path.with_name(f".{self.ledger_path.name}.lock"))
        if not scope or self.lock_path == self.ledger_path:
            raise ValueError("concurrent durable memory needs scope and a distinct lock path")

    @contextmanager
    def _locked(self, exclusive: bool):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            yield DurableCausalMemory(self.scope, self.ledger_path)
        finally:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def ingest(self, adapter: CausalIngestAdapter,
               batch: CausalAdapterBatch) -> CausalIngestReceipt:
        with self._locked(True) as memory:
            return memory.ingest(adapter, batch)

    def purge_source(self, source_id: str) -> CausalDeleteReceipt:
        with self._locked(True) as memory:
            return memory.purge_source(source_id)

    def query(self, program: TypedCausalProgram) -> TypedCausalResult:
        with self._locked(False) as memory:
            return memory.query(program)

    def verify_proof(self, proof) -> bool:
        with self._locked(False) as memory:
            return memory.verify_proof(proof)

    @property
    def fact_count(self) -> int:
        with self._locked(False) as memory:
            return memory.fact_count

    @property
    def record_count(self) -> int:
        with self._locked(False) as memory:
            return memory.record_count

    @property
    def ledger_head_sha256(self) -> str:
        with self._locked(False) as memory:
            return memory.ledger_head_sha256
