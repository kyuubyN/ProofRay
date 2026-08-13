# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Autoridade ÚNICA de recuperação da Horizon Memory (V23-C5 / FH-01).

Depois de uma morte real de processo, o ÚNICO estado recuperado é o apontado pelo `CURRENT` durável.
Nenhuma cauda física não publicada (bytes escritos e até `fsync`ados no ACTIVE, mas nunca cobertos por um
avanço do `CURRENT`) pode virar memória reconhecida. O recovery:

  1. abre o `CURRENT` com limite + autenticação (`open_published_cursor`);
  2. reconstrói `PublicationRecord` → manifesto → geração INTEIRA (`open_generation`), que replaya SÓ o
     prefixo WAL declarado pelo manifesto (a cauda além de `durable_prefix_length` é ignorada na leitura);
  3. compara o ACTIVE FÍSICO com o `ActivePrefix` publicado e classifica os bytes posteriores como cauda
     NÃO publicada — nunca aplicada à ReadView;
  4. só depois de provar a autoridade do cursor, prepara o truncamento da cauda ao prefixo publicado
     (`ftruncate` + `fsync(fd)` + `fsync_dir`), idempotente;
  5. reconstrói L0/TxIdIndex/dedup pela MESMA autoridade de replay (o handle da geração);
  6. produz um `WriterResumePlan` para retomar o writer em `R+1`.

`CURRENT` ausente é genesis não inicializado (`NO_CURRENT`). `CURRENT` ilegível/corrompido NUNCA autoriza
procurar e abrir uma geração anterior em silêncio — devolve `ABSTAIN_CURRENT` (sem fallback ao pai).

`recover()` é ESTRITAMENTE READ-ONLY (C5.1) — jamais trunca ou muta o diretório; é seguro chamá-lo de
qualquer processo, a qualquer momento, sem lease. Toda mutação (o truncamento da cauda) acontece só dentro
de `resume_from_recovery`, que adquire o `ScopeWriterLease` ANTES de qualquer escrita, RELÊ o `CURRENT`
sob o lease e recusa (`STALE_RECOVERY`, sem truncar um byte) se o cursor avançou. Assim o próprio
mecanismo de recuperação nunca vira origem de perda de dados: um plano de recovery obsoleto não pode
truncar um ACTIVE que outro writer já avançou.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from horizon_memory._engine.horizon_durability import fsync_dir
from horizon_memory._engine.horizon_manifest import (
    DEFAULT_GENERATION_LIMITS, OpenGenerationState, open_generation)
from horizon_memory._engine.horizon_publication import (
    CursorState, DEFAULT_PUBLICATION_LIMITS, PublicationStore, open_published_cursor)
from horizon_memory._engine.horizon_walstore import WalIdentity


class RecoveryState(Enum):
    RECOVERED = 0
    NO_CURRENT = 1            # CURRENT ausente → genesis não inicializado (não é erro)
    ABSTAIN_CURRENT = 2       # CURRENT ilegível/corrompido → abstém-se, nunca abre o pai
    MISSING_REQUIRED = 3      # artefato obrigatório da geração ausente
    CORRUPT = 4               # geração/segmento fisicamente incoerente
    INCOMPATIBLE = 5          # versão de formato não suportada
    RESOURCE_LIMIT = 6        # excede limites declarados (orçamento, não corrupção)


@dataclass(frozen=True)
class WriterResumePlan:
    """Plano determinístico de retomada do writer sobre o ACTIVE vigente. `unpublished_tail_bytes` é o
    excedente físico além do prefixo publicado — bytes que o recovery jamais aplica e que o truncamento
    (reconciliação) remove antes de retomar em `resume_seq = R+1`."""
    identity: WalIdentity
    resume_seq: int                  # R+1
    published_prefix_length: int
    published_prefix_sha256: bytes
    physical_length: int
    unpublished_tail_bytes: int
    key_id: int
    tail_reconciled: bool            # True se a cauda já foi truncada e persistida


@dataclass(frozen=True)
class RecoveryResult:
    state: RecoveryState
    generation_handle: object        # GenerationHandle | None (ReadView autoritativa)
    published_cursor: object         # PublishedCursor | None
    writer_resume_plan: WriterResumePlan | None
    reason_code: str


