# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""horizon_batch.py — group commit do WAL (V23-A2 correctness).

Agrupa durabilidade (um `fsync` por batch) e publica visibilidade atômica por `L0Snapshot`
imutável. **Não** é transação crash-atômica do batch: um prefixo de comandos pendentes pode
reaparecer no recovery — válido, pois nenhum recebeu resposta negativa e os retries são idempotentes
pelo `operation_id`.

Camadas separadas para testabilidade:
- `prepare_batch(snapshot, hasher, commands, ...)` — **puro**: preflight sequencial em shadow state
  (clones), atribui `wal_seq` contíguos, monta frames e o `L0Snapshot`/`WalHead` candidatos. Sem I/O.
- `commit_prepared(fd, plan, failpoint)` — só o físico: `write_all → fsync` (com failpoints).
- `GroupCommitStore` — fila (deque+Condition) com 4 limites, single worker, swap atômico do
  snapshot sob lock curto, máquina de estados de request e captura de read view.

Ordem intra-batch: o `enqueue_ticket`, atribuído atomicamente na admissão (FIFO por cliente).
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import os
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

from horizon_memory._engine.horizon_durability import open_hardened_lock
from horizon_memory._engine.horizon_store import (
    BASE_ABSENT,
    BASE_ABSTAIN,
    EmptyBaseView,
    TxIdIndex,
    WalIndex,
    classify_dedup,
    preflight,
)
from horizon_memory._engine.horizon_wal import (
    APPLIED,
    CLEAN,
    DEDUP_REPLAY,
    IDEMPOTENT,
    IDEMPOTENCY_EXPIRED,
    INVALID_ARGUMENT,
    STALE_REJECTED,
    STATE_ACTIVE,
    TAIL_DROPPED,
    TXID_CONFLICT,
    VERSION_CONFLICT,
    OP_PUT,
    WalError,
    is_u8_value,
    _command_is_canonical,
    _write_all,
    content_digest,
    encode_frame,
    encode_segment_header,
    reduce_frames,
    scan,
)


def _pow2_bits(n: int) -> int:
    """Rejeita quantidades de shards que não sejam potência de dois (nada de arredondar em silêncio)."""
    if n < 2 or (n & (n - 1)) != 0:
        raise ValueError(f"shards deve ser potência de dois ≥2: {n}")
    return n.bit_length() - 1


def _make_wal_index(n: int, seed: int):
    if n and n > 1:
        from horizon_memory._engine.horizon_sharded import ShardedWalIndex
        return ShardedWalIndex.empty(_pow2_bits(n), seed)
    return WalIndex()


def _make_tx_index(n: int, seed: int):
    if n and n > 1:
        from horizon_memory._engine.horizon_sharded import ShardedTxIdIndex
        return ShardedTxIdIndex.empty(_pow2_bits(n), seed)
    return TxIdIndex()

# estados adicionais de request (observados pelo cliente)
NOT_ACCEPTED = "NOT_ACCEPTED"      # nunca entrou no worker: seguro reenviar
COMMIT_UNKNOWN = "COMMIT_UNKNOWN"  # após CLAIMED, resultado incerto: reenviar o mesmo operation_id
OVERLOADED = "OVERLOADED"          # fila cheia (comandos ou bytes)
ABSTAIN_BASE = "ABSTAIN_BASE"      # base ilegível no preflight: escrita recusada fail-closed


@dataclass(frozen=True)
class WalHead:
    scope_id: int
    segment_id: int
    first_seq: int
    durable_through_seq: int
    byte_length: int
    prefix_digest: bytes
    state: int


@dataclass(frozen=True)
class L0Snapshot:
    index: WalIndex
    txindex: TxIdIndex
    visible_through_seq: int
    wal_head: WalHead


@dataclass(frozen=True)
class PublishedStoreState:
    """Estado publicado ÚNICO do writer (C4.0): o `snapshot` (autoridade de visibilidade em memória) e
    o `cursor` (autoridade publicada — CURRENT→…→ACTIVE) avançam JUNTOS numa única troca sob `_cv`,
    evitando um snapshot em `R+1` combinado com uma prova em `R`. `cursor` é `None` num writer ainda
    sem publicação adotada (genesis em memória, testes de mecânica pura)."""
    snapshot: L0Snapshot
    cursor: object = None          # PublishedCursor (horizon_publication) ou None


@dataclass(frozen=True)
class _PublicationContext:
    """Referências que o worker de um writer ATIVADO precisa para publicar cada batch (C4.3): o
    `PublicationStore` (diretório do CURRENT), o WAL store, o object store e o keyring. `None` num writer
    legado (só memória) — nesse caso o worker usa o caminho antigo, sem publicação por batch."""
    publication_store: object
    wal_store: object
    object_store: object
    keyring: object


@dataclass(frozen=True)
class CommitCommand:
    operation_id: bytes
    op: str
    fact_id: int
    fact_version: int
    value: int | None
    enqueue_ticket: int


@dataclass(frozen=True)
class PlannedReceipt:
    ticket: int
    status: str
    wal_seq: int | None


@dataclass(frozen=True)
class BatchPlan:
    expected_first_seq: int
    frames: tuple
    receipts: tuple
    candidate_snapshot: L0Snapshot
    candidate_head: WalHead
    byte_count: int
    candidate_hasher: object     # sha256 copiado e já atualizado com os frames


@dataclass(frozen=True)
class BatchMetric:
    """Telemetria OPCIONAL por batch (emitida só quando há `telemetry_sink`; nada é acumulado sem
    ele além de contadores agregados)."""
    batch_id: int
    command_count: int
    applied_count: int
    byte_count: int
    oldest_admitted_ns: int
    claimed_ns: int
    prepare_start_ns: int
    prepare_end_ns: int
    write_end_ns: int
    fsync_end_ns: int
    publish_ns: int
    receipts_done_ns: int


def prepare_batch(snapshot: L0Snapshot, hasher, commands, key: bytes,
                  scope_id: int, segment_id: int, base=None, *, u8_domain: bool = False) -> BatchPlan:
    """Planejamento PURO (sem disco): preflight sequencial na ordem dos tickets.

    Usa `begin_mutation()`/`freeze()` — clone integral (baseline) ou COW sharded (V23-A3) sem
    diferença aqui: um snapshot candidato imutável sai do `freeze`.

    `base` (V23-B0/B4.2) é a autoridade da geração base: `lookup(fact_id)` dá a versão vigente
    (base+WAL = UMA linha de versão, impede downgrade/ressurreição) e `dedup_probe(op_id, digest)` dá a
    memória de dedup da base. Base ilegível → `ABSTAIN_BASE` (fail-closed). A dedup segue a AUTORIDADE
    ÚNICA `classify_dedup` (WAL corrente primeiro, base no miss): DEDUP_REPLAY / TXID_CONFLICT /
    IDEMPOTENCY_EXPIRED. `u8_domain=True` (store compaction-aware/standalone) congela o domínio do valor
    em u8 no INGRESSO — um PUT fora de [0,255] é INVALID_ARGUMENT, nunca ACKado e depois incompactável."""
    base = base or EmptyBaseView()
    shadow_index = snapshot.index.begin_mutation()
    shadow_tx = snapshot.txindex.begin_mutation()
    next_seq = snapshot.wal_head.durable_through_seq + 1
    expected_first_seq = next_seq
    cand_hasher = hasher.copy()
    byte_len = snapshot.wal_head.byte_length
    frames = []
    receipts = []
    seq = next_seq
    for cmd in commands:
        t = cmd.enqueue_ticket
        if not _command_is_canonical(cmd.op, cmd.fact_id, cmd.fact_version, cmd.value, cmd.operation_id):
            receipts.append(PlannedReceipt(t, INVALID_ARGUMENT, None))
            continue
        if u8_domain and cmd.op == OP_PUT and not is_u8_value(cmd.value):   # B4.2 §4: domínio u8 no ingresso
            receipts.append(PlannedReceipt(t, INVALID_ARGUMENT, None))
            continue
        digest = content_digest(cmd.op, cmd.fact_id, cmd.fact_version, cmd.value)
        dec, dseq = classify_dedup(shadow_tx.check(cmd.operation_id, digest), shadow_tx.get_seq,
                                   base, cmd.operation_id, digest)
        if dec == "dedup_replay":
            receipts.append(PlannedReceipt(t, DEDUP_REPLAY, dseq))
            continue
        if dec == "txid_conflict":
            receipts.append(PlannedReceipt(t, TXID_CONFLICT, None))
            continue
        if dec == "idempotency_expired":
            receipts.append(PlannedReceipt(t, IDEMPOTENCY_EXPIRED, None))
            continue
        if dec == "abstain_base":                    # base de outra geração/scope → fail-closed
            receipts.append(PlannedReceipt(t, ABSTAIN_BASE, None))
            continue
        cur = shadow_index.get(cmd.fact_id)          # versão vigente: L0 primeiro…
        if cur is None:                               # …senão a geração base (só no 1º write do fato)
            bf = base.lookup(cmd.fact_id)
            if bf is BASE_ABSTAIN:
                receipts.append(PlannedReceipt(t, ABSTAIN_BASE, None))   # base ilegível → fail-closed
                continue
            if bf is not BASE_ABSENT:
                cur = (bf.version, bf.op, bf.value)
        decision = preflight(cur, cmd.op, cmd.fact_version, cmd.value)
        if decision == "stale":
            receipts.append(PlannedReceipt(t, STALE_REJECTED, None))
            continue
        if decision == "idempotent":
            receipts.append(PlannedReceipt(t, IDEMPOTENT, None))
            continue
        if decision == "conflict":
            receipts.append(PlannedReceipt(t, VERSION_CONFLICT, None))
            continue
        payload = b"" if cmd.value is None else struct.pack("<I", int(cmd.value))
        frame = encode_frame(key, scope_id, segment_id, cmd.op, seq, cmd.operation_id,
                             cmd.fact_id, cmd.fact_version, payload)
        frames.append(frame)
        cand_hasher.update(frame)
        byte_len += len(frame)
        shadow_index.apply(cmd.fact_id, cmd.fact_version, cmd.op, cmd.value)
        shadow_tx.record(cmd.operation_id, digest, seq)
        receipts.append(PlannedReceipt(t, APPLIED, seq))
        seq += 1
    last_seq = seq - 1
    head = WalHead(scope_id, segment_id, snapshot.wal_head.first_seq, last_seq, byte_len,
                   cand_hasher.digest(), STATE_ACTIVE)   # SHA-256 COMPLETO (HEM2: durable_prefix_sha256)
    cand = L0Snapshot(shadow_index.freeze(), shadow_tx.freeze(), last_seq, head)
    return BatchPlan(expected_first_seq, tuple(frames), tuple(receipts), cand, head,
                     sum(len(f) for f in frames), cand_hasher)


