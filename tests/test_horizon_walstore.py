# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""V23-B3-1 — WalStore: namespaces ACTIVE/SEALED, identidade tipada, criação/reabertura seguras,
objeto SEALED imutável, e a paridade de header entre WalWriter e GroupCommitStore."""

from __future__ import annotations

import hashlib
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from horizon_memory._engine.horizon_artifacts import Keyring
from horizon_memory._engine.horizon_batch import GroupCommitStore
from horizon_memory._engine.horizon_walstore import (
    ActiveWalHandle,
    WalIdentity,
    WalStore,
    WalStoreLimits,
    WalStoreState as W,
)
from horizon_memory._engine.horizon_store import OP_PUT
from horizon_memory._engine.horizon_wal import (
    WalWriter,
    _seal_footer,
    encode_frame,
    encode_segment_header,
    make_operation_id,
    scan,
)

KEY = b"qhdre-v23-b31-walstore-key-01234"
KR = Keyring({0: KEY})
Z16 = b"\x00" * 16


def _store():
    return WalStore(str(Path(tempfile.mkdtemp())), KR)


def _hdr(scope, seg, first_seq=1, key_id=0, prev=Z16):
    return encode_segment_header(KEY, scope, seg, first_seq, key_id=key_id,
                                 previous_segment_digest=prev)


def _sealed_blob(scope, seg, first_seq=1, ops=(), key_id=0, prev=Z16):
    """Segmento completo header+frames+footer (SEALED)."""
    body = encode_segment_header(KEY, scope, seg, first_seq, key_id=key_id,
                                 previous_segment_digest=prev)
    seqn = first_seq
    for (op, fid, fver, val, opid) in ops:
        payload = struct.pack("<I", val) if op == OP_PUT else b""
        body += encode_frame(KEY, scope, seg, op, seqn, opid, fid, fver, payload)
        seqn += 1
    off = len(body)
    last = first_seq + len(ops) - 1
    body += _seal_footer(KEY, scope, seg, last, off, hashlib.sha256(body).digest()[:16])
    return body


def _write_at(ws, ident, blob):
    """Escreve bytes diretamente no caminho ACTIVE derivado (para preparar SEALED nos testes)."""
    p = ws._active_path(ident)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(blob)
    return p


class WalStoreTests(unittest.TestCase):
    # G1 -----------------------------------------------------------------
    def test_same_segment_id_different_scopes_no_collision(self):
        ws = _store()
        a = ws.create_active(WalIdentity(7, 3), _hdr(7, 3))
        b = ws.create_active(WalIdentity(9, 3), _hdr(9, 3))
        self.assertEqual((a.state, b.state), (W.VALID, W.VALID))
        self.assertNotEqual(a.handle.path, b.handle.path)
        self.assertEqual(ws.open_active(WalIdentity(7, 3)).header.scope_id, 7)
        self.assertEqual(ws.open_active(WalIdentity(9, 3)).header.scope_id, 9)

    # G2 -----------------------------------------------------------------
    def test_recreate_never_overwrites(self):
        ws = _store()
        self.assertEqual(ws.create_active(WalIdentity(7, 3), _hdr(7, 3)).state, W.VALID)
        before = Path(ws._active_path(WalIdentity(7, 3))).read_bytes()
        r = ws.create_active(WalIdentity(7, 3), _hdr(7, 3, first_seq=99))   # tentativa divergente
        self.assertEqual(r.state, W.ALREADY_EXISTS)
        self.assertIsNone(r.handle)
        self.assertEqual(Path(ws._active_path(WalIdentity(7, 3))).read_bytes(), before)

    # G3 -----------------------------------------------------------------
    def test_path_is_derived_not_representable_traversal(self):
        ws = _store()
        p = ws._active_path(WalIdentity(7, 3)).resolve()
        self.assertTrue(str(p).startswith(str(ws.root.resolve())))
        # identidade fora de u32 é recusada de forma tipada (nunca vira caminho)
        self.assertEqual(ws.create_active(WalIdentity(-1, 3), _hdr(7, 3)).state, W.CORRUPT)
        self.assertEqual(ws.open_active(WalIdentity(7, 1 << 40)).state, W.CORRUPT)

    # G4 -----------------------------------------------------------------
    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "sem O_NOFOLLOW")
    def test_symlink_not_followed(self):
        ws = _store()
        ident = WalIdentity(7, 3)
        path = ws._active_path(ident)
        path.parent.mkdir(parents=True, exist_ok=True)
        target = ws.root / "secret"
        target.write_bytes(b"x" * 64)
        os.symlink(str(target), str(path))
        self.assertEqual(ws.open_active(ident).state, W.CORRUPT)

    # G5 -----------------------------------------------------------------
    def test_header_carries_identity(self):
        ws = _store()
        ws.create_active(WalIdentity(7, 3), _hdr(7, 3, first_seq=41, key_id=0, prev=b"\x22" * 16))
        o = ws.open_active(WalIdentity(7, 3))
        self.assertEqual((o.header.first_seq, o.header.key_id, o.header.segment_id), (41, 0, 3))
        self.assertEqual(o.header.previous_segment_digest, b"\x22" * 16)

    # G6 -----------------------------------------------------------------
    def test_header_diverging_from_identity_is_corrupt(self):
        ws = _store()
        r = ws.create_active(WalIdentity(7, 3), _hdr(9, 3))   # header scope=9, identidade scope=7
        self.assertEqual(r.state, W.CORRUPT)
        self.assertIsNone(r.handle)

    # G7 -----------------------------------------------------------------
    def test_resume_keeps_identity_and_sequence(self):
        d = Path(tempfile.mkdtemp()); p = str(d / "seg.hwal")
        w = WalWriter.create_new(p, KEY, 7, segment_id=3, first_seq=5, key_id=0,
                                 previous_segment_digest=b"\x33" * 16)
        from horizon_memory._engine.horizon_store import OP_PUT
        from horizon_memory._engine.horizon_wal import make_operation_id
        w.append(OP_PUT, 100, 1, 900, make_operation_id(1, 1))
        w.append(OP_PUT, 101, 1, 901, make_operation_id(1, 2))
        w.close()
        r = WalWriter.resume_existing(p, KEY, 7, segment_id=3, expected_key_id=0,
                                      expected_previous_segment_digest=b"\x33" * 16)
        self.assertEqual((r.first_seq, r.key_id), (5, 0))
        self.assertEqual(r.previous_segment_digest, b"\x33" * 16)
        self.assertEqual(r._next_seq, 7)                    # 5,6 usados → próximo 7
        # divergência do prev esperado é fail-closed
        with self.assertRaises(Exception):
            WalWriter.resume_existing(p, KEY, 7, segment_id=3,
                                      expected_previous_segment_digest=b"\x00" * 16)
        r.close()

    # G8 -----------------------------------------------------------------
    def test_sealed_publish_is_byte_identical_and_content_addressed(self):
        ws = _store()
        ident = WalIdentity(7, 3)
        raw = _sealed_blob(7, 3, ops=[(OP_PUT, 100, 1, 900, make_operation_id(1, 1))])
        _write_at(ws, ident, raw)
        pub = ws.publish_sealed(ident)
        self.assertEqual(pub.state, W.VALID)
        self.assertEqual(pub.descriptor.sha256, hashlib.sha256(raw).digest())
        got = ws.open_sealed(pub.descriptor.sha256.hex())
        self.assertEqual((got.state, got.blob), (W.VALID, raw))

    # G9 -----------------------------------------------------------------
    def test_divergent_sealed_object_never_replaced(self):
        ws = _store()
        ident = WalIdentity(7, 3)
        raw = _sealed_blob(7, 3, ops=[(OP_PUT, 100, 1, 900, make_operation_id(1, 1))])
        _write_at(ws, ident, raw)
        sha = hashlib.sha256(raw).hexdigest()
        (ws.root / "objects").mkdir(parents=True, exist_ok=True)
        (ws.root / "objects" / f"{sha}.hobj").write_bytes(b"divergente")   # objeto divergente
        self.assertEqual(ws.publish_sealed(ident).state, W.CORRUPT)
        self.assertEqual((ws.root / "objects" / f"{sha}.hobj").read_bytes(), b"divergente")

    # G10 ----------------------------------------------------------------
    def test_active_and_sealed_namespaces_are_distinct(self):
        ws = _store()
        ident = WalIdentity(7, 3)
        raw = _sealed_blob(7, 3, ops=[(OP_PUT, 100, 1, 900, make_operation_id(1, 1))])
        p = _write_at(ws, ident, raw)
        pub = ws.publish_sealed(ident)
        self.assertIn("wal/active", str(p).replace(os.sep, "/"))
        self.assertEqual(ws.open_sealed("0" * 64).state, W.MISSING)         # SEALED só em objects/
        self.assertEqual(ws.open_active(WalIdentity(1, 1)).state, W.MISSING)  # ACTIVE só por identidade
        self.assertEqual(ws.open_sealed(pub.descriptor.sha256.hex()).state, W.VALID)

    # G11 ----------------------------------------------------------------
    def test_partial_create_leaves_no_usable_active(self):
        ws = _store()

        def _boom(fd, data):
            raise OSError("disco cheio")
        r = ws.create_active(WalIdentity(7, 3), _hdr(7, 3), _write=_boom)
        self.assertEqual(r.state, W.IO_ERROR)
        self.assertIsNone(r.handle)
        self.assertEqual(ws.open_active(WalIdentity(7, 3)).state, W.MISSING)   # nada utilizável ficou

    # G12 ----------------------------------------------------------------
    def test_size_limit_before_read(self):
        ws = _store()
        ws.create_active(WalIdentity(7, 3), _hdr(7, 3))
        r = ws.open_active(WalIdentity(7, 3), WalStoreLimits(max_active_bytes=8))
        self.assertEqual(r.state, W.RESOURCE_LIMIT)
        self.assertIsNone(r.blob)

    # G13 ----------------------------------------------------------------
    def test_walwriter_and_groupcommit_headers_are_equivalent(self):
        d = Path(tempfile.mkdtemp())
        pw = str(d / "w.hwal"); pg = str(d / "g.hwal")
        w = WalWriter.create_new(pw, KEY, 7, segment_id=3, first_seq=5, key_id=0,
                                 previous_segment_digest=b"\x44" * 16)
        w.close()
        g = GroupCommitStore(pg, KEY, 7, segment_id=3, first_seq=5, key_id=0,
                             previous_segment_digest=b"\x44" * 16)
        try:
            from horizon_memory._engine.horizon_wal import _SEG_HEADER, TAG_BYTES
            n = _SEG_HEADER.size + TAG_BYTES
            self.assertEqual(Path(pw).read_bytes()[:n], Path(pg).read_bytes()[:n])
        finally:
            g.close()

    # G14 ----------------------------------------------------------------
    def test_no_failure_returns_partial_handle(self):
        ws = _store()
        for r in (ws.create_active(WalIdentity(9, 3), _hdr(7, 3)),        # identidade divergente
                  ws.create_active(WalIdentity(-1, 3), _hdr(7, 3))):      # identidade inválida
            self.assertNotEqual(r.state, W.VALID)
            self.assertIsNone(r.handle)
        for o in (ws.open_active(WalIdentity(5, 5)),                       # ausente
                  ws.open_sealed("z" * 64)):                              # digest inválido
            self.assertNotEqual(o.state, W.VALID)
            self.assertIsNone(o.blob)


class PublishFooterProofTests(unittest.TestCase):
    """B3-1.1: publish_sealed(identity) não certifica bytes arbitrários — exige footer válido."""

    def test_active_without_footer_refuses_and_creates_no_object(self):
        ws = _store()
        ident = WalIdentity(7, 3)
        ws.create_active(ident, _hdr(7, 3))          # header-only, ACTIVE (sem footer)
        r = ws.publish_sealed(ident)
        self.assertEqual(r.state, W.CORRUPT)
        self.assertIsNone(r.descriptor)
        self.assertFalse(list((ws.root / "objects").glob("*.hobj")))   # nenhum objeto criado

    def test_torn_footer_refuses(self):
        ws = _store()
        ident = WalIdentity(7, 3)
        blob = _sealed_blob(7, 3, ops=[(OP_PUT, 100, 1, 900, make_operation_id(1, 1))])
        _write_at(ws, ident, blob[:-3])              # footer rasgado
        self.assertEqual(ws.publish_sealed(ident).state, W.CORRUPT)
        self.assertFalse(list((ws.root / "objects").glob("*.hobj")))

    def test_path_argument_never_certifies(self):
        ws = _store()
        ident = WalIdentity(7, 3)
        _write_at(ws, ident, _sealed_blob(7, 3, ops=[(OP_PUT, 1, 1, 9, make_operation_id(1, 1))]))
        # a assinatura só aceita WalIdentity: um `str`/path nunca certifica (nem vira descriptor)
        r = ws.publish_sealed(str(ws._active_path(ident)))
        self.assertEqual(r.state, W.CORRUPT)
        self.assertIsNone(r.descriptor)

    def test_valid_sealed_is_idempotent(self):
        ws = _store()
        ident = WalIdentity(7, 3)
        _write_at(ws, ident, _sealed_blob(7, 3, ops=[(OP_PUT, 1, 1, 9, make_operation_id(1, 1))]))
        a = ws.publish_sealed(ident)
        b = ws.publish_sealed(ident)
        self.assertEqual((a.state, b.state), (W.VALID, W.VALID))
        self.assertEqual(a.descriptor.sha256, b.descriptor.sha256)


if __name__ == "__main__":
    unittest.main()