_GEN_STATE_MAP = {
    OpenGenerationState.REQUIRED_MISSING: RecoveryState.MISSING_REQUIRED,
    OpenGenerationState.CORRUPT: RecoveryState.CORRUPT,
    OpenGenerationState.INCOMPATIBLE: RecoveryState.INCOMPATIBLE,
    OpenGenerationState.RESOURCE_LIMIT: RecoveryState.RESOURCE_LIMIT,
    OpenGenerationState.COVERAGE_ERROR: RecoveryState.CORRUPT,
}


def _physical_active_length(wal_store, identity) -> int | None:
    """Comprimento físico do ACTIVE (`S_ISREG` obrigatório) ou None se ausente/irregular."""
    path = wal_store._active_path(identity)
    try:
        st = os.stat(str(path))
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return st.st_size


def recover(publication_store, wal_store, object_store, keyring, *, limits=None,
            gen_limits=None) -> RecoveryResult:
    """Autoridade única de recovery, ESTRITAMENTE READ-ONLY (C5.1): observa o `CURRENT`, reconstrói a
    geração e CLASSIFICA a cauda não publicada — mas NUNCA trunca nem muta o diretório. Nunca lança. O
    truncamento é exclusivo do `resume_from_recovery` (sob o lease). Devolve um `RecoveryResult` tipado."""
    lim = limits or DEFAULT_PUBLICATION_LIMITS
    glim = gen_limits or DEFAULT_GENERATION_LIMITS

    # 1) CURRENT → cursor publicado (autoridade). Corrupção/limite jamais viram fallback ao pai.
    cst, cursor, why = open_published_cursor(publication_store, object_store, keyring, limits=lim)
    if cst == CursorState.NO_CURRENT:
        return RecoveryResult(RecoveryState.NO_CURRENT, None, None, None, "CURRENT ausente")
    if cst == CursorState.RESOURCE_LIMIT:
        return RecoveryResult(RecoveryState.RESOURCE_LIMIT, None, None, None, f"CURRENT: {why}")
    if cst != CursorState.VALID:
        return RecoveryResult(RecoveryState.ABSTAIN_CURRENT, None, None, None, f"CURRENT: {why}")

    # 2/3) geração inteira a partir do manifesto do cursor — replay SÓ do prefixo publicado
    og = open_generation(cursor.manifest_blob, object_store, wal_store, keyring,
                         limits=lim.artifact, gen_limits=glim)
    if og.state != OpenGenerationState.VALID:
        return RecoveryResult(_GEN_STATE_MAP.get(og.state, RecoveryState.CORRUPT), None, cursor, None,
                              f"geração: {og.reason}")
    handle = og.handle

    # 4/5) compara o ACTIVE físico com o prefixo publicado; classifica a cauda não publicada
    active = cursor.active_descriptor
    identity = WalIdentity(cursor.manifest.scope_id, active.segment_id)
    published_length = active.durable_prefix_length
    published_sha = active.durable_prefix_sha256
    physical = _physical_active_length(wal_store, identity)
    if physical is None:
        return RecoveryResult(RecoveryState.MISSING_REQUIRED, None, cursor, None, "ACTIVE físico ausente")
    if physical < published_length:
        # o físico é MENOR que o prefixo durável publicado: durabilidade violada → corrupção fail-closed
        return RecoveryResult(RecoveryState.CORRUPT, None, cursor, None,
                              "ACTIVE físico < prefixo publicado (durabilidade violada)")
    tail = physical - published_length

    plan = WriterResumePlan(identity, cursor.read_seq + 1, published_length, published_sha, physical,
                            tail, active.key_id, False)
    return RecoveryResult(RecoveryState.RECOVERED, handle, cursor, plan, "ok")


