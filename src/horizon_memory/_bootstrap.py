# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Composition glue for the standalone durable engine.

This module orchestrates genesis, publication, writer activation and reads.
The storage laws remain isolated in :mod:`horizon_memory._engine`.
"""
from __future__ import annotations

from pathlib import Path

from horizon_memory._engine.horizon_artifacts import (
    ArtifactKind, DedupTable, Keyring, make_descriptor)
from horizon_memory._engine.horizon_batch import GroupCommitStore
from horizon_memory._engine.horizon_bulk import BulkSnapshot
from horizon_memory._engine.horizon_manifest import (
    EpochManifest, OpenGenerationState, WalSegmentDescriptor, open_generation)
from horizon_memory._engine.horizon_publication import (
    CursorState, PublicationStore, bind_rotation_prepared, open_publication_record,
    open_published_cursor, read_current)
from horizon_memory._engine.horizon_publication import _publish_cas_primitive as _publish
from horizon_memory._engine.horizon_rotation import RotationState, prepare_rotation
from horizon_memory._engine.horizon_store import FactRegistry
from horizon_memory._engine.horizon_walstore import WalIdentity, WalStore
from horizon_memory._engine.horizon_wal import STATE_ACTIVE
from horizon_memory._engine.residual_field import ResidualField, TombstoneLayer

_Z16, _Z32 = b"\x00" * 16, b"\x00" * 32


def keyring_for(cfg) -> Keyring:
    return Keyring({cfg.key_id: cfg.key})


def genesis_base(ws: WalStore, cfg) -> dict:
    """Materializa os 5 artefatos da base vazia (genesis) e devolve seus descriptors."""
    s, g, fc, k = cfg.scope_id, cfg.generation_id, cfg.fact_capacity, cfg.key
    reg = FactRegistry.build({}, s, g, fc, k).serialize()
    bulk = BulkSnapshot.build(fc, {}, s, g, k).serialize()
    field = ResidualField.build(fc, {}, cfg.residual_dim, s, g, k).serialize()
    tomb = TombstoneLayer.build(fc, set(), s, g, k).serialize()
    dedup = DedupTable.build(s, g, 1, 0, 2, [], {}, k).serialize()
    blobs = {ArtifactKind.REGISTRY: reg, ArtifactKind.BULK: bulk, ArtifactKind.RESIDUAL: field,
             ArtifactKind.TOMBSTONE: tomb, ArtifactKind.DEDUP: dedup}
    for b in blobs.values():
        ws.put_object(b)
    return {kind: make_descriptor(kind, b) for kind, b in blobs.items()}


def _genesis_store(ws: WalStore, cfg) -> GroupCommitStore:
    ident = WalIdentity(cfg.scope_id, 1)
    ws._active_path(ident).parent.mkdir(parents=True, exist_ok=True)
    return GroupCommitStore(str(ws._active_path(ident)), cfg.key, cfg.scope_id,
                            segment_id=1, first_seq=1)


def _active_desc(store: GroupCommitStore, ordinal: int):
    head = store.capture_read_view().wal_head
    rc = max(0, head.durable_through_seq - head.first_seq + 1)
    return WalSegmentDescriptor(ordinal, head.segment_id, head.first_seq, rc, STATE_ACTIVE,
                                head.byte_length, head.prefix_digest, 0, _Z32,
                                store.previous_segment_digest, store.key_id), head.durable_through_seq


def create_genesis(cfg):
    """create(): publica uma geração genesis VAZIA (man0, R=0). Ainda sem writer publicado.
    Devolve (ws, pub, base, genesis_store)."""
    kr = keyring_for(cfg)
    ws = WalStore(str(cfg.wal_root), kr)
    base = genesis_base(ws, cfg)
    store = _genesis_store(ws, cfg)
    active, R = _active_desc(store, 0)
    man0 = EpochManifest(cfg.scope_id, cfg.generation_id, cfg.fact_capacity, 0, R, -1, _Z32,
                         base, (active,), 0).serialize(cfg.key)
    Path(cfg.pub_root).mkdir(parents=True, exist_ok=True)   # root da publicação deve pré-existir
    pub = PublicationStore(str(cfg.pub_root), cfg.scope_id).initialize()
    _publish(pub, ws, ws, kr, man0, _Z32, cfg.key_id)
    return ws, pub, base, store


def open_existing(cfg):
    """open(): reabre ws + pub de um root existente. Não retoma writer aqui (o resume é do recovery)."""
    kr = keyring_for(cfg)
    ws = WalStore(str(cfg.wal_root), kr)
    pub = PublicationStore(str(cfg.pub_root), cfg.scope_id)
    return ws, pub


def activate_first_writer(cfg, ws, pub, base, genesis_store, seed_ops):
    """Ativação preguiçosa na PRIMEIRA escrita: grava o primeiro batch no genesis store (sem cursor),
    publica um man0' que o cobre (CAS sobre o man0 vazio), rotaciona (sela o seg1) e ativa o writer
    PUBLICADO do seg2. Devolve (writer_store, receipts)."""
    kr = keyring_for(cfg)
    receipts = []
    for (op, fid, ver, val, opid) in seed_ops:
        receipts.append(genesis_store.submit(op, fid, ver, val, opid).result(5.0))
    active, R = _active_desc(genesis_store, 0)
    man0p = EpochManifest(cfg.scope_id, cfg.generation_id, cfg.fact_capacity, 0, R, -1, _Z32,
                          base, (active,), 0).serialize(cfg.key)
    cur_empty = read_current(pub.directory, kr)[1].publication_sha256
    _publish(pub, ws, ws, kr, man0p, cur_empty, cfg.key_id)
    cur0 = read_current(pub.directory, kr)[1].publication_sha256
    _, rec0, _ = open_publication_record(cur0.hex(), ws, kr)
    cur_desc = WalSegmentDescriptor(0, 1, 1, 0, STATE_ACTIVE, 67, _Z32, 0, _Z32, _Z16, 0)
    rot = prepare_rotation(genesis_store, ws, cur_desc)
    if rot.state != RotationState.ROTATION_PREPARED:
        raise RuntimeError(f"rotação genesis falhou: {rot.state} {rot.reason}")
    ok, segs, why = bind_rotation_prepared(rot.prepared, cur0, rec0.serialize(cfg.key), man0p, kr)
    if not ok:
        raise RuntimeError(f"bind_rotation_prepared falhou: {why}")
    man1 = EpochManifest(cfg.scope_id, cfg.generation_id, cfg.fact_capacity, 0, rot.prepared.read_seq,
                         -1, _Z32, base, segs, 0).serialize(cfg.key)
    r1 = _publish(pub, ws, ws, kr, man1, cur0, cfg.key_id)
    act = GroupCommitStore.activate_prepared(rot.prepared, r1.proof, pub, ws, ws, kr)
    if act.store is None:
        raise RuntimeError(f"activate_prepared falhou: {act.state} {act.reason}")
    return act.store, receipts


def current_cursor(cfg, ws, pub):
    """(CursorState, cursor|None). Autoridade publicada do scope."""
    kr = keyring_for(cfg)
    st, cursor, _why = open_published_cursor(pub, ws, kr)
    return st, cursor


def read_generation(cfg, ws, manifest_blob):
    """Abre uma geração por manifest_blob (imutável). Devolve (state, handle|None)."""
    kr = keyring_for(cfg)
    og = open_generation(manifest_blob, ws, ws, kr)
    return og.state, og.handle