def commit_prepared(fd: int, plan: BatchPlan, failpoint=lambda s: None):
    """Só o físico: `write_all → fsync`. Retorna (durable, write_end_ns, fsync_end_ns); sem frames
    aplicáveis → (False, 0, 0), sem fsync."""
    if not plan.frames:
        return (False, 0, 0)
    failpoint("before_write")
    _write_all(fd, b"".join(plan.frames))
    write_end = time.monotonic_ns()
    failpoint("after_write")
    os.fsync(fd)
    fsync_end = time.monotonic_ns()
    failpoint("after_fsync")
    return (True, write_end, fsync_end)


_DURABLE_PROOF_SEAL = object()     # selo privado: só emitido pelo caminho de durabilidade (C4.2)


@dataclass(frozen=True)
class DurableBatchProof:
    """Prova SELADA (C4.2) de que os bytes de um batch chegaram ao ponto de durabilidade. Emitida
    EXCLUSIVAMENTE por `commit_prepared_durable` após `write_all + fsync + fstat`. Vincula a identidade
    física do arquivo (`st_dev/st_ino`), scope/segment, o intervalo `[interval_lo, candidate_read_seq]`,
    o comprimento físico após o fsync, o SHA-256 completo do prefixo, e o digest do intent/plano. Prova
    QUE os bytes ficaram duráveis — nenhum ACK pode apontar para bytes que ainda não passaram por aqui."""
    st_dev: int
    st_ino: int
    scope_id: int
    segment_id: int
    interval_lo: int                 # source_read_seq + 1
    candidate_read_seq: int
    byte_length: int                 # st_size após o fsync
    prefix_sha256: bytes             # SHA-256 completo do prefixo durável (= candidate_head.prefix_digest)
    intent_digest: bytes             # candidate_manifest_sha256 do intent
    _seal: object


def is_sealed_durable_proof(proof) -> bool:
    return getattr(proof, "_seal", None) is _DURABLE_PROOF_SEAL


def _pwrite_all(fd: int, data: bytes, offset: int) -> None:
    """Escrita POSICIONAL total no `offset` exato (C4.2.1): não depende da posição do FD, então um FD
    deslocado nunca sobrescreve bytes já duráveis. Trata escrita parcial e EINTR."""
    mv, n = memoryview(data), 0
    while n < len(data):
        try:
            w = os.pwrite(fd, mv[n:], offset + n)
        except InterruptedError:
            continue
        if w <= 0:
            raise WalError("os.pwrite retornou 0/negativo (disco cheio?)")
        n += w


def commit_prepared_durable(fd: int, plan: BatchPlan, intent, failpoint=lambda s: None):
    """Físico + PROVA (C4.2 / C4.2.1): `fstat(prévio) → pwrite(offset exato) → fsync → fstat(final)` e
    emite um `DurableBatchProof` selado vinculado ao `intent` pelo seu DIGEST CANÔNICO íntegro. Retorna
    (proof|None, write_end_ns, fsync_end_ns). Sem frames → (None, 0, 0).

    Segurança de posição (C4.2.1): ANTES de escrever, exige `st_size == intent.source_byte_length` — o
    arquivo tem que estar EXATAMENTE no fim do prefixo anterior (sem cauda inesperada); e a escrita é
    POSICIONAL no offset `source_byte_length` (nunca sequencial dependente da posição do FD). Assim um
    FD deslocado não sobrescreve bytes nem produz um tamanho final "certo" por acaso. Se o tamanho
    prévio, o tamanho final ou o SHA/identidade do candidato divergirem, NÃO emite prova."""
    from horizon_memory._engine.horizon_publication import intent_canonical_digest
    if not plan.frames:
        return (None, 0, 0)
    head = plan.candidate_head
    st0 = os.fstat(fd)
    if st0.st_size != intent.source_byte_length:     # FD/arquivo não está no fim do prefixo de origem
        return (None, 0, 0)
    failpoint("after_wal_write_pre")
    _pwrite_all(fd, b"".join(plan.frames), intent.source_byte_length)   # offset EXATO
    write_end = time.monotonic_ns()
    failpoint("after_wal_write")
    os.fsync(fd)
    fsync_end = time.monotonic_ns()
    failpoint("after_wal_fsync")
    st = os.fstat(fd)
    if (st.st_size != intent.candidate_byte_length
            or not hmac.compare_digest(head.prefix_digest, intent.candidate_prefix_sha256)
            or head.scope_id != intent.scope_id or head.segment_id != intent.segment_id):
        return (None, write_end, fsync_end)          # divergência física → sem prova
    proof = DurableBatchProof(st.st_dev, st.st_ino, head.scope_id, head.segment_id,
                              intent.source_read_seq + 1, intent.candidate_read_seq, st.st_size,
                              head.prefix_digest, intent_canonical_digest(intent), _DURABLE_PROOF_SEAL)
    return (proof, write_end, fsync_end)


# ------------------------------ store concorrente ------------------------------
class ShutdownTimeout(Exception):
    """`close()` não conseguiu dar join no worker no prazo — o FD NÃO foi fechado (evita EBADF)."""


ROTATION_NOT_NEEDED = "ROTATION_NOT_NEEDED"   # snapshot vazio + fila vazia: nada a rotacionar
ROTATION_FENCED = "ROTATION_FENCED"


@dataclass(frozen=True)
class ShutdownResult:
    ok: bool
    reason: str


class ActivationState(IntEnum):
    ACTIVATED = 0
    WRITER_ACTIVE = 1        # já há um writer ativo no scope (lease ocupado)
    STALE_PROOF = 2          # o CURRENT mudou depois da prova
    INVALID_PROOF = 3        # selo ausente ou proof↔prepared incoerentes
    INVALID_ACTIVE = 4       # o próximo ACTIVE não é header-only
    IO_ERROR = 5
    STALE_RECOVERY = 6       # C5.1: o CURRENT avançou entre o recovery e o resume — NENHUM byte truncado


@dataclass(frozen=True)
class ActivationResult:
    state: ActivationState
    store: "GroupCommitStore | None"
    reason: str


# ------------------------------ lease exclusivo por scope (C3.1) ------------------------------
_LEASE_SEAL = object()


class LeaseState(IntEnum):
    OWNED_BY_STORE = 0        # o writer ativo detém o lease
    OWNED_BY_ROTATION = 1     # a rotação o retirou do store fenced
    OWNED_BY_PREPARED = 2     # embutido no RotationPrepared
    OWNED_BY_NEW_STORE = 3    # transferido ao writer sucessor (ativação)
    CLOSED = 4                # liberado — o scope fica sem writer
    OWNED_BY_COMPACTION = 5           # a compaction (B4) o retirou do store fenced
    OWNED_BY_COMPACTION_PREPARED = 6  # embutido no CompactionPrepared


