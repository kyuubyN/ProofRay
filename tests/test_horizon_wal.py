# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Testes do WAL (V23-A1/A1.1): durabilidade, idempotência, parser canônico, scan/apply, selagem."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from horizon_memory._engine.horizon_store import OP_DELETE, OP_PUT
from horizon_memory._engine.horizon_wal import (
    APPLIED,
    CLEAN,
    CONFLICT,
    CORRUPT_COMMITTED_PREFIX,
    DEDUP_REPLAY,
    IDEMPOTENT,
    INVALID_ARGUMENT,
    MISSING_COMMITTED_PREFIX,
    POISONED,
    STALE_REJECTED,
    TAG_BYTES,
    TXID_CONFLICT,
    VERSION_CONFLICT,
    SegmentSeal,
    WalError,
    WalWriter,
    _SEAL,
    encode_frame,
    encode_segment_header,
    make_operation_id,
    recover,
    reduce_frames,
    scan,
)

KEY = b"unit-test-wal-session-key-012345"
SCOPE, SEG = 7, 1


def _tmp():
    return Path(tempfile.mkdtemp(prefix="qhdre-wal-test-"))


def _ops(n):
    return [(OP_PUT if i % 3 else OP_DELETE, i % (n // 2 + 1), i + 1,
             (None if i % 3 == 0 else i * 2 + 1), make_operation_id(0, i + 1)) for i in range(n)]


class WriteAllTests(unittest.TestCase):
    def test_partial_writes_produce_identical_blob(self):
        tmp = _tmp()
        ops = _ops(30)
        def w(path, chunk):
            ww = WalWriter.create_new(str(path), KEY, SCOPE, SEG, max_write_chunk=chunk)
            for op, fid, ver, val, opid in ops:
                ww.append(op, fid, ver, val, opid)
            ww.close()
            return path.read_bytes()
        ref = w(tmp / "ref", None)
        for k in range(1, 8):
            self.assertEqual(w(tmp / f"c{k}", k), ref)


class IdempotencyTests(unittest.TestCase):
    def test_conflict_and_dedup_status_sequence(self):
        tmp = _tmp()
        ww = WalWriter.create_new(str(tmp / "cd"), KEY, SCOPE, SEG)
        D = make_operation_id(1, 100)
        self.assertEqual(ww.append(OP_PUT, 1, 1, 10, make_operation_id(1, 1)).status, APPLIED)
        self.assertEqual(ww.append(OP_PUT, 1, 1, 99, make_operation_id(1, 2)).status, VERSION_CONFLICT)
        self.assertEqual(ww.append(OP_PUT, 1, 1, 10, make_operation_id(1, 3)).status, IDEMPOTENT)
        self.assertEqual(ww.append(OP_PUT, 1, 2, 20, D).status, APPLIED)
        self.assertEqual(ww.append(OP_PUT, 1, 1, 5, make_operation_id(1, 4)).status, STALE_REJECTED)
        self.assertEqual(ww.append(OP_PUT, 1, 2, 20, D).status, DEDUP_REPLAY)
        self.assertEqual(ww.append(OP_PUT, 1, 3, 30, D).status, TXID_CONFLICT)
        ww.close()
        rec = recover((tmp / "cd").read_bytes(), KEY, required=True, scope_id=SCOPE, segment_id=SEG)
        self.assertEqual(rec.classification, CLEAN)
        self.assertEqual(rec.applied_count, 2)              # só as 2 aplicáveis persistiram
        self.assertEqual(rec.index.get(1), (2, OP_PUT, 20))


class CanonicalTests(unittest.TestCase):
    def test_authentic_but_wrong_length_is_corrupt(self):
        hdr = encode_segment_header(KEY, SCOPE, SEG, 1)
        # PUT com payload de 3 bytes é autêntico (MAC ok) mas não canônico
        frame = encode_frame(KEY, SCOPE, SEG, OP_PUT, 1, make_operation_id(0, 1), 5, 1, b"\x00\x00\x00")
        r = recover(hdr + frame, KEY, required=True, scope_id=SCOPE, segment_id=SEG)
        self.assertEqual(r.classification, CORRUPT_COMMITTED_PREFIX)
        self.assertEqual(r.applied_count, 0)


class ScanApplyTests(unittest.TestCase):
    def test_corruption_after_read_seq_is_detected(self):
        tmp = _tmp()
        ops = _ops(20)
        ww = WalWriter.create_new(str(tmp / "sa"), KEY, SCOPE, SEG)
        for op, fid, ver, val, opid in ops:
            ww.append(op, fid, ver, val, opid)
        ww.close()
        blob = bytearray((tmp / "sa").read_bytes())
        blob[-6] ^= 0x01                                  # corrompe o último frame
        r = recover(bytes(blob), KEY, required=True, scope_id=SCOPE, segment_id=SEG, upto_seq=2)
        self.assertEqual(r.classification, CORRUPT_COMMITTED_PREFIX)   # scan valida além do read_seq
        self.assertEqual(r.applied_count, 0)                          # índice parcial nunca entregue


class SealTests(unittest.TestCase):
    def test_sealed_full_vs_truncated(self):
        tmp = _tmp()
        ops = _ops(12)
        ww = WalWriter.create_new(str(tmp / "f"), KEY, SCOPE, SEG)
        for op, fid, ver, val, opid in ops:
            ww.append(op, fid, ver, val, opid)
        ww.close()
        fb = (tmp / "f").read_bytes()
        seal = SegmentSeal(len(ops), len(fb), hashlib.sha256(fb).digest()[:16])
        self.assertEqual(recover(fb, KEY, required=True, scope_id=SCOPE, segment_id=SEG,
                                 expected_seal=seal).classification, CLEAN)
        self.assertEqual(recover(fb[:-40], KEY, required=True, scope_id=SCOPE, segment_id=SEG,
                                 expected_seal=seal).classification, MISSING_COMMITTED_PREFIX)
        # ACTIVE (sem selagem declarada): prefixo menor é aceitável
        self.assertIn(recover(fb[:-40], KEY, required=True, scope_id=SCOPE, segment_id=SEG).classification,
                      (CLEAN, "TAIL_DROPPED"))


class PoisonTests(unittest.TestCase):
    def test_write_error_poisons_writer(self):
        tmp = _tmp()
        ww = WalWriter.create_new(str(tmp / "p"), KEY, SCOPE, SEG)
        os.close(ww._fd)                                  # força EBADF
        with self.assertRaises(Exception):
            ww.append(OP_PUT, 1, 1, 1, make_operation_id(0, 1))
        self.assertTrue(ww.poisoned)
        self.assertEqual(ww.append(OP_PUT, 1, 2, 2, make_operation_id(0, 2)).status, POISONED)


class CanonicalIngressTests(unittest.TestCase):
    def test_invalid_command_rejected_without_side_effects(self):
        tmp = _tmp()
        path = str(tmp / "ing")
        ww = WalWriter.create_new(path, KEY, SCOPE, SEG)
        size0 = os.path.getsize(path)
        self.assertEqual(ww.append(OP_PUT, 1, 1, None, make_operation_id(0, 1)).status, INVALID_ARGUMENT)
        self.assertEqual(ww.append(OP_DELETE, 1, 1, 5, make_operation_id(0, 2)).status, INVALID_ARGUMENT)
        self.assertEqual(ww.append(OP_PUT, 1, 0, 9, make_operation_id(0, 3)).status, INVALID_ARGUMENT)
        self.assertEqual(os.path.getsize(path), size0)   # WAL intocado
        self.assertEqual(ww._next_seq, 1)
        self.assertFalse(ww.poisoned)                     # erro de cliente não envenena
        self.assertEqual(ww.append(OP_PUT, 1, 1, 42, make_operation_id(0, 4)).status, APPLIED)
        ww.close()


class ResumeTests(unittest.TestCase):
    def test_create_new_refuses_to_clobber(self):
        tmp = _tmp()
        p = str(tmp / "x")
        WalWriter.create_new(p, KEY, SCOPE, SEG).close()
        with self.assertRaises(FileExistsError):
            WalWriter.create_new(p, KEY, SCOPE, SEG)      # O_EXCL

    def test_resume_dedup_same_seq_no_new_frame(self):
        tmp = _tmp()
        p = str(tmp / "r")
        w = WalWriter.create_new(p, KEY, SCOPE, SEG)
        for i in range(1, 5):
            w.append(OP_PUT, i, i, i * 7, make_operation_id(0, i))
        os.close(w._fd)                                    # simula crash antes do ACK (frames duráveis)
        w2 = WalWriter.resume_existing(p, KEY, SCOPE, SEG)
        size = os.path.getsize(p)
        retry = w2.append(OP_PUT, 4, 4, 28, make_operation_id(0, 4))   # mesmo operation_id
        self.assertEqual(retry.status, DEDUP_REPLAY)
        self.assertEqual(retry.wal_seq, 4)                 # mesmo wal_seq
        self.assertEqual(os.path.getsize(p), size)         # nenhum frame novo
        nxt = w2.append(OP_PUT, 5, 5, 35, make_operation_id(0, 5))
        self.assertEqual((nxt.status, nxt.wal_seq), (APPLIED, 5))   # sequência contígua
        w2.close()

    def test_sealed_segment_not_resumable(self):
        tmp = _tmp()
        p = str(tmp / "s")
        w = WalWriter.create_new(p, KEY, SCOPE, SEG)
        w.append(OP_PUT, 1, 1, 1, make_operation_id(0, 1))
        w.seal(); w.close()
        with self.assertRaises(WalError):
            WalWriter.resume_existing(p, KEY, SCOPE, SEG)


class SealExactTests(unittest.TestCase):
    def test_declared_seal_rejects_extra_frame(self):
        tmp = _tmp()
        p = str(tmp / "se")
        w = WalWriter.create_new(p, KEY, SCOPE, SEG)
        for i in range(1, 5):
            w.append(OP_PUT, i, i, i, make_operation_id(0, i))
        w.close()
        fb = Path(p).read_bytes()
        seal = SegmentSeal(4, len(fb), hashlib.sha256(fb).digest()[:16])
        self.assertEqual(recover(fb, KEY, required=True, scope_id=SCOPE, segment_id=SEG,
                                 expected_seal=seal).classification, CLEAN)
        extra = fb + encode_frame(KEY, SCOPE, SEG, OP_PUT, 5, make_operation_id(0, 5), 5, 1,
                                  b"\x01\x00\x00\x00")
        self.assertEqual(recover(extra, KEY, required=True, scope_id=SCOPE, segment_id=SEG,
                                 expected_seal=seal).classification, MISSING_COMMITTED_PREFIX)


class RecoveryAuthorityTests(unittest.TestCase):
    """V23-A2-0: footer defere ao manifesto; reducer único recusa história não canônica."""

    def test_footer_defers_to_manifest(self):
        tmp = _tmp()
        p = str(tmp / "sealed")
        w = WalWriter.create_new(p, KEY, SCOPE, SEG)
        for i in range(1, 5):
            w.append(OP_PUT, i, i, i * 3, make_operation_id(0, i))
        w.seal(); w.close()
        blob = Path(p).read_bytes()
        off = len(blob) - (_SEAL.size + TAG_BYTES)
        good = SegmentSeal(4, off, hashlib.sha256(blob[:off]).digest()[:16])
        bad = SegmentSeal(3, off, good.prefix_digest)   # last_seq divergente
        self.assertEqual(recover(blob, KEY, required=True, scope_id=SCOPE, segment_id=SEG,
                                 expected_seal=good).classification, CLEAN)
        self.assertEqual(recover(blob, KEY, required=True, scope_id=SCOPE, segment_id=SEG,
                                 expected_seal=bad).classification, MISSING_COMMITTED_PREFIX)

    def test_single_reducer_rejects_duplicate_operation_id(self):
        hdr = encode_segment_header(KEY, SCOPE, SEG, 1)
        A = make_operation_id(0, 1)
        f1 = encode_frame(KEY, SCOPE, SEG, OP_PUT, 1, A, 1, 1, b"\x0a\x00\x00\x00")
        f2 = encode_frame(KEY, SCOPE, SEG, OP_PUT, 2, A, 2, 1, b"\x14\x00\x00\x00")
        r = recover(hdr + f1 + f2, KEY, required=True, scope_id=SCOPE, segment_id=SEG)
        self.assertEqual(r.classification, TXID_CONFLICT)
        self.assertEqual(r.applied_count, 0)
        self.assertIsNone(r.index.get(1))

    def test_single_reducer_rejects_stale_frame(self):
        hdr = encode_segment_header(KEY, SCOPE, SEG, 1)
        f1 = encode_frame(KEY, SCOPE, SEG, OP_PUT, 1, make_operation_id(0, 1), 7, 5, b"\x32\x00\x00\x00")
        f2 = encode_frame(KEY, SCOPE, SEG, OP_PUT, 2, make_operation_id(0, 2), 7, 3, b"\x1e\x00\x00\x00")
        r = recover(hdr + f1 + f2, KEY, required=True, scope_id=SCOPE, segment_id=SEG)
        self.assertEqual(r.classification, CONFLICT)
        self.assertEqual(r.applied_count, 0)

    def test_recover_and_resume_share_reducer(self):
        # resume recusa exatamente o que recover marca como não canônico
        tmp = _tmp()
        p = tmp / "dup"
        A = make_operation_id(0, 1)
        p.write_bytes(encode_segment_header(KEY, SCOPE, SEG, 1)
                      + encode_frame(KEY, SCOPE, SEG, OP_PUT, 1, A, 1, 1, b"\x01\x00\x00\x00")
                      + encode_frame(KEY, SCOPE, SEG, OP_PUT, 2, A, 2, 1, b"\x02\x00\x00\x00"))
        with self.assertRaises(WalError):
            WalWriter.resume_existing(str(p), KEY, SCOPE, SEG)


if __name__ == "__main__":
    unittest.main()
