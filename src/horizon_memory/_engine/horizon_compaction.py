# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compaction bloqueante da Horizon Memory (V23-B4.0, com as correções FH-00 da auditoria).

Compactar absorve a base `G` + o WAL `[H+1, R]` numa geração NOVA `G+1` cujo estado autoritativo até
`R` vira a base (5 artefatos content-addressed), com um ACTIVE VAZIO começando em `R+1`. É BLOQUEANTE
(fence → dreno → materializa → publica → ativa), não online — a pausa é medida no V23-F.

Correções incorporadas (FH-00):
  1. captura PÓS-DRENO obrigatória: `prepare_compaction` deriva sua origem do `PublishedStoreState`
     devolvido por `begin_compaction` DEPOIS do dreno (batches admitidos publicam durante o dreno),
     nunca de um cursor externo capturado antes do fence;
  2. dedup ENTRE compactions: a nova `DedupTable` é o MERGE canônico da DedupTable da base + o TxIdIndex
     dos WALs absorvidos (nunca esquece um `operation_id` que a base ainda retém);
  3. índices por INTERFACE pública (`.items()`) — idêntico com índice simples ou sharded;
  4. domínio u8 FAIL-CLOSED: valor fora de `[0,255]` → estado tipado `INCOMPATIBLE`, sem base truncada;
  5. `CompactionPrepared` realmente imutável (descriptors em tupla ordenada + digest canônico do
     candidato); `CompactionInput` construído e VALIDADO (lease selado, no scope, `OWNED_BY_COMPACTION`);
     overflow de `generation_id`/`segment_id`/`R+1`/`fact_count` tratado ANTES de construir artefatos;
  6. o registry cobre os fatos CONHECIDOS/MATERIALIZADOS (vivos E deletados) — ordinais densos sobre
     TODOS; DELETE preserva a versão (fence) e neutraliza o valor;
  7. RESIDUAL não duplica o BULK: política congelada `bulk exato u8 → residual vazio` (a equivalência é
     SEMÂNTICA — present/value/version, deleted/version, not_found — não exige `correct` vs `from_bulk`).

Deleção aqui é LÓGICA (o valor some da nova base; gerações antigas seguem acessíveis a snapshots até o
GC do V23-E) — não é *secure erase* físico do dispositivo.

`publish_maintenance` (CAS tipado) e `activate_compacted` são o B4.1; o dedup-com-base no ingress é o B4.2.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import struct
from dataclasses import dataclass, replace
from enum import IntEnum

from horizon_memory._engine.horizon_artifacts import (
    ArtifactKind, DedupTable, make_descriptor)
from horizon_memory._engine import file_lock as fcntl
from horizon_memory._engine.horizon_batch import LeaseState
from horizon_memory._engine.horizon_bulk import BulkSnapshot
from horizon_memory._engine.horizon_manifest import (
    DEFAULT_GENERATION_LIMITS, EpochManifest, OpenGenerationState, WalSegmentDescriptor,
    open_generation)
from horizon_memory._engine.horizon_store import FactRegistry, OP_DELETE, OP_PUT
from horizon_memory._engine.horizon_walstore import WalIdentity, WalStoreState
from horizon_memory._engine.horizon_wal import MAX_SEQ, STATE_ACTIVE, encode_segment_header
from horizon_memory._engine.residual_field import ResidualField, TombstoneLayer

_Z16, _Z32 = b"\x00" * 16, b"\x00" * 32
_U32_MAX = 0xFFFFFFFF
_COMPACTION_SEAL = object()        # selo privado — só prepare_compaction produz um CompactionPrepared
_DEFAULT_PAGE_BYTES = 128
_PREPARED_DOMAIN = b"HORIZON-COMPACTION-PREPARED-v1"
_BASE_ORDER = (ArtifactKind.REGISTRY, ArtifactKind.BULK, ArtifactKind.RESIDUAL,
               ArtifactKind.TOMBSTONE, ArtifactKind.DEDUP)


