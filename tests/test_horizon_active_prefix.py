# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""V23-B3-2 — contrato exato do ActivePrefix: lê SÓ o prefixo ACKado, tolera append concorrente
depois do prefixo (nunca falso CORRUPT), rejeita footer dentro do prefixo, e integra SEALED+ACTIVE
real via WalStore em open_generation. Inclui a atomicidade WalHead↔ReadView (digest completo)."""

from __future__ import annotations

import hashlib
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from horizon_memory._engine.horizon_artifacts import ArtifactKind, DedupTable, Keyring, make_descriptor
from horizon_memory._engine.horizon_batch import GroupCommitStore
from horizon_memory._engine.horizon_bulk import BulkSnapshot
from horizon_memory._engine.horizon_manifest import (
    EpochManifest,
    OpenGenerationState as S,
    WalSegmentDescriptor,
    open_generation,
)
from horizon_memory._engine.horizon_store import FactRegistry, OP_PUT
from horizon_memory._engine.horizon_walstore import (
    ActivePrefixExpectation,
    ActivePrefixState as A,
    WalIdentity,
    WalStore,
)
from horizon_memory._engine.residual_field import ResidualField, TombstoneLayer
from horizon_memory._engine.horizon_wal import (
    STATE_ACTIVE,
    STATE_SEALED,
    _seal_footer,
    encode_frame,
    encode_segment_header,
    make_operation_id,
)

KEY = b"qhdre-v23-b32-activeprefix-key01"
SCOPE, GEN, FC = 7, 5, 2000
KR = Keyring({0: KEY})
Z16, Z32 = b"\x00" * 16, b"\x00" * 32


def _store():
    return WalStore(str(Path(tempfile.mkdtemp())), KR)


def _active_bytes(seg, first_seq, ops, *, prev=Z16, key_id=0):
    body = encode_segment_header(KEY, SCOPE, seg, first_seq, key_id=key_id, previous_segment_digest=prev)
    seqn = first_seq
    for (op, fid, fver, val, opid) in ops:
        body += encode_frame(KEY, SCOPE, seg, op, seqn, opid, fid, fver, struct.pack("<I", val))
        seqn += 1
    return body


def _footer_for(prefix, seg, last_seq):
    return _seal_footer(KEY, SCOPE, seg, last_seq, len(prefix), hashlib.sha256(prefix).digest()[:16])


def _write(ws, ident, blob):
    p = ws._active_path(ident)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(blob)
    return p


def _exp(prefix):
    return ActivePrefixExpectation(len(prefix), hashlib.sha256(prefix).digest())


def _op(fid, val=None, seqhint=None):
    return (OP_PUT, fid, 1, val if val is not None else fid * 10, make_operation_id(1, seqhint or fid))


class ActivePrefixTests(unittest.TestCase):
    def test_header_only_zero_records_opens(self):
        ws = _store(); ident = WalIdentity(SCOPE, 3)
        prefix = _active_bytes(3, 11, [])
        _write(ws, ident, prefix)
        r = ws.read_active_prefix(ident, _exp(prefix))
        self.assertEqual(r.state, A.VALID)
        self.assertEqual(len(r.prefix.frames), 0)

    def test_exact_prefix_opens(self):
        ws = _store(); ident = WalIdentity(SCOPE, 3)
        prefix = _active_bytes(3, 11, [_op(100, 900), _op(101, 901)])
        _write(ws, ident, prefix)
        r = ws.read_active_prefix(ident, _exp(prefix))
        self.assertEqual((r.state, len(r.prefix.frames), r.prefix.last_seq), (A.VALID, 2, 12))

    def test_full_frame_after_prefix_ignored(self):
        ws = _store(); ident = WalIdentity(SCOPE, 3)
        prefix = _active_bytes(3, 11, [_op(100, 900)])
        after = _active_bytes(3, 11, [_op(100, 900), _op(101, 901)])   # prefix + 1 frame extra
        _write(ws, ident, after)
        r = ws.read_active_prefix(ident, _exp(prefix))                 # espera só o prefixo
        self.assertEqual((r.state, len(r.prefix.frames)), (A.VALID, 1))

    def test_torn_frame_after_prefix_ignored(self):
        ws = _store(); ident = WalIdentity(SCOPE, 3)
        prefix = _active_bytes(3, 11, [_op(100, 900)])
        extra = _active_bytes(3, 11, [_op(100, 900), _op(101, 901)])
        _write(ws, ident, prefix + extra[len(prefix):][:7])           # cauda rasgada após o prefixo
        r = ws.read_active_prefix(ident, _exp(prefix))
        self.assertEqual((r.state, len(r.prefix.frames)), (A.VALID, 1))

    def test_footer_after_prefix_ignored_old_view_survives(self):
        ws = _store(); ident = WalIdentity(SCOPE, 3)
        prefix = _active_bytes(3, 11, [_op(100, 900)])
        sealed = prefix + _footer_for(prefix, 3, 11)                  # selado DEPOIS do prefixo
        _write(ws, ident, sealed)
        r = ws.read_active_prefix(ident, _exp(prefix))                # a view antiga sobrevive
        self.assertEqual((r.state, len(r.prefix.frames)), (A.VALID, 1))

    def test_footer_inside_prefix_is_incompatible(self):
        ws = _store(); ident = WalIdentity(SCOPE, 3)
        prefix = _active_bytes(3, 11, [_op(100, 900)])
        sealed = prefix + _footer_for(prefix, 3, 11)
        _write(ws, ident, sealed)
        r = ws.read_active_prefix(ident, _exp(sealed))                # footer DENTRO do prefixo
        self.assertEqual(r.state, A.INCOMPATIBLE)

    def test_smaller_than_prefix_missing_committed(self):
        ws = _store(); ident = WalIdentity(SCOPE, 3)
        prefix = _active_bytes(3, 11, [_op(100, 900)])
        _write(ws, ident, prefix[:-5])                               # arquivo menor que o prefixo
        r = ws.read_active_prefix(ident, _exp(prefix))
        self.assertEqual(r.state, A.MISSING_COMMITTED_PREFIX)

    def test_mutation_in_prefix_rejects(self):
        ws = _store(); ident = WalIdentity(SCOPE, 3)
        prefix = _active_bytes(3, 11, [_op(100, 900)])
        mutated = bytearray(prefix); mutated[-4] ^= 0xFF
        _write(ws, ident, bytes(mutated))
        r = ws.read_active_prefix(ident, _exp(prefix))               # digest do prefixo original
        self.assertEqual(r.state, A.CORRUPT)

    def test_length_ending_mid_frame_rejects(self):
        ws = _store(); ident = WalIdentity(SCOPE, 3)
        prefix = _active_bytes(3, 11, [_op(100, 900)])
        cut = prefix[:-3]                                            # termina no meio do frame
        _write(ws, ident, prefix)
        r = ws.read_active_prefix(ident, _exp(cut))                 # byte_length no meio de um frame
        self.assertEqual(r.state, A.CORRUPT)


class CompositionTests(unittest.TestCase):
    def _base(self, ws, H, through=None):
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

    def _sealed_seg(self, ws, seg, first_seq, ops):
        prefix = _active_bytes(seg, first_seq, ops)
        blob = prefix + _footer_for(prefix, seg, first_seq + len(ops) - 1)
        ws.put_object(blob)
        sha = hashlib.sha256(blob).digest()
        return blob, WalSegmentDescriptor(0, seg, first_seq, len(ops), STATE_SEALED,
                                          len(blob), sha, len(blob), sha, Z16, 0)

    def _active_seg(self, ws, seg, first_seq, ops, ordinal, prev, *, write=True):
        prefix = _active_bytes(seg, first_seq, ops, prev=prev)
        if write:
            _write(ws, WalIdentity(SCOPE, seg), prefix)
        sha = hashlib.sha256(prefix).digest()
        desc = WalSegmentDescriptor(ordinal, seg, first_seq, len(ops), STATE_ACTIVE,
                                    len(prefix), sha, 0, Z32, prev, 0)
        return prefix, desc

    def _man(self, base, segs, H, R):
        return EpochManifest(SCOPE, GEN, FC, H, R, -1, Z32, base, tuple(segs), 0)

    def test_open_generation_sealed_plus_active(self):
        ws = _store()
        base = self._base(ws, 10)
        sealed_blob, d_sealed = self._sealed_seg(ws, 1, 11, [_op(100, 900), _op(101, 901)])
        prev = hashlib.sha256(sealed_blob).digest()[:16]
        _, d_active = self._active_seg(ws, 2, 13, [_op(102, 902)], ordinal=1, prev=prev)
        r = open_generation(self._man(base, [d_sealed, d_active], 10, 13).serialize(KEY), ws, ws, KR)
        self.assertEqual(r.state, S.VALID)
        self.assertEqual(r.handle.read_fact(100).value, 900)   # do SEALED
        self.assertEqual(r.handle.read_fact(102).value, 902)   # do ACTIVE
        self.assertEqual(r.handle.read_fact(1).value, 9)       # da base

    def test_same_readview_stable_across_appends(self):
        ws = _store()
        base = self._base(ws, 10)
        sealed_blob, d_sealed = self._sealed_seg(ws, 1, 11, [_op(100, 900), _op(101, 901)])
        prev = hashlib.sha256(sealed_blob).digest()[:16]
        prefix, d_active = self._active_seg(ws, 2, 13, [_op(102, 902)], ordinal=1, prev=prev)
        man = self._man(base, [d_sealed, d_active], 10, 13).serialize(KEY)
        r1 = open_generation(man, ws, ws, KR)
        self.assertEqual(r1.handle.read_fact(102).value, 902)
        self.assertEqual(r1.handle.read_fact(103).status, "not_found")
        # append de um frame NOVO (seq 14) depois do prefixo, fora do R do manifesto
        extra = _active_bytes(2, 13, [_op(102, 902), _op(103, 903)], prev=prev)[len(prefix):]
        with open(ws._active_path(WalIdentity(SCOPE, 2)), "ab") as fh:
            fh.write(extra)
        r2 = open_generation(man, ws, ws, KR)               # MESMO manifesto/ReadView
        self.assertEqual(r2.state, S.VALID)
        self.assertEqual(r2.handle.read_fact(102).value, 902)
        self.assertEqual(r2.handle.read_fact(103).status, "not_found")   # append > R nunca entra

    def test_expectation_from_wal_head_linearization(self):
        ws = _store()
        ident = WalIdentity(SCOPE, 4)
        ws._active_path(ident).parent.mkdir(parents=True, exist_ok=True)
        store = GroupCommitStore(str(ws._active_path(ident)), KEY, SCOPE, segment_id=4, first_seq=1)
        try:
            store.submit(OP_PUT, 100, 1, 900, make_operation_id(1, 1)).result(1.0)
            store.submit(OP_PUT, 101, 1, 901, make_operation_id(1, 2)).result(1.0)
            snap = store.capture_read_view()
            exp = ActivePrefixExpectation.from_wal_head(snap.wal_head)
            self.assertEqual(len(exp.sha256), 32)              # SHA-256 completo
            r = ws.read_active_prefix(ident, exp)
            self.assertEqual(r.state, A.VALID)
            self.assertEqual(r.prefix.last_seq, snap.visible_through_seq)   # mesmo ponto de linearização
            self.assertEqual(len(r.prefix.frames), 2)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