# Matriz de posse CONGELADA (C3.2 + B4). Só estas arestas são legais. `→ CLOSED` é sempre permitido e
# tratado à parte (delega a `close()`, que libera flock/fd). Um lease fresco adquirido direto por um
# writer nasce e permanece OWNED_BY_STORE (não há aresta STORE→NEW_STORE); NEW_STORE só é alcançado
# reutilizando o lease CARREGADO por uma rotação (PREPARED→NEW_STORE) ou por uma compaction
# (COMPACTION_PREPARED→NEW_STORE). O token de rotação e o de compaction NÃO se confundem.
_LEASE_TRANSITIONS = {
    LeaseState.OWNED_BY_STORE: frozenset({LeaseState.OWNED_BY_ROTATION, LeaseState.OWNED_BY_COMPACTION}),
    LeaseState.OWNED_BY_NEW_STORE: frozenset({LeaseState.OWNED_BY_ROTATION, LeaseState.OWNED_BY_COMPACTION}),
    LeaseState.OWNED_BY_ROTATION: frozenset({LeaseState.OWNED_BY_PREPARED}),
    LeaseState.OWNED_BY_PREPARED: frozenset({LeaseState.OWNED_BY_NEW_STORE}),
    LeaseState.OWNED_BY_COMPACTION: frozenset({LeaseState.OWNED_BY_COMPACTION_PREPARED}),
    LeaseState.OWNED_BY_COMPACTION_PREPARED: frozenset({LeaseState.OWNED_BY_NEW_STORE}),
    LeaseState.CLOSED: frozenset(),
}