class CompactionState(IntEnum):
    PREPARED = 0
    FAILED = 1                     # falha lógica (materialização/criação) — sem writer novo
    POISONED = 2                   # falha física após o fence
    INCOMPATIBLE = 3              # valor fora do domínio u8 / overflow de limites (fail-closed)


@dataclass(frozen=True)
class CompactionInput:
    """Entrada IMUTÁVEL e VALIDADA da compaction (FH-00 §5): o cursor publicado (autoridade no `R`
    final, PÓS-dreno), a geração de origem, o snapshot fenced, o cutoff `R` e o lease em
    `OWNED_BY_COMPACTION`. Construída e conferida por `prepare_compaction`."""
    cursor: object                 # PublishedCursor
    source_generation_id: int
    snapshot: object               # L0Snapshot fenced
    read_seq: int                  # R
    lease: object                  # ScopeWriterLease em OWNED_BY_COMPACTION


@dataclass(frozen=True)
class CompactionPrepared:
    """Pacote SELADO e IMUTÁVEL (FH-00 §5 + FH-00.1). Além do selo privado (que prova a ORIGEM do
    objeto), o pacote carrega `prepared_digest` — digest canônico calculado por ÚLTIMO sobre TODOS os
    demais campos. `is_sealed_compaction` recomputa e exige igualdade, então um `dataclasses.replace`
    que preserve o selo mas troque `base_descriptors`/`next_active_descriptor`/`candidate_*` é detectado
    (o digest guardado deixa de bater com o recomputado). Só `prepare_compaction` produz um pacote
    válido; `publish_maintenance` exige candidato EXATAMENTE igual (descriptors + sha)."""
    source_publication_sha256: bytes
    read_seq: int                  # R (base_seq == read_seq na nova geração)
    generation_id: int             # G+1
    base_descriptors: tuple        # ((ArtifactKind, ArtifactDescriptor), …) ordenada por kind
    next_active_descriptor: WalSegmentDescriptor
    candidate_manifest_sha256: bytes
    candidate_manifest_blob: bytes
    lease: object
    prepared_digest: bytes         # FH-00.1: digest canônico de todos os campos acima (menos ele mesmo)
    seal: object

    def descriptor(self, kind):
        for k, d in self.base_descriptors:
            if k == kind:
                return d
        return None


@dataclass(frozen=True)
class CompactionResult:
    state: CompactionState
    prepared: CompactionPrepared | None
    reason: str


def is_sealed_compaction(prepared) -> bool:
    """FH-00.1 — selo PRIVADO (origem) E `prepared_digest` que bate com o recomputado (integridade de
    TODOS os campos). Um `replace(prepared, campo=…)` preserva o selo mas quebra o digest guardado →
    recusado. Nunca lança."""
    if getattr(prepared, "seal", None) is not _COMPACTION_SEAL:
        return False
    stored = getattr(prepared, "prepared_digest", None)
    if not isinstance(stored, (bytes, bytearray)):
        return False
    return hmac.compare_digest(bytes(stored), compaction_prepared_digest(prepared))


def compaction_prepared_digest(prepared) -> bytes:
    """Digest CANÔNICO de todos os campos do pacote EXCETO `prepared_digest`/`seal`/`lease` (FH-00 §5 +
    FH-00.1) — `is_sealed_compaction`/`publish_maintenance`/`activate_compacted` o recomputam e comparam,
    então nem a tupla de descriptors, nem o `next_active`, nem o candidato podem ser trocados (via
    `replace` preservando o selo) sem detecção."""
    h = hashlib.sha256()
    h.update(_PREPARED_DOMAIN)
    h.update(struct.pack("<32sQI", prepared.source_publication_sha256, prepared.read_seq,
                         prepared.generation_id))
    for kind, d in prepared.base_descriptors:
        h.update(struct.pack("<IHBQ32sI", int(kind), d.format_version, 1 if d.required else 0,
                             d.byte_length, d.sha256, d.key_id))
    na = prepared.next_active_descriptor
    h.update(struct.pack("<IIQIBQ32s16sI", na.ordinal, na.segment_id, na.first_seq, na.record_count,
                         na.status, na.durable_prefix_length, na.durable_prefix_sha256,
                         na.prev_segment_digest, na.key_id))
    h.update(prepared.candidate_manifest_sha256)
    return h.digest()


