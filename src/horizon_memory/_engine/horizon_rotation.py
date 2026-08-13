# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Preparação de rotação de segmento do WAL (V23-B3-4).

Rotação é um PROTOCOLO DE PREPARAÇÃO, não uma troca imediata para um writer novo: um ACK só pode
existir num segmento cuja cobertura já esteja autorizada por um manifesto publicado. Por isso o
B3-4 apenas PREPARA um pacote; habilitar novos ACKs (ativar o próximo ACTIVE) pertence ao V23-C.

Máquina de estados:
    WRITABLE → FENCED → DRAINED → OLD_SEALED → SEALED_PUBLISHED → NEXT_ACTIVE_CREATED
             → ROTATION_PREPARED
Qualquer falha física depois do FENCE leva a POISONED — nunca volta a WRITABLE automaticamente.

Protocolo (`prepare_rotation`):
  1. FENCE de admissão (sob o lock da fila): novos `submit` → NOT_ACCEPTED (seguro reenviar); os já
     admitidos drenam. 2. DRENAR e capturar atomicamente (L0, `wal_head`, último ticket); fechar o FD
     do writer antigo; abortar se POISONED. 3. SELAR pelo `WalStore.seal_active` (prova o prefixo
     exato, `last_seq==R`, sem frame posterior; footer idempotente; footer rasgado reparado; frame
     posterior recusado). 4. PUBLICAR o SEALED (objeto imutável). 5. CRIAR o próximo ACTIVE
     (header-only, `segment_id+1`, `first_seq=R+1`, `previous=sealed[:16]`), idempotente. 6. Devolver
     um `RotationPrepared` com os índices CARREGADOS — sem iniciar um writer que aceite escrita.

