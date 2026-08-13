# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""FH-04 — tipos de resultado fechados da API pública da Horizon Memory.

Regra do contrato (Final_Horizon §9): resultados são tipos fechados. Exceções ficam para uso
inválido/programação; corrupção, ausência, conflito, expiração, overload e abstenção são estados
EXPLÍCITOS — nunca exceções e nunca "simples ausência".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# --------------------------------------------------------------------------- escrita
class WriteState(Enum):
    APPLIED = "applied"                 # mutação aplicada e coberta por um CURRENT durável
    DEDUP_REPLAY = "dedup_replay"       # retry do MESMO operation_id/command → resultado original
    VERSION_CONFLICT = "version_conflict"
    STALE_REJECTED = "stale_rejected"   # versão <= vigente
    IDEMPOTENT = "idempotent"           # no-op idempotente (não entra na dedup durável)
    REJECTED_SCOPE = "rejected_scope"   # scope != scope da instância (isolamento fail-closed)
    OVERLOAD = "overload"               # backpressure/timeout do group commit
    INCOMPATIBLE = "incompatible"       # valor fora do domínio/limite (fail-closed)


@dataclass(frozen=True)
class WriteResult:
    state: WriteState
    seq: int | None                     # wal_seq quando aplicável (APPLIED/DEDUP_REPLAY)
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.state in (WriteState.APPLIED, WriteState.DEDUP_REPLAY)


# --------------------------------------------------------------------------- leitura
class ReadState(Enum):
    PRESENT = "present"
    DELETED = "deleted"                 # deleção TERMINAL (nunca "simples ausência")
    NOT_FOUND = "not_found"
    ABSTAIN = "abstain"                 # corrupção/limite/incerteza — fail-closed
    ABSTAIN_SCOPE = "abstain_scope"     # scope != scope da instância


@dataclass(frozen=True)
class ReadResult:
    state: ReadState
    value: int | None
    source: str = "none"                # l0 | residual | tombstone | bulk | none
    generation_id: int | None = None
    read_seq: int | None = None
    reason: str = ""
    version: int | None = None

    @property
    def present(self) -> bool:
        return self.state == ReadState.PRESENT


# --------------------------------------------------------------------------- read view / snapshot
@dataclass(frozen=True)
class ReadViewHandle:
    """Snapshot imutável: carrega o manifest_blob capturado (imutável, content-addressed) + o cursor
    lógico. Ler contra este handle dá isolamento de snapshot (o blob nunca muda após capturado)."""
    scope_id: int
    generation_id: int
    read_seq: int
    manifest_blob: bytes
    _closed: bool = field(default=False, compare=False)


# --------------------------------------------------------------------------- consulta (FH-06 amplia)
class QueryState(Enum):
    EVIDENCE = "evidence"               # evidência verificada devolvida
    ABSTENTION = "abstention"           # nenhuma evidência passou o verificador
    ABSTAIN_SCOPE = "abstain_scope"


@dataclass(frozen=True)
class Provenance:
    fact_id: int
    version: int | None
    source: str
    generation_id: int | None
    read_seq: int | None
    verifier_state: str                 # verified | rejected | absent


@dataclass(frozen=True)
class QueryResult:
    """Toda query devolve proveniência, verificação e motivo de abstenção (contrato §9)."""
    state: QueryState
    value: int | None
    provenance: Provenance | None
    abstention_reason: str = ""


# --------------------------------------------------------------------------- compaction
class CompactState(Enum):
    COMPACTED = "compacted"
    NOTHING_TO_DO = "nothing_to_do"
    FAILED = "failed"
    POISONED = "poisoned"
    INCOMPATIBLE = "incompatible"


@dataclass(frozen=True)
class CompactResult:
    state: CompactState
    generation_id: int | None
    reason: str = ""


# --------------------------------------------------------------------------- recovery
class RecoverState(Enum):
    RECOVERED = "recovered"
    NO_CURRENT = "no_current"           # genesis não inicializado (não é erro)
    ABSTAIN_CURRENT = "abstain_current"
    MISSING_REQUIRED = "missing_required"
    CORRUPT = "corrupt"
    INCOMPATIBLE = "incompatible"
    RESOURCE_LIMIT = "resource_limit"
    STALE = "stale"                     # cursor avançou durante o resume (STALE_RECOVERY)
    WRITER_ACTIVE = "writer_active"     # outro writer detém o lease


@dataclass(frozen=True)
class RecoverResult:
    state: RecoverState
    generation_id: int | None
    read_seq: int | None
    unpublished_tail_bytes: int = 0
    process_crash_tested: bool = True
    power_loss_tested: bool = False     # honestidade de durabilidade (Final_Horizon §6)
    reason: str = ""


# --------------------------------------------------------------------------- export / audit
@dataclass(frozen=True)
class ExportedFact:
    fact_id: int
    version: int | None
    value: int | None
    source: str
    deletion_state: str                 # live | deleted
    checksum: str                       # sha256 hex do registro exportado


@dataclass(frozen=True)
class ExportResult:
    scope_id: int
    generation_id: int | None
    read_seq: int | None
    facts: tuple                        # tuple[ExportedFact, ...]
    manifest_sha256: str
    reason: str = ""


@dataclass(frozen=True)
class AuditReport:
    scope_id: int
    generation_id: int | None
    read_seq: int | None
    cursor_state: str
    invariants_ok: bool
    findings: tuple                     # tuple[str, ...] — violações/observações
    ledger: dict                        # LedgerReport.as_dict() (marginal + total)