def compaction_registry_name(prepared) -> str:
    """Nome canônico/determinístico do registro GC deste prepared."""
    return f"comp-{bytes(prepared.prepared_digest).hex()}"


def _materialize_state(handle) -> dict:
    """Estado autoritativo em `R`: `fact_id → (fact_version, op, value)` para TODOS os fatos CONHECIDOS
    (vivos E deletados — os deletados permanecem para preservar o fence de versão), combinando a base
    (registry da geração) com o L0 reduzido (WAL). O L0 SOBREPÕE a base. Usa só interfaces públicas."""
    l0 = handle.l0
    facts: dict[int, tuple[int, str, int | None]] = {}
    reg = handle.bundle.registry
    if reg is not None:
        for fid, _ordinal, fver in reg.iter_entries():
            if l0.get(fid) is not None:
                continue                      # o L0 sobrepõe a base
            rr = handle.read_fact(fid)
            if rr.status == "deleted":
                facts[fid] = (fver, OP_DELETE, None)
            elif rr.value is not None:        # correct/from_bulk/fallback_bulk → presente
                facts[fid] = (fver, OP_PUT, int(rr.value))
    for fid, (ver, op, val) in l0.items():
        facts[fid] = (ver, op, val)
    return facts


class _DedupCollision(Exception):
    """B4.2 §2 — o MESMO `operation_id` aparece na base E no WAL absorvido. O ingresso base-aware o teria
    deduplicado; se chegou aqui, é corrupção → fail-closed, NUNCA overwrite silencioso de uma memória
    pela outra."""


def _merge_dedup(base_dedup, txindex, scope_id: int, gen: int, read_seq: int, retention: int,
                 key: bytes) -> bytes:
    """MERGE canônico + RETENÇÃO (FH-00 §2 + B4.2): DedupTable da base + TxIdIndex dos WALs absorvidos →
    nova DedupTable. Preserva `ClientDedupState`, digest e original_seq. Colisão base×WAL do mesmo
    `operation_id` → `_DedupCollision` (nunca overwrite silencioso).

    RETENÇÃO (B4.2): por cliente, o `retained_floor` avança MONOTONICAMENTE para
    `max(floor_da_base, highest_seen − retention + 1)`; as entries com `txid < retained_floor` são
    DESCARTADAS (memória coletada), mas o CLIENTE permanece registrado com seu `(highest_seen,
    retained_floor)`. Assim um `txid` abaixo do floor vira `IDEMPOTENCY_EXPIRED` no probe (conhecido-mas-
    removido) — jamais reaplicado — em vez de reaparecer como novo. `retention >= 1` garante
    `retained_floor <= highest_seen`."""
    r = max(1, int(retention))
    entries: dict[bytes, tuple[bytes, int]] = {}
    hi_seen: dict[int, int] = {}
    lo_bound: dict[int, int] = {}        # menor floor admissível por cliente (monótono: floor da base)
    if base_dedup is not None:
        for op_id, digest, seq in base_dedup.iter_entries():
            entries[op_id] = (digest, seq)
        for cid, (hi, lo) in base_dedup.clients().items():
            hi_seen[cid] = hi
            lo_bound[cid] = lo           # o floor nunca regride abaixo do que a base já coletou
    for op_id, (digest, seq) in txindex.items():
        if op_id in entries:             # base já retém este operation_id → colisão fail-closed
            raise _DedupCollision(op_id.hex())
        entries[op_id] = (digest, seq)   # o WAL é autoridade dos seus próprios seqs
        cid, txid = struct.unpack("<QQ", op_id)
        hi_seen[cid] = max(hi_seen.get(cid, txid), txid)
        lo_bound.setdefault(cid, txid)
        lo_bound[cid] = min(lo_bound[cid], txid)
    # floor final por cliente = max(floor herdado da base, highest_seen - retention + 1)
    floor_of: dict[int, int] = {}
    for cid, hi in hi_seen.items():
        floor_of[cid] = max(lo_bound.get(cid, 0), hi - r + 1)
    # descarta as entries abaixo do floor do seu cliente (retenção); mantém o cliente registrado
    kept = {}
    for op_id, (digest, seq) in entries.items():
        cid, txid = struct.unpack("<QQ", op_id)
        if txid >= floor_of[cid]:
            kept[op_id] = (digest, seq)
    seqs = [s for (_d, s) in kept.values()]
    if kept:
        floor, through = min(seqs), read_seq
    else:
        through, floor = read_seq, read_seq + 1
    ent_list = [(o, d, s) for o, (d, s) in kept.items()]
    clients_out = {cid: (hi_seen[cid], floor_of[cid]) for cid in hi_seen}
    return DedupTable.build(scope_id, gen, floor, through, r, ent_list, clients_out, key).serialize()