Regra de visibilidade congelada para o V23-C: o novo ACTIVE só aceita operações depois que o V23-C
publicar um CURRENT que o contenha. Evita ACK fora da cobertura autorizada após crash.
"""

from __future__ import annotations

import hashlib
import hmac
import fcntl
import os
import struct
from dataclasses import dataclass, replace
from enum import IntEnum

from horizon_memory._engine.horizon_batch import LeaseState, ROTATION_NOT_NEEDED as _NOT_NEEDED

_LEASE_OWNED_BY_PREPARED = LeaseState.OWNED_BY_PREPARED
from horizon_memory._engine.horizon_manifest import WalSegmentDescriptor
from horizon_memory._engine.horizon_walstore import (
    ActivePrefixExpectation,
    WalIdentity,
    WalStoreState,
)
from horizon_memory._engine.horizon_wal import (
    MAX_SEQ,
    STATE_ACTIVE,
    STATE_SEALED,
    encode_segment_header,
)

_U32_MAX = 0xFFFFFFFF
_ZERO32 = b"\x00" * 32
_ROTATION_SEAL = object()      # selo privado do pacote preparado
_ROTATION_DOMAIN = b"HORIZON-ROTATION-PREPARED-v1"


def is_sealed_prepared(prepared) -> bool:
    """Selo privado + digest canônico: `dataclasses.replace` preservando selo não forja o pacote."""
    if getattr(prepared, "seal", None) is not _ROTATION_SEAL:
        return False
    try:
        stored = getattr(prepared, "prepared_digest", None)
        return isinstance(stored, (bytes, bytearray)) and hmac.compare_digest(
            bytes(stored), rotation_prepared_digest(prepared))
    except (AttributeError, TypeError, ValueError, struct.error):
        return False


class RotationState(IntEnum):
    ROTATION_PREPARED = 0
    FAILED = 1                 # falha lógica (seal/publish/create) — sem writer novo aceitando
    POISONED = 2               # falha física após o fence, ou writer envenenado
    ROTATION_NOT_NEEDED = 3    # snapshot vazio + fila vazia: nada a selar (nenhum SEALED vazio)


@dataclass(frozen=True)
class RotationPrepared:
    old_sealed_descriptor: WalSegmentDescriptor
    next_active_descriptor: WalSegmentDescriptor
    carried_index: object              # L0 (WalIndex) carregado — nunca se começa vazio
    carried_txindex: object            # TxIdIndex carregado (dedup por operation_id preservado)
    read_seq: int                      # R publicado por esta rotação
    prepared_digest: bytes             # digest de descriptors+R (lease/índices não serializados)
    seal: object                       # _ROTATION_SEAL
    lease: object = None               # ScopeWriterLease transferível (C3.1) ou None (genesis)


@dataclass(frozen=True)
class RotationResult:
    state: RotationState
    prepared: RotationPrepared | None
    reason: str


def _descriptor_bytes(d) -> bytes:
    return struct.pack("<IIQIBQ32sQ32s16sI", d.ordinal, d.segment_id, d.first_seq,
                       d.record_count, d.status, d.durable_prefix_length,
                       d.durable_prefix_sha256, d.sealed_object_length,
                       d.sealed_object_sha256, d.prev_segment_digest, d.key_id)


def rotation_prepared_digest(prepared) -> bytes:
    h = hashlib.sha256()
    h.update(_ROTATION_DOMAIN)
    h.update(struct.pack("<Q", prepared.read_seq))
    h.update(_descriptor_bytes(prepared.old_sealed_descriptor))
    h.update(_descriptor_bytes(prepared.next_active_descriptor))
    return h.digest()


def rotation_registry_name(prepared) -> str:
    return f"rot-{bytes(prepared.prepared_digest).hex()}"


def prepare_rotation(store, wal_store, current_active_descriptor=None, *,
                     timeout: float = 5.0) -> RotationResult:
    """Prepara a rotação do segmento ACTIVE de `store` no `wal_store`. O descriptor ACTIVE vigente
    (usado para casar identidade↔`WalHead` e para os ORDINAIS: `old.ordinal = cur.ordinal`,
    `next.ordinal = cur.ordinal+1`) vem, para um writer REAL, do CURSOR PUBLICADO do store (C4.0):
    `store.capture_published_state().cursor.active_descriptor`. Se o store carrega um cursor, o
    argumento `current_active_descriptor` é IGNORADO — o descriptor ACTIVE externo não participa mais
    da rotação de um writer publicado. `current_active_descriptor` só é usado por stores SEM cursor
    (scaffolding de mecânica pura); sem cursor E sem argumento, a rotação falha fechada. Não ativa
    nenhum writer novo: devolve um `RotationPrepared` para o V23-C publicar."""
    try:
        outcome, snap, lease = store.begin_rotation(timeout)  # NOT_NEEDED ou FENCE+DRENO (+lease)
    except Exception as e:                             # noqa: BLE001 — POISONED/timeout
        return RotationResult(RotationState.POISONED, None, f"fence/dreno: {e!r}")
    if outcome == _NOT_NEEDED:
        return RotationResult(RotationState.ROTATION_NOT_NEEDED, None, "sem registros novos")

    # C3.1: depois do fence o lease foi DESTACADO do store. Qualquer falha aqui FECHA o lease (o scope
    # fica sem writer, exigindo recovery explícito) — nunca fica um lease pendurado numa rotação morta.
    gc_fd = None
    prepared_registry = None

    def _release_gc_guard():
        nonlocal gc_fd
        if gc_fd is not None:
            fcntl.flock(gc_fd, fcntl.LOCK_UN)
            os.close(gc_fd)
            gc_fd = None

    def _fail(state, reason):
        _release_gc_guard()
        if lease is not None:
            lease.close()
        return RotationResult(state, None, reason)

    head = snap.wal_head
    scope, seg, first_seq = head.scope_id, head.segment_id, head.first_seq
    read_seq = head.durable_through_seq
    if read_seq < first_seq:                            # drenou vazio: nada a selar
        return _fail(RotationState.ROTATION_NOT_NEEDED, "drenou vazio; nada a selar")
    if seg + 1 > _U32_MAX:
        return _fail(RotationState.POISONED, "overflow de segment_id")
    if read_seq + 1 >= MAX_SEQ:
        return _fail(RotationState.POISONED, "overflow de wal_seq")
    # C4.0: o ACTIVE vem do CURSOR publicado do store, se houver (o writer real); o descriptor externo
    # só serve a stores sem cursor (scaffolding). O cursor SEMPRE vence quando presente.
    ps = store.capture_published_state() if hasattr(store, "capture_published_state") else None
    pub_cursor = ps.cursor if ps is not None else None
    if pub_cursor is not None:
        cur = pub_cursor.active_descriptor
    elif current_active_descriptor is not None:
        cur = current_active_descriptor
    else:
        return _fail(RotationState.FAILED, "sem cursor publicado nem descriptor ACTIVE externo")
    if (cur.status != STATE_ACTIVE or cur.segment_id != seg or cur.first_seq != first_seq
            or cur.key_id != store.key_id or cur.prev_segment_digest != store.previous_segment_digest):
        return _fail(RotationState.FAILED, "descriptor ACTIVE não casa o WalHead")

    # FH-03.2: selagem/publicação do SEALED + criação do próximo ACTIVE + registro prepared ficam sob
    # o mesmo gc.lock para que o GC nunca observe os novos objetos como órfãos utilizáveis.
    pubctx = getattr(store, "_pubctx", None)
    if pubctx is not None:
        from pathlib import Path
        from horizon_memory._engine.horizon_durability import open_hardened_lock
        from horizon_memory._engine.horizon_gc import PreparedRegistry, _GC_LOCK
        gc_fd = open_hardened_lock(Path(pubctx.publication_store.directory), _GC_LOCK)
        fcntl.flock(gc_fd, fcntl.LOCK_EX)
        prepared_registry = PreparedRegistry(pubctx.publication_store)

    identity = WalIdentity(scope, seg)
    exp = ActivePrefixExpectation.from_wal_head(head)
    sa = wal_store.seal_active(identity, exp, read_seq)     # OLD_SEALED
    if sa.state != WalStoreState.VALID:
        return _fail(RotationState.FAILED, f"seal_active: {sa.reason}")
    pub = wal_store.publish_sealed(identity)                # SEALED_PUBLISHED
    if pub.state != WalStoreState.VALID:
        return _fail(RotationState.FAILED, f"publish_sealed: {pub.reason}")
    sealed_sha, sealed_len = pub.descriptor.sha256, pub.descriptor.byte_length

    record_count = read_seq - first_seq + 1
    old_ordinal = cur.ordinal
    old_desc = WalSegmentDescriptor(old_ordinal, seg, first_seq, record_count, STATE_SEALED,
                                    sealed_len, sealed_sha, sealed_len, sealed_sha,
                                    cur.prev_segment_digest, store.key_id)

    # NEXT_ACTIVE_CREATED — header-only, idempotente
    next_seg, next_first, next_prev = seg + 1, read_seq + 1, sealed_sha[:16]
    next_header = encode_segment_header(store.key, scope, next_seg, next_first,
                                        key_id=store.key_id, previous_segment_digest=next_prev)
    ident_next = WalIdentity(scope, next_seg)
    ca = wal_store.create_active(ident_next, next_header)
    if ca.state == WalStoreState.ALREADY_EXISTS:
        op = wal_store.open_active(ident_next)
        if op.state != WalStoreState.VALID or op.blob != next_header:   # divergente → CONFLICT
            return _fail(RotationState.FAILED, "ACTIVE existente divergente")
    elif ca.state != WalStoreState.VALID:
        return _fail(RotationState.FAILED, f"create_active: {ca.reason}")

    next_sha = hashlib.sha256(next_header).digest()
    next_desc = WalSegmentDescriptor(old_ordinal + 1, next_seg, next_first, 0, STATE_ACTIVE,
                                     len(next_header), next_sha, 0, _ZERO32, next_prev, store.key_id)
    if lease is not None:                              # o lease sobrevive ao pacote (nunca fica livre)
        lease.transfer(_LEASE_OWNED_BY_PREPARED)
    draft = RotationPrepared(old_desc, next_desc, snap.index, snap.txindex, read_seq,
                             b"", _ROTATION_SEAL, lease)
    prepared = replace(draft, prepared_digest=rotation_prepared_digest(draft))
    if prepared_registry is not None:
        try:
            prepared_registry.register_rotation(rotation_registry_name(prepared), prepared, pubctx.keyring)
        except Exception as e:
            return _fail(RotationState.FAILED, f"registro GC do prepared: {e!r}")
    _release_gc_guard()
    return RotationResult(RotationState.ROTATION_PREPARED, prepared, "ok")