class ScopeWriterLease:
    """Lease exclusivo por scope, TRANSFERÍVEL (nunca liberado durante uma rotação saudável). É um
    `flock` sobre `scopes/<scope>/.writer.lock`; a máquina de estados garante que o `fd` sobrevive à
    passagem store→rotação→prepared→novo store, sem uma janela em que o scope fique livre."""

    def __init__(self, fd: int, scope_id: int):
        self._fd = fd
        self.scope_id = scope_id
        self.state = LeaseState.OWNED_BY_STORE
        self._seal = _LEASE_SEAL

    @classmethod
    def acquire(cls, directory, scope_id: int) -> "ScopeWriterLease | None":
        """`LOCK_EX|LOCK_NB` sobre `.writer.lock` ENDURECIDO (O_NOFOLLOW + S_ISREG + 0600, C3.2) —
        devolve `None` se outro processo já detém o scope."""
        fd = open_hardened_lock(directory, ".writer.lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return None
        return cls(fd, scope_id)

    def transfer(self, new_state: LeaseState) -> "ScopeWriterLease":
        """Transição de posse CONGELADA (C3.2): só as arestas de `_LEASE_TRANSITIONS` são aceitas;
        qualquer salto ou repetição levanta `WalError` SEM modificar o estado. `→ CLOSED` é sempre
        legal e delega a `close()` (libera flock/fd), então nunca vaza recurso."""
        if new_state == LeaseState.CLOSED:
            self.close()
            return self
        if new_state not in _LEASE_TRANSITIONS.get(self.state, frozenset()):
            raise WalError(f"transição de lease ilegal: {self.state.name} → {new_state.name}")
        self.state = new_state
        return self

    def close(self) -> None:
        if self.state != LeaseState.CLOSED:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(self._fd)
            except OSError:
                pass
            self.state = LeaseState.CLOSED

    @property
    def held(self) -> bool:
        return self.state != LeaseState.CLOSED

    def is_sealed(self) -> bool:
        return getattr(self, "_seal", None) is _LEASE_SEAL


class _Request:
    __slots__ = ("cmd", "size", "state", "status", "wal_seq", "_event", "cancelled",
                 "admitted_ns", "claimed_ns", "finished_ns", "_store")

    def __init__(self, cmd: CommitCommand, size: int, store):
        self.cmd = cmd
        self.size = size
        self.state = "ADMITTED"
        self.status = None
        self.wal_seq = None
        self.cancelled = False
        self.admitted_ns = 0
        self.claimed_ns = 0
        self.finished_ns = 0
        self._event = threading.Event()
        self._store = store

    def _finish(self, status: str, wal_seq):
        self.status = status
        self.wal_seq = wal_seq
        self.finished_ns = time.monotonic_ns()   # latência medida por timestamp, não pelo result()
        self.state = "DONE"
        self._event.set()

    def result(self, timeout: float | None = None):
        """Delegado ao store: a decisão de timeout/cancel é tomada SOB o lock da fila."""
        return self._store._resolve(self, timeout)

    def cancel(self) -> bool:
        return self._store._cancel(self)


@dataclass(frozen=True)
class Limits:
    max_queue_commands: int = 1024
    max_queue_bytes: int = 1 << 20
    max_batch_commands: int = 64
    max_batch_bytes: int = 1 << 18
    window_ns: int = 0                    # 0 = drena imediatamente; >0 = janela de agrupamento


class GroupCommitStore:
    """Single-writer group commit. `submit` é MPSC; um worker prepara+comita+publica batches."""

    def __init__(self, path: str, key: bytes, scope_id: int, segment_id: int = 1, first_seq: int = 1,
                 limits: Limits | None = None, telemetry_sink=None, shards: int = 0,
                 wal_shards: int | None = None, tx_shards: int | None = None,
                 wal_shard_seed: int = 0, tx_shard_seed: int = 0, base=None, *, key_id: int = 0,
                 previous_segment_digest: bytes = b"\x00" * 16):
        ws = wal_shards if wal_shards is not None else shards   # WalIndex e TxIdIndex podem diferir:
        ts = tx_shards if tx_shards is not None else shards     # o TxIdIndex cresce por operação
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        # header multissegmento completo (mesma codificação de WalWriter.create_new — gate B3-1)
        header = encode_segment_header(key, scope_id, segment_id, first_seq, key_id=key_id,
                                       previous_segment_digest=previous_segment_digest)
        _write_all(fd, header)
        os.fsync(fd)
        hasher = hashlib.sha256()
        hasher.update(header)
        head = WalHead(scope_id, segment_id, first_seq, first_seq - 1, len(header),
                       hasher.digest(), STATE_ACTIVE)     # SHA-256 completo do prefixo (só header)
        snapshot = L0Snapshot(_make_wal_index(ws, wal_shard_seed), _make_tx_index(ts, tx_shard_seed),
                              first_seq - 1, head)
        self._boot(path, key, scope_id, segment_id, fd, snapshot, hasher, first_seq,
                   limits, telemetry_sink, ws, ts, wal_shard_seed, tx_shard_seed, base,
                   key_id, previous_segment_digest)

    @classmethod
    def resume_existing(cls, path: str, key: bytes, scope_id: int, segment_id: int = 1,
                        limits: Limits | None = None, telemetry_sink=None, shards: int = 0,
                        wal_shards: int | None = None, tx_shards: int | None = None,
                        wal_shard_seed: int = 0, tx_shard_seed: int = 0, base=None, *,
                        expected_key_id: int | None = None,
                        expected_previous_segment_digest: bytes | None = None) -> "GroupCommitStore":
        """Retoma um segmento ACTIVE: scan físico → `reduce_frames` (a MESMA autoridade) na
        representação escolhida (sharded ou clone) → restaura hasher/WalHead/`_next_seq`."""
        ws = wal_shards if wal_shards is not None else shards
        ts = tx_shards if tx_shards is not None else shards
        blob = Path(path).read_bytes()
        sr = scan(blob, key, required=True, scope_id=scope_id, segment_id=segment_id)
        if sr.sealed:
            raise WalError("segmento SEALED não pode ser retomado")
        if sr.classification not in (CLEAN, TAIL_DROPPED):
            raise WalError(f"não é possível retomar: {sr.classification}")
        h = sr.header
        if h is None:
            raise WalError("header não autenticado ao retomar")
        if expected_key_id is not None and h.key_id != expected_key_id:
            raise WalError("key_id do header diverge do esperado")
        if (expected_previous_segment_digest is not None
                and not hmac.compare_digest(h.previous_segment_digest,
                                            expected_previous_segment_digest)):
            raise WalError("previous_segment_digest do header diverge do esperado")
        first_seq = h.first_seq                           # do header autenticado, nunca default
        # `initial_seq = first_seq-1`: um segmento header-only (0 frames) retoma em `first_seq`, não em
        # 1 — o próximo seq é `first_seq`, e o durable_through fica em `first_seq-1` (B3-4/ativação).
        rr = reduce_frames(sr.frames,
                           wal_index_factory=lambda: _make_wal_index(ws, wal_shard_seed),
                           tx_index_factory=lambda: _make_tx_index(ts, tx_shard_seed),
                           base=base, initial_seq=first_seq - 1)
        if rr.classification != "OK":
            raise WalError(f"história não canônica ao retomar: {rr.classification}")
        hasher = hashlib.sha256()
        hasher.update(blob[:sr.consumed_bytes])          # prefixo íntegro reconstrói o hasher
        fd = os.open(path, os.O_WRONLY)                   # sem O_TRUNC
        if sr.classification == TAIL_DROPPED:
            os.ftruncate(fd, sr.consumed_bytes)
            os.fsync(fd)
        os.lseek(fd, sr.consumed_bytes, os.SEEK_SET)
        head = WalHead(scope_id, segment_id, first_seq, rr.applied_seq, sr.consumed_bytes,
                       hasher.digest(), STATE_ACTIVE)     # SHA-256 completo do prefixo retomado
        snapshot = L0Snapshot(rr.index, rr.txindex, rr.applied_seq, head)
        self = cls.__new__(cls)
        self._boot(path, key, scope_id, segment_id, fd, snapshot, hasher, rr.applied_seq + 1,
                   limits, telemetry_sink, ws, ts, wal_shard_seed, tx_shard_seed, base,
                   h.key_id, h.previous_segment_digest)
        return self

    def _boot(self, path, key, scope_id, segment_id, fd, snapshot, hasher, next_seq,
              limits, telemetry_sink, ws, ts, wal_shard_seed, tx_shard_seed, base,
              key_id=0, previous_segment_digest=b"\x00" * 16, start_worker=True, cursor=None,
              pubctx=None, u8_domain=False):
        self.key = key
        self.scope_id = scope_id
        self.segment_id = segment_id
        self._lease = None               # ScopeWriterLease exclusivo por scope (C3/C3.1) ou None
        self.key_id = key_id
        self.previous_segment_digest = previous_segment_digest
        self.limits = limits or Limits()
        self._fd = fd
        self._hasher = hasher
        self._pubctx = pubctx            # _PublicationContext (C4.3) ou None (writer legado só-memória)
        # C4.0: autoridade ÚNICA (snapshot + cursor publicado juntos); `_snapshot` é uma view read-only
        self._published_state = PublishedStoreState(snapshot, cursor)
        self._wal_shards, self._tx_shards = ws, ts
        self._wal_shard_seed, self._tx_shard_seed = wal_shard_seed, tx_shard_seed
        self._base = base or EmptyBaseView()
        self._u8_domain = u8_domain      # B4.2: congela u8 no ingresso (store compaction-aware/standalone)
        self._cv = threading.Condition()
        self._queue: deque = deque()
        self._queue_bytes = 0
        self._ticket = 0
        self._next_seq = next_seq
        self._batch_id = 0
        self._poisoned = False
        self._stop = False
        self._rotating = False
        self._fd_open = True
        self.failpoint = lambda s: None          # tests substituem
        self._telemetry_sink = telemetry_sink
        self.stats = {"batches": 0, "batches_with_frames": 0, "fsync_count": 0, "applied": 0,
                      "max_queue_depth": 0}
        self._worker = threading.Thread(target=self._run, daemon=True)
        if start_worker:
            self._worker.start()

    def _start_worker(self) -> None:
        """Inicia o worker de um store construído PAUSADO (ativação do C3): transição única após
        CURRENT verificado, lease adquirido e índices carregados injetados."""
        if not self._worker.is_alive():
            self._worker.start()

    def _release_writer_lease(self) -> None:
        """Fecha o lease exclusivo EXATAMENTE uma vez — em close/poison. NÃO é chamado no fence de
        rotação (lá o lease é DESTACADO, não fechado — C3.1)."""
        lease = getattr(self, "_lease", None)
        if lease is not None:
            self._lease = None
            lease.close()

    def detach_writer_lease(self):
        """Retira o lease do store SEM fechá-lo (transferência de posse para a rotação, C3.1). O
        store fica sem lease; quem recebe é responsável por fechá-lo ou repassá-lo."""
        lease = getattr(self, "_lease", None)
        self._lease = None
        return lease

    # -------- API --------
    def submit(self, op: str, fact_id: int, fact_version: int, value, operation_id: bytes) -> _Request:
        size = 76 + (4 if value is not None else 0)   # estimativa de bytes do frame
        with self._cv:
            if self._poisoned or self._stop:
                return self._instant(op, fact_id, fact_version, value, operation_id, size, NOT_ACCEPTED)
            if size > self.limits.max_batch_bytes:    # singleton oversized: rejeitado na admissão
                return self._instant(op, fact_id, fact_version, value, operation_id, size, OVERLOADED)
            over = (len(self._queue) >= self.limits.max_queue_commands
                    or self._queue_bytes + size > self.limits.max_queue_bytes)
            if over:
                return self._instant(op, fact_id, fact_version, value, operation_id, size, OVERLOADED)
            self._ticket += 1
            req = _Request(CommitCommand(operation_id, op, fact_id, fact_version, value, self._ticket),
                           size, self)
            req.admitted_ns = time.monotonic_ns()
            self._queue.append(req)
            self._queue_bytes += size
            if len(self._queue) > self.stats["max_queue_depth"]:
                self.stats["max_queue_depth"] = len(self._queue)
            self._cv.notify()
            return req

    def _instant(self, op, fact_id, fact_version, value, operation_id, size, status) -> _Request:
        r = _Request(CommitCommand(operation_id, op, fact_id, fact_version, value, -1), size, self)
        r._finish(status, None)
        return r

    def _resolve(self, req: _Request, timeout):
        """Timeout/cancel resolvido SOB o lock: exatamente um lado vence a corrida com o claim."""
        if req._event.wait(timeout):
            return (req.status, req.wal_seq)
        with self._cv:
            if req.state == "DONE":
                return (req.status, req.wal_seq)
            if req.state == "ADMITTED":
                req.cancelled = True                  # o worker vai finalizá-lo NOT_ACCEPTED
                return (NOT_ACCEPTED, None)
            return (COMMIT_UNKNOWN, None)             # CLAIMED ou posterior: incerto, nunca "não aconteceu"

    def _cancel(self, req: _Request) -> bool:
        with self._cv:
            if req.state == "ADMITTED":
                req.cancelled = True
                return True
            return False                               # CLAIMED/DONE: tarde demais

    @property
    def _snapshot(self) -> L0Snapshot:
        """View read-only do snapshot vigente — a autoridade é `self._published_state` (C4.0). Escrever
        o snapshot é sempre uma troca de `_published_state` (snapshot+cursor juntos), nunca deste campo."""
        return self._published_state.snapshot

    def capture_read_view(self) -> L0Snapshot:
        """Captura ATÔMICA do ponto de linearização: `R` (=visible_through_seq), e o
        `wal_head` (byte_length + `prefix_digest` SHA-256 COMPLETO) — todos derivados do MESMO
        snapshot publicado sob o lock após o fsync (troca atômica em `_run`). Assim
        `ActivePrefixExpectation.from_wal_head(snap.wal_head)` descreve exatamente o prefixo durável
        em `R`, sem reler nem rehashear o arquivo."""
        with self._cv:
            return self._published_state.snapshot    # referência imutável; leitura posterior sem lock

    def capture_published_state(self) -> PublishedStoreState:
        """Captura ATÔMICA do estado publicado (snapshot + cursor) sob o lock — snapshot e cursor
        sempre coerentes entre si (C4.0). A rotação e o commit publicado partem exclusivamente daqui."""
        with self._cv:
            return self._published_state

    def adopt_published_cursor(self, cursor) -> None:
        """Adota um `PublishedCursor` num writer que já tem snapshot mas ainda não carregava a
        autoridade publicada (genesis recém-publicado). O ACTIVE do cursor tem que casar o segmento
        vigente do snapshot (scope/segment_id/first_seq) — senão o cursor é de outra geração/segmento."""
        from horizon_memory._engine.horizon_publication import is_sealed_cursor
        if not is_sealed_cursor(cursor):
            raise WalError("cursor não selado (adopt)")
        with self._cv:
            head = self._published_state.snapshot.wal_head
            act = cursor.active_descriptor
            if (act.segment_id != head.segment_id or act.first_seq != head.first_seq
                    or cursor.proof.scope_id != head.scope_id):
                raise WalError("cursor não casa o segmento ACTIVE vigente do writer")
            self._published_state = PublishedStoreState(self._published_state.snapshot, cursor)

    @property
    def poisoned(self) -> bool:
        return self._poisoned

    def close(self, timeout: float = 5.0):
        """Shutdown: para admissão e drena. join no prazo → fecha o FD; timeout → ShutdownTimeout
        e o FD NÃO é fechado (evita EBADF dentro do worker que ainda roda)."""
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        self._worker.join(timeout=timeout)
        if self._worker.is_alive():
            raise ShutdownTimeout("worker ainda ativo; FD preservado")
        if self._fd_open:
            os.close(self._fd)
            self._fd_open = False
        self._release_writer_lease()

    def fence_and_drain(self, timeout: float = 5.0) -> L0Snapshot:
        """Fase de FENCE+DRENO da rotação (B3-4): sob o lock, fecha a admissão (novos `submit` →
        NOT_ACCEPTED, seguro reenviar); os já admitidos drenam normalmente. Espera o worker esvaziar
        a fila, captura ATOMICAMENTE o snapshot final (L0 + `wal_head` + último ticket) e fecha o FD
        do writer antigo. Aborta se o writer estiver `POISONED` — nunca volta a WRITABLE sozinho."""
        with self._cv:
            if self._poisoned:
                raise WalError("writer POISONED — rotação abortada")
            self._rotating = True
            self._stop = True
            self._cv.notify_all()
        self._worker.join(timeout=timeout)
        if self._worker.is_alive():
            raise ShutdownTimeout("worker ainda ativo; rotação não concluída")
        if self._poisoned:
            raise WalError("writer POISONED após dreno — rotação abortada")
        snap = self._snapshot
        if self._fd_open:
            os.close(self._fd)
            self._fd_open = False
        return snap

    def begin_rotation(self, timeout: float = 5.0):
        """Decisão ATÔMICA da rotação (B3-5.0): sob o lock, se o snapshot está VAZIO
        (`durable_through == first_seq-1`) E a fila está vazia, devolve `(ROTATION_NOT_NEEDED, None)`
        SEM alterar `_stop`/`_rotating` — nunca se produz um SEALED vazio nem se fecha o writer à toa.
        Caso contrário, aplica o fence, drena, captura o snapshot final e fecha o FD antigo."""
        with self._cv:
            if self._poisoned:
                raise WalError("writer POISONED — rotação abortada")
            head = self._snapshot.wal_head
            empty_and_idle = head.durable_through_seq < head.first_seq and not self._queue
            if empty_and_idle:
                return (ROTATION_NOT_NEEDED, None, None)   # nada muda: sem fence, lease fica no store
            self._rotating = True
            self._stop = True
            self._cv.notify_all()
        self._worker.join(timeout=timeout)
        if self._worker.is_alive():
            raise ShutdownTimeout("worker ainda ativo; rotação não concluída")
        if self._poisoned:
            raise WalError("writer POISONED após dreno — rotação abortada")
        snap = self._snapshot
        if self._fd_open:
            os.close(self._fd)
            self._fd_open = False
        # C3.1: o lease é DESTACADO (não fechado) — a rotação o carrega até o novo store, sem janela
        lease = self.detach_writer_lease()
        if lease is not None:
            lease.transfer(LeaseState.OWNED_BY_ROTATION)
        return (ROTATION_FENCED, snap, lease)

    def begin_compaction(self, timeout: float = 5.0):
        """Fence + dreno para a COMPACTION (B4): ao contrário da rotação, é SEMPRE necessária (compactar
        absorve a base + o WAL numa nova geração mesmo com ACTIVE vazio). Sob o lock: fence de admissão,
        drena o worker (batches já admitidos PUBLICAM durante o dreno, avançando snapshot+cursor até o R
        final), fecha o FD antigo e DESTACA o lease para `OWNED_BY_COMPACTION`.

        Captura PÓS-DRENO obrigatória (FH-00 §1): devolve o `PublishedStoreState` INTEIRO (snapshot +
        cursor coerentes no R final) — a compaction deriva sua origem EXCLUSIVAMENTE daqui, nunca de um
        cursor externo capturado antes do fence. Devolve (published_state, lease). POISONED aborta."""
        with self._cv:
            if self._poisoned:
                raise WalError("writer POISONED — compaction abortada")
            self._rotating = True
            self._stop = True
            self._cv.notify_all()
        self._worker.join(timeout=timeout)
        if self._worker.is_alive():
            raise ShutdownTimeout("worker ainda ativo; compaction não concluída")
        if self._poisoned:
            raise WalError("writer POISONED após dreno — compaction abortada")
        published = self._published_state       # snapshot + cursor coerentes, PÓS-dreno (R final)
        if self._fd_open:
            os.close(self._fd)
            self._fd_open = False
        lease = self.detach_writer_lease()
        if lease is not None:
            lease.transfer(LeaseState.OWNED_BY_COMPACTION)
        return (published, lease)

    def await_fenced_shutdown(self, timeout: float = 5.0) -> ShutdownResult:
        """Finalização segura após um fence que expirou (`_stop` já ativo): NUNCA reabre a admissão;
        só espera o worker terminar e fecha o FD EXATAMENTE uma vez. Evita vazar o descriptor sem
        criar falso sucesso."""
        self._worker.join(timeout=timeout)
        if self._worker.is_alive():
            return ShutdownResult(False, "worker ainda ativo; FD preservado")
        with self._cv:
            if self._fd_open:
                os.close(self._fd)
                self._fd_open = False
        return ShutdownResult(True, "encerrado")

    @classmethod
    def activate_prepared(cls, prepared, proof, publication_store, wal_store, object_store,
                          keyring, *, base=None, shards=0, wal_shards=None, tx_shards=None,
                          wal_shard_seed=0, tx_shard_seed=0, limits=None, telemetry_sink=None,
                          failpoint=None) -> "ActivationResult":
        """V23-C3 — ativa o writer do próximo ACTIVE SÓ com prova, sem janela concorrente. Fluxo:
        writer.lock (LOCK_EX|LOCK_NB, lease exclusivo por scope) → publish.lock → relê CURRENT e exige
        que ainda aponte para `proof.publication_sha256` → valida selos e proof↔prepared → abre o
        próximo ACTIVE e exige EXATAMENTE header-only → constrói o store PAUSADO com índices carregados,
        `next_seq=R+1` → libera publish.lock → inicia o worker. Qualquer falha: nenhum worker, lease
        liberado.

        C3.2 — `publication_store` é um `PublicationStore` (não um diretório cru): seu `scope_id` TEM
        que casar `proof.scope_id`, senão um store de outro scope autorizaria esta ativação. As locks
        (`.writer.lock`/`.publish.lock`) são abertas endurecidas (O_NOFOLLOW + S_ISREG + 0600)."""
        from horizon_memory._engine.horizon_publication import (
            CursorState, PROOF_SEAL, PublicationState, PublicationStore, open_published_cursor,
            read_current)
        from horizon_memory._engine.horizon_rotation import is_sealed_prepared
        from horizon_memory._engine.horizon_walstore import WalIdentity, WalStoreState
        fp = failpoint or (lambda s: None)
        if not isinstance(publication_store, PublicationStore):
            return ActivationResult(ActivationState.INVALID_PROOF, None,
                                    "publication_store deve ser um PublicationStore (C3.2)")
        if publication_store.scope_id != proof.scope_id:
            return ActivationResult(ActivationState.INVALID_PROOF, None,
                                    "scope do PublicationStore != proof.scope_id")
        directory = Path(publication_store.directory)

        # 1) selos + proof↔prepared (identidade e continuidade)
        if getattr(proof, "seal", None) is not PROOF_SEAL or not is_sealed_prepared(prepared):
            return ActivationResult(ActivationState.INVALID_PROOF, None, "selo ausente")
        nxt = prepared.next_active_descriptor
        if (proof.read_seq != prepared.read_seq or proof.next_active_first_seq != proof.read_seq + 1
                or proof.next_active_segment_id != nxt.segment_id
                or proof.next_active_first_seq != nxt.first_seq
                or proof.next_active_prefix_length != nxt.durable_prefix_length
                or not hmac.compare_digest(proof.next_active_prefix_sha256,
                                           nxt.durable_prefix_sha256)):
            return ActivationResult(ActivationState.INVALID_PROOF, None, "proof↔prepared incoerentes")

        ws_shards = wal_shards if wal_shards is not None else shards
        ts_shards = tx_shards if tx_shards is not None else shards
        identity = WalIdentity(proof.scope_id, proof.next_active_segment_id)
        seg_key = keyring.get(nxt.key_id)
        if seg_key is None:
            return ActivationResult(ActivationState.INVALID_PROOF, None, "key_id do ACTIVE desconhecido")

        # 2) lease exclusivo: REUSA o carregado pela rotação (nunca ficou livre) ou adquire um NOVO.
        #    C3.2 — ao reutilizar o carregado, exige AUTORIDADE completa: selo + posse OWNED_BY_PREPARED
        #    + `scope_id` casando `proof.scope_id`. Um lease válido de OUTRO scope (ou noutro estado da
        #    máquina) NÃO pode autorizar esta ativação. Um lease fresco nasce e permanece OWNED_BY_STORE
        #    (a matriz congelada não tem aresta STORE→NEW_STORE): ele já é o lease do próprio writer.
        carried = getattr(prepared, "lease", None)
        reuse_carried = carried is not None
        if reuse_carried:
            if not carried.is_sealed() or not carried.held:
                return ActivationResult(ActivationState.INVALID_PROOF, None, "lease inválido no pacote")
            if carried.state != LeaseState.OWNED_BY_PREPARED:
                return ActivationResult(ActivationState.INVALID_PROOF, None,
                                        "lease carregado não está OWNED_BY_PREPARED")
            if carried.scope_id != proof.scope_id:
                return ActivationResult(ActivationState.INVALID_PROOF, None,
                                        "lease carregado é de outro scope")
            lease = carried
        else:
            lease = ScopeWriterLease.acquire(directory, proof.scope_id)
            if lease is None:
                return ActivationResult(ActivationState.WRITER_ACTIVE, None, "outro writer ativo no scope")

        def _abort(state, reason):                  # falha: nenhum worker, lease encerrado
            lease.close()
            return ActivationResult(state, None, reason)

        pub_fd = open_hardened_lock(directory, ".publish.lock")
        token = None
        try:
            fcntl.flock(pub_fd, fcntl.LOCK_EX)
            # 3) CURRENT ainda tem que apontar para a prova
            cst, cur_ptr, _ = read_current(directory, keyring)
            if cst != PublicationState.VALID or not hmac.compare_digest(
                    cur_ptr.publication_sha256, proof.publication_sha256):
                return _abort(ActivationState.STALE_PROOF, "CURRENT mudou após a prova")
            fp("after_current_check")
            # C4.0: reconstrói o cursor publicado DESTE CURRENT (autoridade única do writer). Tem que
            # apontar para a MESMA publicação da prova e para o ACTIVE que vamos abrir.
            cur_state, cursor, cur_why = open_published_cursor(publication_store, object_store, keyring)
            if cur_state != CursorState.VALID:
                return _abort(ActivationState.STALE_PROOF, f"cursor publicado: {cur_why}")
            if (not hmac.compare_digest(cursor.proof.publication_sha256, proof.publication_sha256)
                    or cursor.active_descriptor.segment_id != nxt.segment_id
                    or cursor.active_descriptor.first_seq != nxt.first_seq):
                return _abort(ActivationState.STALE_PROOF, "cursor não casa a prova/ACTIVE")
            # 4) adquire o FD do ACTIVE UMA vez (sem TOCTOU): valida header-only NO PRÓPRIO FD
            wst, token, why = wal_store.acquire_active_writer(
                identity, nxt.durable_prefix_length, nxt.durable_prefix_sha256)
            if wst != WalStoreState.VALID:
                st = (ActivationState.INVALID_ACTIVE if wst != WalStoreState.MISSING
                      else ActivationState.INVALID_ACTIVE)
                return _abort(st, f"acquire_active_writer: {why}")
            # 5) constrói o store PAUSADO com os índices carregados, usando o FD já validado
            R = proof.read_seq
            fd = token.fd
            header_bytes = os.pread(fd, token.header_length, 0)   # do MESMO FD já validado
            if len(header_bytes) != token.header_length:
                return _abort(ActivationState.INVALID_ACTIVE, "leitura curta do header")
            os.lseek(fd, token.header_length, os.SEEK_SET)
            hasher = hashlib.sha256(); hasher.update(header_bytes)
            head = WalHead(proof.scope_id, nxt.segment_id, nxt.first_seq, R, token.header_length,
                           hasher.digest(), STATE_ACTIVE)
            snapshot = L0Snapshot(prepared.carried_index, prepared.carried_txindex, R, head)
            self = cls.__new__(cls)
            self._boot(str(wal_store._active_path(identity)), seg_key, proof.scope_id, nxt.segment_id,
                       fd, snapshot, hasher, R + 1, limits, telemetry_sink, ws_shards, ts_shards,
                       wal_shard_seed, tx_shard_seed, base, nxt.key_id, nxt.prev_segment_digest,
                       start_worker=False, cursor=cursor,
                       pubctx=_PublicationContext(publication_store, wal_store, object_store, keyring))
            # o lease vive junto do writer: o carregado transita PREPARED→NEW_STORE; o fresco já é
            # OWNED_BY_STORE (não há aresta STORE→NEW_STORE na matriz congelada — C3.2)
            self._lease = lease.transfer(LeaseState.OWNED_BY_NEW_STORE) if reuse_carried else lease
            token = None                            # FD pertence ao store agora
        finally:
            fcntl.flock(pub_fd, fcntl.LOCK_UN)
            os.close(pub_fd)
            if token is not None:                   # falhou após adquirir o FD → fecha o FD
                os.close(token.fd)
        # 6) partida do worker cercada: se `Thread.start()` falhar, zero worker, zero FD, zero lease
        try:
            fp("before_start_worker")
            self._start_worker()
        except BaseException:                       # noqa: BLE001
            if self._fd_open:
                os.close(self._fd)
                self._fd_open = False
            self._release_writer_lease()            # fecha o lease (uma vez)
            return ActivationResult(ActivationState.IO_ERROR, None, "falha ao iniciar o worker")
        return ActivationResult(ActivationState.ACTIVATED, self, "ativado")

    @classmethod
    def activate_compacted(cls, prepared, proof, publication_store, wal_store, object_store,
                           keyring, *, shards=0, wal_shards=None, tx_shards=None,
                           wal_shard_seed=0, tx_shard_seed=0, limits=None, telemetry_sink=None,
                           failpoint=None, _base_override=None) -> "ActivationResult":
        """B4.1 — ativa o writer da geração COMPACTADA `G+1` SÓ com prova, sem janela concorrente. Difere
        de `activate_prepared` em dois pontos: (1) reutiliza CONTINUAMENTE o lease
        `OWNED_BY_COMPACTION_PREPARED` embutido no pacote (→ `OWNED_BY_NEW_STORE`), nunca deixando o
        scope livre; (2) o novo writer abre `G+1` com o L0 VAZIO começando em `R+1` — a nova base já
        absorveu todo o estado até `R`, então nada é herdado do índice antigo. Nenhuma falha reabre o
        writer antigo automaticamente: falha = zero worker, lease encerrado, scope sem writer (a
        recuperação é sempre por reabertura explícita a partir do CURRENT publicado).

        FH-00.1: a API operacional NÃO aceita mais uma `base` externa — a base é construída
        EXCLUSIVAMENTE da geração `G+1` aberta a partir do `CURRENT` publicado (autenticada). `_base_override`
        é um gancho de teste PRIVADO e explicitamente NÃO operacional (nenhum caminho publicado o usa)."""
        from horizon_memory._engine.horizon_publication import (
            CursorState, PROOF_SEAL, PublicationState, PublicationStore, open_published_cursor,
            read_current)
        from horizon_memory._engine.horizon_compaction import is_sealed_compaction
        from horizon_memory._engine.horizon_walstore import WalIdentity, WalStoreState
        fp = failpoint or (lambda s: None)
        if not isinstance(publication_store, PublicationStore):
            return ActivationResult(ActivationState.INVALID_PROOF, None,
                                    "publication_store deve ser um PublicationStore")
        if publication_store.scope_id != proof.scope_id:
            return ActivationResult(ActivationState.INVALID_PROOF, None,
                                    "scope do PublicationStore != proof.scope_id")
        directory = Path(publication_store.directory)

        # 1) selos + proof↔prepared (identidade da nova geração e do novo ACTIVE)
        if getattr(proof, "seal", None) is not PROOF_SEAL or not is_sealed_compaction(prepared):
            return ActivationResult(ActivationState.INVALID_PROOF, None, "selo ausente")
        nxt = prepared.next_active_descriptor
        if (proof.read_seq != prepared.read_seq or proof.next_active_first_seq != proof.read_seq + 1
                or proof.generation_id != prepared.generation_id
                or proof.next_active_segment_id != nxt.segment_id
                or proof.next_active_first_seq != nxt.first_seq
                or proof.next_active_prefix_length != nxt.durable_prefix_length
                or not hmac.compare_digest(proof.next_active_prefix_sha256,
                                           nxt.durable_prefix_sha256)):
            return ActivationResult(ActivationState.INVALID_PROOF, None, "proof↔prepared incoerentes")

        ws_shards = wal_shards if wal_shards is not None else shards
        ts_shards = tx_shards if tx_shards is not None else shards
        identity = WalIdentity(proof.scope_id, proof.next_active_segment_id)
        seg_key = keyring.get(nxt.key_id)
        if seg_key is None:
            return ActivationResult(ActivationState.INVALID_PROOF, None, "key_id do ACTIVE desconhecido")

        # 2) lease: REUSA continuamente o OWNED_BY_COMPACTION_PREPARED embutido (nunca ficou livre).
        #    Autoridade completa: selo + posse + scope. Sem lease no pacote → recusa (não adquire fresco:
        #    a compaction sempre carrega seu próprio lease desde begin_compaction).
        carried = getattr(prepared, "lease", None)
        if carried is None:
            return ActivationResult(ActivationState.INVALID_PROOF, None, "pacote de compaction sem lease")
        if not carried.is_sealed() or not carried.held:
            return ActivationResult(ActivationState.INVALID_PROOF, None, "lease inválido no pacote")
        if carried.state != LeaseState.OWNED_BY_COMPACTION_PREPARED:
            return ActivationResult(ActivationState.INVALID_PROOF, None,
                                    "lease carregado não está OWNED_BY_COMPACTION_PREPARED")
        if carried.scope_id != proof.scope_id:
            return ActivationResult(ActivationState.INVALID_PROOF, None, "lease carregado é de outro scope")
        lease = carried

        def _abort(state, reason):                  # falha: nenhum worker, lease encerrado, sem reabrir
            lease.close()
            return ActivationResult(state, None, reason)

        pub_fd = open_hardened_lock(directory, ".publish.lock")
        token = None
        try:
            fcntl.flock(pub_fd, fcntl.LOCK_EX)
            # 3) CURRENT ainda tem que apontar para a prova (a publicação da compaction)
            cst, cur_ptr, _ = read_current(directory, keyring)
            if cst != PublicationState.VALID or not hmac.compare_digest(
                    cur_ptr.publication_sha256, proof.publication_sha256):
                return _abort(ActivationState.STALE_PROOF, "CURRENT mudou após a prova")
            fp("after_current_check")
            # reconstrói o cursor da NOVA geração DESTE CURRENT (autoridade única do writer)
            cur_state, cursor, cur_why = open_published_cursor(publication_store, object_store, keyring)
            if cur_state != CursorState.VALID:
                return _abort(ActivationState.STALE_PROOF, f"cursor publicado: {cur_why}")
            if (not hmac.compare_digest(cursor.proof.publication_sha256, proof.publication_sha256)
                    or cursor.manifest.generation_id != prepared.generation_id
                    or cursor.active_descriptor.segment_id != nxt.segment_id
                    or cursor.active_descriptor.first_seq != nxt.first_seq):
                return _abort(ActivationState.STALE_PROOF, "cursor não casa a prova/ACTIVE de G+1")
            # 4) adquire o FD do ACTIVE vazio de G+1 UMA vez (sem TOCTOU): header-only NO PRÓPRIO FD
            wst, token, why = wal_store.acquire_active_writer(
                identity, nxt.durable_prefix_length, nxt.durable_prefix_sha256)
            if wst != WalStoreState.VALID:
                return _abort(ActivationState.INVALID_ACTIVE, f"acquire_active_writer: {why}")
            # 5) constrói o store PAUSADO com L0 VAZIO (a base G+1 absorveu tudo até R), usando o FD já validado
            R = proof.read_seq
            fd = token.fd
            header_bytes = os.pread(fd, token.header_length, 0)
            if len(header_bytes) != token.header_length:
                return _abort(ActivationState.INVALID_ACTIVE, "leitura curta do header")
            os.lseek(fd, token.header_length, os.SEEK_SET)
            hasher = hashlib.sha256(); hasher.update(header_bytes)
            head = WalHead(proof.scope_id, nxt.segment_id, nxt.first_seq, R, token.header_length,
                           hasher.digest(), STATE_ACTIVE)
            snapshot = L0Snapshot(_make_wal_index(ws_shards, wal_shard_seed),
                                  _make_tx_index(ts_shards, tx_shard_seed), R, head)
            # FH-00.1: a base do novo writer é SEMPRE a geração COMPACTADA G+1 aberta a partir do CURRENT
            # publicado (autenticada) — carrega a versão vigente (preflight base-aware) e a DedupTable
            # (dedup-com-base no ingresso). Nenhuma base externa é aceita pela API operacional; só o
            # gancho de teste privado `_base_override` (não operacional) pode substituí-la.
            if _base_override is not None:
                gen_base = _base_override
            else:
                from horizon_memory._engine.horizon_manifest import (
                    OpenGenerationState, ReadView, open_generation)
                from horizon_memory._engine.horizon_store import BundleBaseView
                og = open_generation(cursor.manifest_blob, object_store, wal_store, keyring)
                if og.state != OpenGenerationState.VALID:
                    return _abort(ActivationState.INVALID_ACTIVE, f"base G+1 ilegível: {og.reason}")
                gh = og.handle
                gen_base = BundleBaseView(ReadView(gh.generation_id, gh.scope_id, gh.base_seq, gh.read_seq),
                                          gh.bundle, base_dedup=gh.dedup)
            self = cls.__new__(cls)
            self._boot(str(wal_store._active_path(identity)), seg_key, proof.scope_id, nxt.segment_id,
                       fd, snapshot, hasher, R + 1, limits, telemetry_sink, ws_shards, ts_shards,
                       wal_shard_seed, tx_shard_seed, gen_base, nxt.key_id, nxt.prev_segment_digest,
                       start_worker=False, cursor=cursor,
                       pubctx=_PublicationContext(publication_store, wal_store, object_store, keyring),
                       u8_domain=True)                  # B4.2 §4: domínio u8 congelado no ingresso
            # lease contínuo: COMPACTION_PREPARED → NEW_STORE (o writer sucessor detém o lease)
            self._lease = lease.transfer(LeaseState.OWNED_BY_NEW_STORE)
            token = None                            # FD pertence ao store agora
        finally:
            fcntl.flock(pub_fd, fcntl.LOCK_UN)
            os.close(pub_fd)
            if token is not None:
                os.close(token.fd)
        # 6) partida do worker cercada: falha → zero worker, zero FD, zero lease (sem reabrir o antigo)
        try:
            fp("before_start_worker")
            self._start_worker()
        except BaseException:                       # noqa: BLE001
            if self._fd_open:
                os.close(self._fd)
                self._fd_open = False
            self._release_writer_lease()
            return ActivationResult(ActivationState.IO_ERROR, None, "falha ao iniciar o worker")
        return ActivationResult(ActivationState.ACTIVATED, self, "ativado")

    # -------- worker --------
    def _run(self):
        while True:
            with self._cv:
                while not self._queue and not self._stop:
                    self._cv.wait()
                if not self._queue:            # só sai vazio (drenou tudo)
                    return
                self._wait_window_locked()
                batch = self._drain_locked()
                snapshot = self._snapshot
                hasher = self._hasher
            if not batch:
                continue
            self._commit_batch(batch, snapshot, hasher)

    def _wait_window_locked(self):
        """Janela: espera a partir da admissão do request mais antigo; fecha por tempo, quantidade
        ou bytes (chamado com `_cv` adquirido)."""
        if self.limits.window_ns <= 0 or self._stop:
            return
        deadline = self._queue[0].admitted_ns + self.limits.window_ns
        while not self._stop and len(self._queue) < self.limits.max_batch_commands:
            now = time.monotonic_ns()
            if now >= deadline:
                break
            if self._queue_bytes >= self.limits.max_batch_bytes:   # O(1); próximo já encheria
                break
            self._cv.wait(timeout=(deadline - now) / 1e9)

    def _drain_locked(self):
        now = time.monotonic_ns()
        batch, bytes_ = [], 0
        while self._queue and len(batch) < self.limits.max_batch_commands:
            req = self._queue[0]
            if req.cancelled:                         # cancelamento venceu sob o mesmo lock
                self._queue.popleft()
                self._queue_bytes -= req.size
                req._finish(NOT_ACCEPTED, None)
                continue
            if batch and bytes_ + req.size > self.limits.max_batch_bytes:  # nunca exceder o limite
                break
            self._queue.popleft()
            self._queue_bytes -= req.size
            req.state = "CLAIMED"
            req.claimed_ns = now
            batch.append(req)
            bytes_ += req.size
        return batch

    def _poison_locked_then_finish(self, batch):
        """Envenena, esvazia a fila (sem novos writes) e termina o worker."""
        with self._cv:
            self._poisoned = True
            self._stop = True
            pending = list(self._queue)
            self._queue.clear()
            self._queue_bytes = 0
        for r in batch:                               # já reivindicados → incerto
            r._finish(COMMIT_UNKNOWN, None)
        for r in pending:                             # nunca reivindicados → seguro reenviar
            r._finish(NOT_ACCEPTED, None)
        self._release_writer_lease()                  # poison libera o lease exclusivo (uma vez)

    def _commit_batch(self, batch, snapshot, hasher):
        """Despacha: writer ATIVADO (com contexto de publicação) usa o commit PUBLICADO do C4.3;
        writer legado (só memória) usa o caminho antigo (swap de snapshot sem publicar CURRENT)."""
        if self._pubctx is not None:
            self._commit_batch_published(batch, snapshot, hasher)
        else:
            self._commit_batch_legacy(batch, snapshot, hasher)

    # -------- C4.3: commit publicado (fecha o caminho de ACK) --------
    def _finish_receipts(self, batch, plan):
        """Entrega as respostas do batch. Garantia correta (C4.3): TODAS as respostas SÓ COMEÇAM após o
        commit durável (CURRENT publicado + swap). A entrega em si não é atômica entre vários clientes —
        um crash aqui pode ACKar uma parte e perder a resposta da outra; como TODAS as operações já são
        duráveis e os retries são idempotentes por `operation_id`, cada resposta perdida é resolvida por
        deduplicação (`DEDUP_REPLAY`). O C5 injeta crash ENTRE respostas e aceita entrega parcial."""
        by_ticket = {rc.ticket: rc for rc in plan.receipts}
        for i, r in enumerate(batch):
            self.failpoint(f"receipt_{i}")     # C5.2: crash ENTRE respostas (após k entregues, antes de k+1)
            rc = by_ticket[r.cmd.enqueue_ticket]
            r._finish(rc.status, rc.wal_seq)

    def _poison_pre_wal(self, batch):
        """Falha ANTES de qualquer escrita no WAL: nada foi tocado → o batch reivindicado é SEGURO
        reenviar (NOT_ACCEPTED), não COMMIT_UNKNOWN. Ainda assim envenena (estado suspeito)."""
        with self._cv:
            self._poisoned = True
            self._stop = True
            pending = list(self._queue)
            self._queue.clear()
            self._queue_bytes = 0
        for r in batch:
            r._finish(NOT_ACCEPTED, None)
        for r in pending:
            r._finish(NOT_ACCEPTED, None)
        self._release_writer_lease()

    def _check_batch_preconditions(self, published, snapshot, plan):
        """As seis precondições do C4.3 — impedem combinar um `BatchPlan` de OUTRO snapshot com o cursor
        vigente. (ok, reason)."""
        cursor = published.cursor
        if cursor is None:
            return (False, "writer publicado sem cursor")
        act = cursor.active_descriptor
        head = snapshot.wal_head
        if snapshot.visible_through_seq != cursor.read_seq:
            return (False, "snapshot.visible_through != cursor.read_seq")
        if (head.segment_id != act.segment_id or head.first_seq != act.first_seq
                or head.byte_length != act.durable_prefix_length
                or not hmac.compare_digest(head.prefix_digest, act.durable_prefix_sha256)):
            return (False, "snapshot.wal_head não casa o ACTIVE do cursor")
        if plan.expected_first_seq != cursor.read_seq + 1:
            return (False, "plan.expected_first_seq != cursor.read_seq + 1")
        if plan.candidate_snapshot.wal_head != plan.candidate_head:
            return (False, "candidate_snapshot.wal_head != candidate_head")
        if plan.candidate_head.byte_length != act.durable_prefix_length + plan.byte_count:
            return (False, "candidate_byte_length != source + plan.byte_count")
        applied_seqs = [rc.wal_seq for rc in plan.receipts if rc.status == APPLIED]
        expected = list(range(cursor.read_seq + 1, plan.candidate_head.durable_through_seq + 1))
        if applied_seqs != expected:
            return (False, "seqs APPLIED não são contíguas e exatas no intervalo do intent")
        return (True, "ok")

    def _commit_batch_published(self, batch, snapshot, hasher):
        from horizon_memory._engine.horizon_publication import (
            PublishState, prepare_batch_publication, publish_batch)
        ctx = self._pubctx
        prepare_start = time.monotonic_ns()
        published_before = self._published_state           # captura o estado inteiro (snapshot+cursor)
        plan = prepare_batch(snapshot, hasher, [r.cmd for r in batch], self.key,
                             self.scope_id, self.segment_id, base=self._base, u8_domain=self._u8_domain)
        prepare_end = time.monotonic_ns()

        ok_pre, _why = self._check_batch_preconditions(published_before, snapshot, plan)
        if not ok_pre:
            self._poison_pre_wal(batch)                    # ANTES do WAL: não modifica estado
            return
        pst, intent, _ = prepare_batch_publication(published_before, plan, ctx.keyring)
        if pst == PublishState.NO_PUBLICATION_REQUIRED:    # zero APPLIED: sem WAL/fsync/publish/swap
            self._finish_receipts(batch, plan)
            self._batch_id += 1
            self.stats["batches"] += 1
            return
        if pst != PublishState.PUBLISHED or intent is None:
            self._poison_pre_wal(batch)                    # falha ANTES do WAL (ex.: overflow)
            return

        # failpoint 1: ANTES de qualquer tentativa física — nenhum write iniciado ⇒ NOT_ACCEPTED
        try:
            self.failpoint("c43_before_wal_write")
        except BaseException:                              # noqa: BLE001
            self._poison_pre_wal(batch)                    # pré-WAL: seguro reenviar
            return
        # A PARTIR DAQUI a entrada em `commit_prepared_durable` é o ponto de INCERTEZA: qualquer exceção
        # (a escrita pode ter começado) ⇒ COMMIT_UNKNOWN + POISONED.
        try:
            proof, write_end, fsync_end = commit_prepared_durable(self._fd, plan, intent, self.failpoint)
        except BaseException:                              # noqa: BLE001
            self._poison_locked_then_finish(batch)
            return
        if proof is None:
            if write_end == 0:                             # NADA foi escrito (fstat de posição recusou)
                self._poison_pre_wal(batch)                # ainda pré-WAL → NOT_ACCEPTED
            else:                                          # a escrita começou e divergiu → incerto
                self._poison_locked_then_finish(batch)
            return

        # publicação por CAS (falha pós-fsync ⇒ COMMIT_UNKNOWN + POISONED)
        try:
            rst, new_cursor, _r = publish_batch(ctx.publication_store, ctx.object_store, ctx.wal_store,
                                                ctx.keyring, published_before.cursor, intent, proof,
                                                authority=self._lease, failpoint=self.failpoint)
        except BaseException:                              # noqa: BLE001
            self._poison_locked_then_finish(batch)
            return
        if rst not in (PublishState.PUBLISHED, PublishState.ALREADY_PUBLISHED):
            self._poison_locked_then_finish(batch)
            return
        try:
            self.failpoint("c43_after_current_durable_pre_swap")   # failpoint 8 (CURRENT durável)
        except BaseException:                              # noqa: BLE001
            self._poison_locked_then_finish(batch)
            return

        # SWAP ÚNICO — snapshot e cursor avançam JUNTOS. Confirma que ninguém trocou o estado no meio.
        with self._cv:
            if self._published_state is not published_before:
                self._poisoned = True
                self._stop = True
        if self._poisoned:
            self._poison_locked_then_finish(batch)
            return
        with self._cv:
            self._published_state = PublishedStoreState(plan.candidate_snapshot, new_cursor)
            self._hasher = plan.candidate_hasher
        publish_ns = time.monotonic_ns()
        try:
            self.failpoint("c43_after_swap_pre_ack")       # failpoint 9 (trocado, antes do ACK)
        except BaseException:                              # noqa: BLE001
            self._poison_locked_then_finish(batch)
            return

        # ACK só DEPOIS do swap, e todos de uma vez (sem failpoints entre respostas)
        self._finish_receipts(batch, plan)
        applied = intent.applied_count
        self._batch_id += 1
        self.stats["batches"] += 1
        self.stats["applied"] += applied
        self.stats["batches_with_frames"] += 1
        self.stats["fsync_count"] += 1
        if self._telemetry_sink is not None:
            self._telemetry_sink(BatchMetric(
                batch_id=self._batch_id, command_count=len(batch), applied_count=applied,
                byte_count=plan.byte_count, oldest_admitted_ns=batch[0].admitted_ns,
                claimed_ns=batch[0].claimed_ns, prepare_start_ns=prepare_start,
                prepare_end_ns=prepare_end, write_end_ns=write_end, fsync_end_ns=fsync_end,
                publish_ns=publish_ns, receipts_done_ns=time.monotonic_ns()))

    def _commit_batch_legacy(self, batch, snapshot, hasher):
        prepare_start = time.monotonic_ns()
        plan = prepare_batch(snapshot, hasher, [r.cmd for r in batch], self.key,
                             self.scope_id, self.segment_id, base=self._base, u8_domain=self._u8_domain)
        prepare_end = time.monotonic_ns()
        try:
            self.failpoint("prepare_done")
            durable, write_end, fsync_end = commit_prepared(self._fd, plan, self.failpoint)
        except Exception:                             # erro de dispositivo/failpoint no físico
            self._poison_locked_then_finish(batch)
            return                                    # POISONED → worker NÃO faz mais writes
        with self._cv:                                # swap atômico (ponto de linearização visível)
            if durable:
                self._published_state = PublishedStoreState(plan.candidate_snapshot,
                                                            self._published_state.cursor)
                self._hasher = plan.candidate_hasher
        publish_ns = time.monotonic_ns()
        try:
            self.failpoint("after_swap")
        except Exception:
            self._poison_locked_then_finish(batch)
            return
        by_ticket = {rc.ticket: rc for rc in plan.receipts}   # ACK só depois do swap
        for r in batch:
            rc = by_ticket[r.cmd.enqueue_ticket]
            r._finish(rc.status, rc.wal_seq)
        # contadores agregados (sempre) + telemetria por batch (opcional)
        applied = sum(1 for rc in plan.receipts if rc.status == APPLIED)
        self._batch_id += 1
        self.stats["batches"] += 1
        self.stats["applied"] += applied
        if durable:
            self.stats["batches_with_frames"] += 1
            self.stats["fsync_count"] += 1
        if self._telemetry_sink is not None:
            self._telemetry_sink(BatchMetric(
                batch_id=self._batch_id, command_count=len(batch), applied_count=applied,
                byte_count=plan.byte_count, oldest_admitted_ns=batch[0].admitted_ns,
                claimed_ns=batch[0].claimed_ns, prepare_start_ns=prepare_start,
                prepare_end_ns=prepare_end, write_end_ns=write_end, fsync_end_ns=fsync_end,
                publish_ns=publish_ns, receipts_done_ns=time.monotonic_ns()))