def _build_base_artifacts(facts: dict, base_dedup, txindex, scope_id: int, gen: int, read_seq: int,
                          retention: int, key: bytes):
    """(state, blobs|None, fact_count, reason). Materializa os 5 artefatos da nova base. FAIL-CLOSED no
    domínio u8 (§4). Ordinais densos sobre TODO fato conhecido (§6). Política congelada (§7): BULK exato
    u8 cobre todo ordinal (`support(bulk) == ordinais(registry)`), RESIDUAL VAZIO; DELETE → bulk neutro
    `0` + tombstone, sem valor."""
    for fid, (_ver, op, val) in facts.items():
        if op != OP_DELETE and not (isinstance(val, int) and 0 <= val <= 255):
            return (CompactionState.INCOMPATIBLE, None, 0, f"valor fora do domínio u8: fact {fid}={val!r}")
    fids = sorted(facts)
    fact_count = len(fids)
    registry_map, bulk_values, tombstoned = {}, {}, set()
    for ordinal, fid in enumerate(fids):
        ver, op, val = facts[fid]
        registry_map[fid] = (ordinal, ver)          # versão preservada = fence lógico (mesmo p/ DELETE)
        if op == OP_DELETE:
            bulk_values[ordinal] = 0                # neutro: tombstonado, jamais lido
            tombstoned.add(ordinal)
        else:
            bulk_values[ordinal] = int(val)         # BULK exato u8 (residual vazio)
    try:
        dedup_blob = _merge_dedup(base_dedup, txindex, scope_id, gen, read_seq, retention, key)
    except _DedupCollision as e:
        return (CompactionState.INCOMPATIBLE, None, 0, f"colisão base×WAL de dedup: {e}")
    blobs = {
        ArtifactKind.REGISTRY: FactRegistry.build(registry_map, scope_id, gen, fact_count, key).serialize(),
        ArtifactKind.BULK: BulkSnapshot.build(fact_count, bulk_values, scope_id, gen, key).serialize(),
        ArtifactKind.RESIDUAL: ResidualField.build(fact_count, {}, _DEFAULT_PAGE_BYTES, scope_id, gen, key).serialize(),
        ArtifactKind.TOMBSTONE: TombstoneLayer.build(fact_count, tombstoned, scope_id, gen, key).serialize(),
        ArtifactKind.DEDUP: dedup_blob,
    }
    return (CompactionState.PREPARED, blobs, fact_count, "ok")


def _validate_input(cursor, lease, scope_id: int, read_seq: int):
    """FH-00 §5 — a compaction só prossegue com lease PRESENTE, selado, no scope e OWNED_BY_COMPACTION."""
    from horizon_memory._engine.horizon_publication import is_sealed_cursor
    if not is_sealed_cursor(cursor):
        return (False, "cursor não selado")
    if lease is None:
        return (False, "lease ausente")
    if not lease.is_sealed() or not lease.held:
        return (False, "lease inválido (selo/held)")
    if lease.state != LeaseState.OWNED_BY_COMPACTION:
        return (False, f"lease em {lease.state.name} (esperado OWNED_BY_COMPACTION)")
    if lease.scope_id != scope_id:
        return (False, "lease de outro scope")
    if cursor.read_seq != read_seq:
        return (False, "cursor.read_seq != R pós-dreno")
    return (True, "ok")


