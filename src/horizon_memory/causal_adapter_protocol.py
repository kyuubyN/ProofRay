# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Universal removable-adapter contract for standalone Horizon causal memory."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .typed_causal_program import TypedCausalFact


@dataclass(frozen=True)
class CausalAdapterBatch:
    source_id: str
    content: str
    scope: str
    declarations: tuple[object, ...]

    @property
    def source_sha256(self) -> str:
        return hashlib.sha256(self.content.encode()).hexdigest()


@runtime_checkable
class CausalIngestAdapter(Protocol):
    """A producer proposes facts; Horizon remains the authority."""

    adapter_id: str

    def compile_batch(self, batch: CausalAdapterBatch) -> tuple[TypedCausalFact, ...]: ...
