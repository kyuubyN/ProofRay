# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""V23-C0/C1 — primitivas de durabilidade de nomes + formato canônico de PublicationRecord/CURRENT."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import horizon_memory._engine.horizon_durability as dur
from horizon_memory._engine.horizon_artifacts import Keyring, ObjectStore
from horizon_memory._engine.horizon_durability import (
    durable_link,
    durable_replace,
    ensure_durable_directory_chain,
    fsync_dir,
)
from horizon_memory._engine.horizon_publication import (
    CURRENT_LEN,
    PublicationRecord,
    PublicationState as P,
    parse_current,
    parse_publication_record,
    read_current,
    serialize_current,
    write_current,
)
from horizon_memory._engine.horizon_walstore import WalIdentity, WalStore
from horizon_memory._engine.horizon_wal import encode_segment_header

KEY = b"qhdre-v23-c1-publication-key-0123"
KR = Keyring({0: KEY})
Z32 = b"\x00" * 32


class DurabilityTests(unittest.TestCase):
    def test_fsync_dir_and_chain(self):
        root = Path(tempfile.mkdtemp())
        fsync_dir(root)                                   # não deve lançar
        ensure_durable_directory_chain(root / "a" / "b" / "c", root)
        self.assertTrue((root / "a" / "b" / "c").is_dir())
        with self.assertRaises(ValueError):
            ensure_durable_directory_chain(Path("/etc/passwd"), root)   # fora de root

    def test_durable_link_and_replace(self):
        root = Path(tempfile.mkdtemp())
        (root / "t1").write_bytes(b"x" * 10)
        durable_link(root / "t1", root / "final")
        self.assertEqual(os.stat(root / "t1").st_ino, os.stat(root / "final").st_ino)  # hard-link
        with self.assertRaises(FileExistsError):
            durable_link(root / "t1", root / "final")     # nunca sobrescreve
        (root / "t2").write_bytes(b"novo")
        durable_replace(root / "t2", root / "final")      # rename atômico
        self.assertEqual((root / "final").read_bytes(), b"novo")
        self.assertFalse((root / "t2").exists())

    def test_put_object_fsyncs_directory(self):
        calls = {"n": 0}
        orig = dur._dir_fsync
        dur._dir_fsync = lambda fd: (calls.__setitem__("n", calls["n"] + 1), orig(fd))[1]
        try:
            store = ObjectStore(str(Path(tempfile.mkdtemp())))
            store.put_object(b"conteudo duravel")
        finally:
            dur._dir_fsync = orig
        self.assertGreaterEqual(calls["n"], 1)            # o diretório de objetos foi fsyncado

    def test_create_active_fsyncs_directory(self):
        calls = {"n": 0}
        orig = dur._dir_fsync
        dur._dir_fsync = lambda fd: (calls.__setitem__("n", calls["n"] + 1), orig(fd))[1]
        try:
            ws = WalStore(str(Path(tempfile.mkdtemp())), KR)
            hdr = encode_segment_header(KEY, 7, 3, 1)
            self.assertEqual(ws.create_active(WalIdentity(7, 3), hdr).state.name, "VALID")
        finally:
            dur._dir_fsync = orig
        self.assertGreaterEqual(calls["n"], 1)            # a cadeia wal/active/<scope>/ foi fsyncada


class PublicationRecordTests(unittest.TestCase):
    def test_roundtrip_and_fields(self):
        rec = PublicationRecord(7, 5, 9, 42, b"\x11" * 32, b"\x22" * 32, 0)
        st, got, _ = parse_publication_record(rec.serialize(KEY), KR)
        self.assertEqual(st, P.VALID)
        self.assertEqual((got.scope_id, got.generation_id, got.revision, got.read_seq), (7, 5, 9, 42))
        self.assertEqual((got.manifest_sha256, got.previous_publication_sha256),
                         (b"\x11" * 32, b"\x22" * 32))

    def test_mac_length_version_rejections(self):
        rec = PublicationRecord(7, 5, 1, 42, b"\x11" * 32, Z32, 0)
        blob = rec.serialize(KEY)
        bad = bytearray(blob); bad[-1] ^= 0xFF
        self.assertEqual(parse_publication_record(bytes(bad), KR)[0], P.CORRUPT)
        self.assertEqual(parse_publication_record(blob + b"\x00", KR)[0], P.CORRUPT)
        ver = bytearray(blob); ver[4:6] = (99).to_bytes(2, "little")
        self.assertEqual(parse_publication_record(bytes(ver), KR)[0], P.INCOMPATIBLE)
        self.assertEqual(parse_publication_record(blob, Keyring({}))[0], P.INCOMPATIBLE)   # key desconhecida
        self.assertEqual(parse_publication_record(None, KR)[0], P.MISSING)


class CurrentPointerTests(unittest.TestCase):
    def test_current_roundtrip_fixed_length_and_tamper(self):
        blob = serialize_current(b"\x33" * 32, 0, KEY)
        self.assertEqual(len(blob), CURRENT_LEN)
        st, ptr, _ = parse_current(blob, KR)
        self.assertEqual((st, ptr.publication_sha256, ptr.key_id), (P.VALID, b"\x33" * 32, 0))
        bad = bytearray(blob); bad[-1] ^= 0xFF
        self.assertEqual(parse_current(bytes(bad), KR)[0], P.CORRUPT)
        self.assertEqual(parse_current(blob[:-1], KR)[0], P.CORRUPT)    # comprimento fixo violado

    def test_durable_current_roundtrip_and_limits(self):
        d = Path(tempfile.mkdtemp())
        self.assertEqual(read_current(d, KR)[0], P.MISSING)            # antes de escrever
        write_current(d, serialize_current(b"\x44" * 32, 0, KEY))
        st, ptr, _ = read_current(d, KR)
        self.assertEqual((st, ptr.publication_sha256), (P.VALID, b"\x44" * 32))
        # rewrite atômico → novo alvo
        write_current(d, serialize_current(b"\x55" * 32, 0, KEY))
        self.assertEqual(read_current(d, KR)[1].publication_sha256, b"\x55" * 32)
        self.assertEqual(read_current(d, KR, max_bytes=8)[0], P.RESOURCE_LIMIT)   # limite antes de ler


if __name__ == "__main__":
    unittest.main()