def _validate_post_drain_state(snapshot, cursor):
    """FH-00 — o `PublishedStoreState` pós-dreno tem que ser INTEIRAMENTE coerente: o `snapshot`
    (visibilidade em memória) e o `cursor` (autoridade publicada, CURRENT→…→ACTIVE) avançam JUNTOS sob
    o lock do writer, então descrevem o MESMO `R` e o MESMO segmento ACTIVE vigente. Se divergirem, o
    estado é inconsistente e a compaction recusa fechada (nunca compacta um snapshot em `R+1` com um
    cursor em `R`)."""
    head = getattr(snapshot, "wal_head", None)
    if head is None:
        return (False, "snapshot sem wal_head")
    if snapshot.visible_through_seq != cursor.read_seq:
        return (False, "snapshot.visible_through_seq != cursor.read_seq (R divergente)")
    if head.durable_through_seq != cursor.read_seq:
        return (False, "wal_head.durable_through_seq != R")
    act = cursor.active_descriptor
    if head.scope_id != cursor.manifest.scope_id:
        return (False, "snapshot de outro scope")
    if head.segment_id != act.segment_id or head.first_seq != act.first_seq:
        return (False, "snapshot ↔ ACTIVE do cursor divergem (segmento/first_seq)")
    if (head.byte_length != act.durable_prefix_length
            or not hmac.compare_digest(head.prefix_digest, act.durable_prefix_sha256)):
        return (False, "prefixo durável do snapshot != ACTIVE do cursor")
    return (True, "ok")