def _verify_and_truncate_fd(fd: int, active_dir, published_length: int, published_sha: bytes,
                            failpoint, *, best_effort: bool = False):
    """Trunca o ACTIVE ao prefixo PUBLICADO NO MESMO FD já aberto e validado (C5.1) — nenhuma reabertura
    por caminho. PROVA a autoridade antes: só trunca se os primeiros `published_length` bytes casarem
    `published_sha`. Sequência com failpoints (para a matriz de crash): `before_ftruncate` → `ftruncate`
    → `after_ftruncate` → `fsync(fd)` → `after_fsync_fd` → `fsync_dir` → `after_fsync_dir`. Idempotente
    (arquivo já no tamanho → no-op de escrita). (ok, reason)."""
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return (False, "ACTIVE não é arquivo regular")
        if st.st_size < published_length:
            return (False, "físico < prefixo publicado")
        prefix = _pread_exact(fd, published_length)
        if len(prefix) != published_length or hashlib.sha256(prefix).digest() != published_sha:
            return (False, "prefixo físico não casa o sha publicado")   # nunca trunca sem prova
        if st.st_size != published_length:
            failpoint("before_ftruncate")
            os.ftruncate(fd, published_length)
            failpoint("after_ftruncate")
            os.fsync(fd)
            failpoint("after_fsync_fd")
            fsync_dir(active_dir, best_effort=best_effort)
            failpoint("after_fsync_dir")
        else:
            # já no prefixo publicado — a durabilidade da cauda ausente já foi provada; nada a truncar
            failpoint("tail_already_absent")
    except OSError as e:
        return (False, f"io errno {getattr(e, 'errno', '?')}")
    return (True, "ok")


def _pread_exact(fd: int, n: int) -> bytes:
    data = bytearray()
    off = 0
    while off < n:
        try:
            chunk = os.pread(fd, n - off, off)
        except InterruptedError:
            continue
        if not chunk:
            break
        data += chunk
        off += len(chunk)
    return bytes(data)


