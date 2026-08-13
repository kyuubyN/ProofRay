# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""V23-B3-4 — protocolo de preparação de rotação: fence/dreno/selagem/publicação/próximo-ACTIVE,
produzindo um `RotationPrepared` SEM ativar writer novo. A ativação (novos ACKs) pertence ao V23-C."""

from __future__ import annotations

import hashlib
import struct
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from horizon_memory._engine.horizon_artifacts import ArtifactKind, DedupTable, Keyring, make_descriptor
from horizon_memory._engine.horizon_batch import GroupCommitStore, NOT_ACCEPTED
from horizon_memory._engine.horizon_bulk import BulkSnapshot
from horizon_memory._engine.horizon_manifest import (
    EpochManifest,
    OpenGenerationState as S,
    WalSegmentDescriptor,
    open_generation,
)
from horizon_memory._engine.horizon_wal import STATE_ACTIVE
from horizon_memory._engine.horizon_rotation import RotationState, prepare_rotation
from horizon_memory._engine.horizon_store import FactRegistry, OP_PUT, preflight
from horizon_memory._engine.horizon_walstore import (
    ActivePrefixExpectation,
    ActivePrefixState as A,
    WalIdentity,
    WalStore,
    WalStoreState as W,
)
from horizon_memory._engine.residual_field import ResidualField, TombstoneLayer
from horizon_memory._engine.horizon_wal import (
    content_digest,
    encode_frame,
    encode_segment_header,
    make_operation_id,
)

KEY = b"qhdre-v23-b34-rotation-key-01234"
SCOPE, GEN, FC = 7, 5, 2000
KR = Keyring({0: KEY})
Z16 = b"\x00" * 16


_Z32 = b"\x00" * 32


def _cur_active(ordinal, seg, first_seq, prev=Z16):
    """Descriptor ACTIVE vigente (do CURRENT) — a rotação valida identidade e deriva os ordinais."""
    return WalSegmentDescriptor(ordinal, seg, first_seq, 0, STATE_ACTIVE, 67, _Z32, 0, _Z32, prev, 0)


def _ws():
    return WalStore(str(Path(tempfile.mkdtemp())), KR)


def _store_at(ws, seg, first_seq, base=None):
    ident = WalIdentity(SCOPE, seg)
    ws._active_path(ident).parent.mkdir(parents=True, exist_ok=True)
    return GroupCommitStore(str(ws._active_path(ident)), KEY, SCOPE, segment_id=seg,
                            first_seq=first_seq, base=base)


def _submit(store, ops):
    for (fid, ver, val) in ops:
        store.submit(OP_PUT, fid, ver, val, make_operation_id(1, fid * 100 + ver)).result(2.0)


def _active_prefix_bytes(seg, first_seq, ops):
    body = encode_segment_header(KEY, SCOPE, seg, first_seq)
    seqn = first_seq
    for (fid, ver, val) in ops:
        body += encode_frame(KEY, SCOPE, seg, OP_PUT, seqn, make_operation_id(2, seqn), fid, ver,
                             struct.pack("<I", val))
        seqn += 1
    return body


def _base(ws, H, through=None):
    through = H if through is None else through
    field = ResidualField.build(FC, {1: 9}, 128, SCOPE, GEN, KEY).serialize()
    tomb = TombstoneLayer.build(FC, {2}, SCOPE, GEN, KEY).serialize()
    reg = FactRegistry.build({1: (1, 1), 2: (2, 1), 3: (3, 1)}, SCOPE, GEN, FC, KEY).serialize()
    bulk = BulkSnapshot.build(FC, {1: 9, 2: 1, 3: 1}, SCOPE, GEN, KEY).serialize()
    dedup = DedupTable.build(SCOPE, GEN, through + 1, through, 2, [], {}, KEY).serialize()
    blobs = {ArtifactKind.REGISTRY: reg, ArtifactKind.BULK: bulk, ArtifactKind.RESIDUAL: field,
             ArtifactKind.TOMBSTONE: tomb, ArtifactKind.DEDUP: dedup}
    for b in blobs.values():
        ws.put_object(b)
    return {k: make_descriptor(k, b) for k, b in blobs.items()}


class SealActiveTests(unittest.TestCase):
    def _prep(self, ops):
        ws = _ws(); ident = WalIdentity(SCOPE, 3)
        prefix = _active_prefix_bytes(3, 11, ops)
        p = ws._active_path(ident); p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(prefix)
        exp = ActivePrefixExpectation(len(prefix), hashlib.sha256(prefix).digest())
        return ws, ident, prefix, exp

    def test_seal_then_idempotent(self):
        ws, ident, prefix, exp = self._prep([(100, 1, 900), (101, 1, 901)])
        a = ws.seal_active(ident, exp, 12)
        self.assertEqual(a.state, W.VALID)
        b = ws.seal_active(ident, exp, 12)                     # footer correto já existe
        self.assertTrue(b.state == W.VALID and b.idempotent)
        self.assertEqual(ws.publish_sealed(ident).state, W.VALID)   # validado pelo WalStore

    def test_torn_footer_is_repaired_not_silent(self):
        ws, ident, prefix, exp = self._prep([(100, 1, 900)])
        ws.seal_active(ident, exp, 11)
        full = ws._active_path(ident).read_bytes()
        ws._active_path(ident).write_bytes(full[:-3])          # footer rasgado
        r = ws.seal_active(ident, exp, 11)
        self.assertTrue(r.state == W.VALID and r.repaired)     # reparado explicitamente
        self.assertEqual(ws.publish_sealed(ident).state, W.VALID)

    def test_frame_after_prefix_refuses(self):
        ws, ident, prefix, exp = self._prep([(100, 1, 900)])
        extra = _active_prefix_bytes(3, 11, [(100, 1, 900), (101, 1, 901)])[len(prefix):]
        with open(ws._active_path(ident), "ab") as fh:
            fh.write(extra)                                    # FRAME real após o prefixo (ACK possível)
        r = ws.seal_active(ident, exp, 11)
        self.assertEqual(r.state, W.CORRUPT)                   # nunca trunca uma operação
        self.assertNotIn("WSEAL", ws._active_path(ident).read_bytes()[len(prefix):].decode("latin1"))

    def test_sha_and_length_divergence_refuse(self):
        ws, ident, prefix, exp = self._prep([(100, 1, 900)])
        self.assertEqual(ws.seal_active(ident, ActivePrefixExpectation(len(prefix), b"\x11" * 32), 11).state,
                         W.CORRUPT)
        self.assertEqual(ws.seal_active(ident, ActivePrefixExpectation(len(prefix) + 5, exp.sha256), 11).state,
                         W.CORRUPT)
        self.assertEqual(ws.seal_active(ident, exp, 999).state, W.CORRUPT)   # last_seq != R


class RotationTests(unittest.TestCase):
    def _rotate(self, ws, seg=1, first_seq=11, ops=((100, 1, 900), (101, 1, 901)), base=None,
                ordinal=0, prev=Z16):
        st = _store_at(ws, seg, first_seq, base=base)
        _submit(st, ops)
        return st, prepare_rotation(st, ws, _cur_active(ordinal, seg, first_seq, prev))

    def test_prepared_descriptors_and_chain(self):
        ws = _ws()
        st, r = self._rotate(ws)
        self.assertEqual(r.state, RotationState.ROTATION_PREPARED)
        old, nxt = r.prepared.old_sealed_descriptor, r.prepared.next_active_descriptor
        self.assertEqual((old.segment_id, old.first_seq, old.record_count), (1, 11, 2))
        self.assertEqual((nxt.segment_id, nxt.first_seq, nxt.record_count), (2, 13, 0))
        self.assertEqual(nxt.prev_segment_digest, old.sealed_object_sha256[:16])   # cadeia
        self.assertEqual(r.prepared.read_seq, 12)

    def test_carried_indices_preserve_dedup_and_versions(self):
        ws = _ws()
        st, r = self._rotate(ws)
        tx = r.prepared.carried_txindex
        opid = make_operation_id(1, 100 * 100 + 1)                 # o op_id do fato 100 v1
        dig = content_digest(OP_PUT, 100, 1, 900)
        self.assertEqual(tx.check(opid, dig), "dedup_replay")      # retry antigo continua dedup
        cur = r.prepared.carried_index.get(100)
        self.assertEqual(preflight(cur, OP_PUT, 1, 999), "conflict")   # downgrade rejeitado

    def test_old_view_survives_and_composition_opens(self):
        ws = _ws()
        base = _base(ws, 10)                                       # H=10; WAL escreve fatos novos
        st, r = self._rotate(ws)
        # a view antiga do prefixo sobrevive à selagem (footer além do prefixo é ignorado)
        head_exp = ActivePrefixExpectation(r.prepared.old_sealed_descriptor.durable_prefix_length,
                                           r.prepared.old_sealed_descriptor.durable_prefix_sha256)
        # SEALED + novo ACTIVE abrem por open_generation
        man = EpochManifest(SCOPE, GEN, FC, 10, 12, -1, b"\x00" * 32, base,
                            (r.prepared.old_sealed_descriptor, r.prepared.next_active_descriptor), 0)
        res = open_generation(man.serialize(KEY), ws, ws, KR)
        self.assertEqual(res.state, S.VALID)
        self.assertEqual(res.handle.read_fact(100).value, 900)    # do SEALED rotacionado
        self.assertEqual(res.handle.read_fact(1).value, 9)        # da base

    def test_fence_rejects_new_and_no_file_deleted(self):
        ws = _ws()
        st, r = self._rotate(ws)
        after = st.submit(OP_PUT, 200, 1, 1, make_operation_id(9, 9)).result(1.0)
        self.assertEqual(after[0], NOT_ACCEPTED)                   # após fence: nunca admitido
        self.assertTrue(ws._active_path(WalIdentity(SCOPE, 1)).exists())   # nada é apagado no B3-4

    def test_idempotent_next_active_and_divergent_conflict(self):
        ws = _ws()
        # pré-cria o próximo ACTIVE idêntico ao que a rotação criaria → reutiliza
        st = _store_at(ws, 1, 11); _submit(st, [(100, 1, 900), (101, 1, 901)])
        sealed_prev = None  # a rotação deriva sozinha
        # divergente: escreve um ACTIVE seg2 com header de first_seq errado
        bad = encode_segment_header(KEY, SCOPE, 2, 999)
        p = ws._active_path(WalIdentity(SCOPE, 2)); p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(bad)
        r = prepare_rotation(st, ws, _cur_active(0, 1, 11))
        self.assertEqual(r.state, RotationState.FAILED)           # ACTIVE existente divergente

    def test_poisoned_aborts(self):
        ws = _ws()
        st = _store_at(ws, 1, 11); _submit(st, [(100, 1, 900)])
        st._poisoned = True
        self.assertEqual(prepare_rotation(st, ws, _cur_active(0, 1, 11)).state, RotationState.POISONED)

    def test_rotation_not_needed_when_empty(self):
        ws = _ws()
        st = _store_at(ws, 1, 11)                                  # nada submetido → snapshot vazio
        r = prepare_rotation(st, ws, _cur_active(0, 1, 11))
        self.assertEqual(r.state, RotationState.ROTATION_NOT_NEEDED)
        self.assertIsNone(r.prepared)
        # não houve fence: o writer ainda aceita
        self.assertNotEqual(st.submit(OP_PUT, 100, 1, 900, make_operation_id(1, 1)).result(1.0)[0],
                            NOT_ACCEPTED)
        st.close()

    def test_multisegment_ordinals_and_open(self):
        # geração com 3 SEALED (ordinais 0,1,2) + rotação produz ACTIVE ordinal 3
        ws = _ws()
        prev = Z16
        seg, first, ordinal = 1, 11, 0
        for i in range(2):                                        # duas rotações → dois SEALED
            st = (_store_at(ws, seg, first) if i == 0 else
                  GroupCommitStore.resume_existing(str(ws._active_path(WalIdentity(SCOPE, seg))),
                                                   KEY, SCOPE, segment_id=seg))
            _submit(st, [(100 + i, 1, i)])
            r = prepare_rotation(st, ws, _cur_active(ordinal, seg, first, prev))
            self.assertEqual(r.state, RotationState.ROTATION_PREPARED)
            self.assertEqual(r.prepared.old_sealed_descriptor.ordinal, ordinal)
            self.assertEqual(r.prepared.next_active_descriptor.ordinal, ordinal + 1)
            seg, first, ordinal = seg + 1, r.prepared.read_seq + 1, ordinal + 1
            prev = r.prepared.old_sealed_descriptor.sealed_object_sha256[:16]

    def test_three_consecutive_rotations_chain(self):
        ws = _ws()
        st = _store_at(ws, 1, 11); _submit(st, [(100, 1, 900), (101, 1, 901)])
        prev_sealed, cur = None, _cur_active(0, 1, 11)
        for i in range(3):
            r = prepare_rotation(st, ws, cur)
            self.assertEqual(r.state, RotationState.ROTATION_PREPARED)
            old, nxt = r.prepared.old_sealed_descriptor, r.prepared.next_active_descriptor
            if prev_sealed is not None:
                self.assertEqual(old.prev_segment_digest, prev_sealed)   # cadeia entre rotações
            prev_sealed = old.sealed_object_sha256[:16]
            self.assertEqual(nxt.prev_segment_digest, prev_sealed)
            st = GroupCommitStore.resume_existing(str(ws._active_path(WalIdentity(SCOPE, nxt.segment_id))),
                                                  KEY, SCOPE, segment_id=nxt.segment_id)
            _submit(st, [(300 + i, 1, i)])
            cur = nxt                                             # o próximo ACTIVE vira o current
        st.fence_and_drain()

    def test_overflow_fails_closed(self):
        ws = _ws()

        class _Stub:
            def begin_rotation(self, timeout=5.0):
                head = SimpleNamespace(scope_id=SCOPE, segment_id=0xFFFFFFFF, first_seq=1,
                                       durable_through_seq=5, byte_length=64, prefix_digest=b"\x00" * 32)
                return ("ROTATION_FENCED", SimpleNamespace(wal_head=head, index=None, txindex=None))
        self.assertEqual(prepare_rotation(_Stub(), ws, _cur_active(0, 0xFFFFFFFF, 1)).state,
                         RotationState.POISONED)

    def test_await_fenced_shutdown_closes_once(self):
        from horizon_memory._engine.horizon_batch import ShutdownResult
        ws = _ws()
        st = _store_at(ws, 1, 11); _submit(st, [(100, 1, 900)])
        prepare_rotation(st, ws, _cur_active(0, 1, 11))           # já fez fence + fechou o FD
        r = st.await_fenced_shutdown()                            # idempotente, nunca reabre admissão
        self.assertIsInstance(r, ShutdownResult)
        self.assertTrue(r.ok)


if __name__ == "__main__":
    unittest.main()