def prepare_compaction(store, wal_store, object_store, keyring, *, retention: int = 2,
                       timeout: float = 5.0, failpoint=None) -> CompactionResult:
    """Fence + dreno do `store` (lease → OWNED_BY_COMPACTION); a origem vem do estado PÓS-DRENO. Deriva
    `G+1`, materializa a nova base (5 artefatos), cria o ACTIVE vazio em `R+1` e devolve um
    `CompactionPrepared` selado e imutável (lease → OWNED_BY_COMPACTION_PREPARED). Sem cursor externo."""
    fp = failpoint or (lambda s: None)
    try:
        published, lease = store.begin_compaction(timeout)   # captura PÓS-DRENO obrigatória (§1)
    except Exception as e:                                    # noqa: BLE001 — POISONED/timeout
        return CompactionResult(CompactionState.POISONED, None, f"fence/dreno: {e!r}")

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
        return CompactionResult(state, None, reason)

    cursor = getattr(published, "cursor", None)
    snapshot = getattr(published, "snapshot", None)
    if cursor is None or snapshot is None:
        return _fail(CompactionState.FAILED, "estado pós-dreno sem cursor/snapshot")
    man = cursor.manifest
    scope, key_id, R = man.scope_id, man.key_id, cursor.read_seq
    ok_in, why_in = _validate_input(cursor, lease, scope, R)
    if not ok_in:
        return _fail(CompactionState.FAILED, why_in)
    # FH-00: o estado pós-dreno tem que ser INTERNAMENTE coerente — snapshot ↔ cursor ↔ ACTIVE apontam
    # para o MESMO R e o MESMO segmento vigente (o dreno os avança JUNTOS sob o lock do writer).
    ok_st, why_st = _validate_post_drain_state(snapshot, cursor)
    if not ok_st:
        return _fail(CompactionState.FAILED, why_st)
    key = keyring.get(key_id)
    if key is None:
        return _fail(CompactionState.FAILED, "key_id do cursor desconhecido")

    new_gen = man.generation_id + 1
    old_active = cursor.active_descriptor
    next_seg = old_active.segment_id + 1
    # overflow ANTES de construir artefatos (§5)
    if new_gen > _U32_MAX or next_seg > _U32_MAX or R + 1 >= MAX_SEQ:
        return _fail(CompactionState.INCOMPATIBLE, "overflow de generation_id/segment_id/seq")

    og = open_generation(cursor.manifest_blob, object_store, wal_store, keyring)
    if og.state != OpenGenerationState.VALID:
        return _fail(CompactionState.FAILED, f"materialização: {og.reason}")
    handle = og.handle
    facts = _materialize_state(handle)
    if len(facts) > DEFAULT_GENERATION_LIMITS.max_total_frames:
        return _fail(CompactionState.INCOMPATIBLE, "fact_count acima do limite")

    st, base_blobs, fact_count, why = _build_base_artifacts(
        facts, handle.dedup, handle.txindex, scope, new_gen, R, retention, key)
    if st != CompactionState.PREPARED:
        return _fail(st, why)

    # FH-03.2: para writer operacional publicado, materialização + criação do ACTIVE + registro do
    # prepared são atômicos em relação ao GC. Nenhum pacote utilizável escapa antes do registro durável.
    pubctx = getattr(store, "_pubctx", None)
    if pubctx is not None:
        from pathlib import Path
        from horizon_memory._engine.horizon_durability import open_hardened_lock
        from horizon_memory._engine.horizon_gc import PreparedRegistry, _GC_LOCK
        gc_fd = open_hardened_lock(Path(pubctx.publication_store.directory), _GC_LOCK)
        fcntl.flock(gc_fd, fcntl.LOCK_EX)
        prepared_registry = PreparedRegistry(pubctx.publication_store)

    base_desc = {}
    for kind, blob in base_blobs.items():
        object_store.put_object(blob)                        # imutável, durável (fsync do dir)
        base_desc[kind] = make_descriptor(kind, blob, key_id=key_id)
    fp("after_base_materialized")

    header = encode_segment_header(key, scope, next_seg, R + 1, key_id=key_id, previous_segment_digest=_Z16)
    ident = WalIdentity(scope, next_seg)
    ca = wal_store.create_active(ident, header)
    if ca.state == WalStoreState.ALREADY_EXISTS:
        op = wal_store.open_active(ident)
        if op.state != WalStoreState.VALID or op.blob != header:
            return _fail(CompactionState.FAILED, "ACTIVE existente divergente")
    elif ca.state != WalStoreState.VALID:
        return _fail(CompactionState.FAILED, f"create_active: {ca.reason}")
    next_sha = hashlib.sha256(header).digest()
    next_active = WalSegmentDescriptor(0, next_seg, R + 1, 0, STATE_ACTIVE, len(header), next_sha,
                                       0, _Z32, _Z16, key_id)

    candidate = EpochManifest(scope, new_gen, fact_count, R, R, man.generation_id,
                              cursor.proof.manifest_sha256, base_desc, (next_active,), key_id)
    candidate_blob = candidate.serialize(key)
    fp("after_manifest_built")

    desc_tuple = tuple((k, base_desc[k]) for k in _BASE_ORDER)
    if lease is not None:
        lease.transfer(LeaseState.OWNED_BY_COMPACTION_PREPARED)
    # constrói com um digest placeholder, computa o digest canônico de todos os campos e o FIXA por
    # último (FH-00.1) — `is_sealed_compaction` recomputará e exigirá igualdade.
    draft = CompactionPrepared(cursor.proof.publication_sha256, R, new_gen, desc_tuple, next_active,
                               hashlib.sha256(candidate_blob).digest(), candidate_blob, lease,
                               b"", _COMPACTION_SEAL)
    prepared = replace(draft, prepared_digest=compaction_prepared_digest(draft))
    if prepared_registry is not None:
        try:
            prepared_registry.register_compaction(compaction_registry_name(prepared), prepared, keyring)
        except Exception as e:                              # registro falhou: pacote não escapa
            return _fail(CompactionState.FAILED, f"registro GC do prepared: {e!r}")
    _release_gc_guard()
    return CompactionResult(CompactionState.PREPARED, prepared, "ok")