def resume_from_recovery(recovery_result, publication_store, wal_store, object_store, keyring, *,
                         shards: int = 0, wal_shard_seed: int = 0, tx_shard_seed: int = 0,
                         limits=None, telemetry_sink=None, u8_domain: bool = False, failpoint=None):
    """Retoma um writer VIVO sobre o ACTIVE vigente. C5.1 — ordem SEGURA contra corrida destrutiva:

      1. adquire o `ScopeWriterLease` ANTES de qualquer mutação (se ocupado → `WRITER_ACTIVE`);
      2. SOB o lease, RE-EXECUTA `recover()` (autoridade fresca) e relê o `CURRENT`;
      3. exige que o `publication_sha256` fresco seja IGUAL ao do `recovery_result` passado — o objeto do
         chamador é só a INTENÇÃO; tudo é revalidado. Se o cursor avançou → `STALE_RECOVERY`, ZERO bytes
         truncados;
      4. abre e valida o ACTIVE num ÚNICO FD; verifica o prefixo e TRUNCA nesse MESMO FD
         (`ftruncate → fsync(fd) → fsync_dir`), com failpoints por estágio;
      5. transfere esse FD direto ao writer, sem reabertura por caminho.

    Um plano de recovery obsoleto NUNCA trunca um ACTIVE que outro writer já avançou. Falha ⇒ zero worker,
    lease encerrado (sem reabrir nada em silêncio)."""
    from horizon_memory._engine.horizon_batch import (
        ActivationResult, ActivationState, GroupCommitStore, L0Snapshot, ScopeWriterLease,
        WalHead, _PublicationContext)
    from horizon_memory._engine.horizon_store import BundleBaseView
    from horizon_memory._engine.horizon_manifest import ReadView
    from horizon_memory._engine.horizon_wal import STATE_ACTIVE
    fp = failpoint or (lambda s: None)

    # C5.2 §1 — a AUTORIDADE (scope/diretório do lease) vem EXCLUSIVAMENTE do PublicationStore validado;
    # o `recovery_result` do chamador só fornece a INTENÇÃO (o digest esperado), NUNCA o domínio de
    # autoridade. Um resultado forjado com o SHA certo mas scope diferente não pode mutar o scope real.
    if not isinstance(publication_store, PublicationStore):
        return ActivationResult(ActivationState.INVALID_PROOF, None,
                                "publication_store deve ser um PublicationStore")
    if recovery_result.state != RecoveryState.RECOVERED or recovery_result.published_cursor is None:
        return ActivationResult(ActivationState.INVALID_PROOF, None,
                                f"recovery não RECOVERED: {recovery_result.state.name}")
    intended_pub_sha = recovery_result.published_cursor.proof.publication_sha256   # só a INTENÇÃO
    scope = publication_store.scope_id                                             # AUTORIDADE do store

    # 1) LEASE PRIMEIRO — nenhuma mutação antes disto; scope derivado do PublicationStore
    lease = ScopeWriterLease.acquire(Path(publication_store.directory), scope)
    if lease is None:
        return ActivationResult(ActivationState.WRITER_ACTIVE, None, "outro writer ativo no scope")

    def _abort(state, reason):
        lease.close()
        return ActivationResult(state, None, reason)

    # 2/3) revalida SOB o lease: recovery fresco + mesmo publication_sha256, senão STALE_RECOVERY (sem mutar)
    fp("after_lease_before_revalidate")
    fresh = recover(publication_store, wal_store, object_store, keyring, limits=limits)
    if fresh.state != RecoveryState.RECOVERED or fresh.published_cursor is None:
        return _abort(ActivationState.STALE_RECOVERY, f"recovery fresco não RECOVERED: {fresh.state.name}")
    cursor = fresh.published_cursor
    handle = fresh.generation_handle
    plan = fresh.writer_resume_plan
    if cursor.proof.publication_sha256 != intended_pub_sha:
        return _abort(ActivationState.STALE_RECOVERY, "CURRENT avançou entre recovery e resume")
    identity = plan.identity
    seg_key = keyring.get(plan.key_id)
    if seg_key is None:
        return _abort(ActivationState.INVALID_PROOF, "key_id do ACTIVE desconhecido")

    # 4) ÚNICO FD: abre o ACTIVE, verifica o prefixo e TRUNCA nesse mesmo FD (sem reabertura por caminho)
    path = wal_store._active_path(identity)
    flags = os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(str(path), flags)
    except OSError as e:
        return _abort(ActivationState.INVALID_ACTIVE, f"open ACTIVE errno {getattr(e, 'errno', '?')}")
    try:
        ok, treason = _verify_and_truncate_fd(fd, path.parent, plan.published_prefix_length,
                                              plan.published_prefix_sha256, fp)
        if not ok:
            os.close(fd)
            return _abort(ActivationState.INVALID_ACTIVE, f"reconciliação: {treason}")
        R = cursor.read_seq
        prefix = _pread_exact(fd, plan.published_prefix_length)
        if len(prefix) != plan.published_prefix_length:
            os.close(fd)
            return _abort(ActivationState.INVALID_ACTIVE, "leitura curta do prefixo publicado")
        os.lseek(fd, plan.published_prefix_length, os.SEEK_SET)
        hasher = hashlib.sha256(); hasher.update(prefix)
        head = WalHead(cursor.manifest.scope_id, identity.segment_id, cursor.active_descriptor.first_seq,
                       R, plan.published_prefix_length, hasher.digest(), STATE_ACTIVE)
        snapshot = L0Snapshot(handle.l0, handle.txindex, R, head)
        base = BundleBaseView(ReadView(handle.generation_id, handle.scope_id, handle.base_seq,
                                       handle.read_seq), handle.bundle, base_dedup=handle.dedup)
        # 5) transfere o MESMO FD ao writer, sem reabertura por caminho
        self = GroupCommitStore.__new__(GroupCommitStore)
        self._boot(str(path), seg_key, cursor.manifest.scope_id, identity.segment_id, fd, snapshot,
                   hasher, R + 1, limits, telemetry_sink, shards, shards, wal_shard_seed, tx_shard_seed,
                   base, cursor.active_descriptor.key_id, cursor.active_descriptor.prev_segment_digest,
                   start_worker=False, cursor=cursor,
                   pubctx=_PublicationContext(publication_store, wal_store, object_store, keyring),
                   u8_domain=u8_domain)
        self._lease = lease
    except BaseException:                                # noqa: BLE001
        with _suppress():
            os.close(fd)
        lease.close()
        return ActivationResult(ActivationState.IO_ERROR, None, "falha ao montar o writer retomado")
    try:
        self._start_worker()
    except BaseException:                                # noqa: BLE001
        if self._fd_open:
            os.close(self._fd)
            self._fd_open = False
        self._release_writer_lease()
        return ActivationResult(ActivationState.IO_ERROR, None, "falha ao iniciar o worker retomado")
    return ActivationResult(ActivationState.ACTIVATED, self, "retomado")


def _suppress():
    import contextlib
    return contextlib.suppress(OSError)
