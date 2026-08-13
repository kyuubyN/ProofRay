# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Testes do EpochManifest (V23-B2): parse+MAC, lei de cobertura, redução multissegmento por uma
autoridade única, link do dedup, linhagem e a política CURRENT→ABSTAIN."""

from __future__ import annotations

import hashlib
import struct
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from horizon_memory._engine.horizon_artifacts import (
    ArtifactKind,
    ArtifactLimits,
    Keyring,
    ObjectReadResult,
    ObjectStore,
    make_descriptor,
)
from horizon_memory._engine.horizon_bulk import BulkSnapshot
from horizon_memory._engine.horizon_manifest import (
    EpochManifest,
    Lineage,
    OpenGenerationState,
    WalSegmentDescriptor,
    open_generation,
    resolve_current,
)
from horizon_memory._engine.horizon_store import FactRegistry
from horizon_memory._engine.residual_field import ResidualField, TombstoneLayer
from horizon_memory._engine.horizon_artifacts import DedupTable
from horizon_memory._engine.horizon_walstore import WalStore
from horizon_memory._engine.horizon_wal import (
    OP_DELETE,
    OP_PUT,
    STATE_ACTIVE,
    STATE_SEALED,
    _seal_footer,
    encode_frame,
    encode_segment_header,
    make_operation_id,
)

KEY = b"qhdre-v23-b2-manifest-key-012345"
SCOPE, GEN, FC = 7, 5, 2000
KR = Keyring({0: KEY})
ZERO16 = b"\x00" * 16
ZERO32 = b"\x00" * 32


def _store():
    # WalStore serve como object_store (delega put_object/get_limited) E como wal_store (open_sealed);
    # SEALED em objects/, ACTIVE por identidade — drop-in para open_generation (B3-2).
    return WalStore(str(Path(tempfile.mkdtemp())), KR)


def _seg_blob(seg_id, first_seq, ops, *, sealed, prev=ZERO16, key_id=0):
    body = encode_segment_header(KEY, SCOPE, seg_id, first_seq, key_id=key_id,
                                 segment_state=STATE_SEALED if sealed else STATE_ACTIVE,
                                 previous_segment_digest=prev)
    seq = first_seq
    for (op, fid, fver, val, opid) in ops:
        payload = struct.pack("<I", val) if op == OP_PUT else b""
        body += encode_frame(KEY, SCOPE, seg_id, op, seq, opid, fid, fver, payload)
        seq += 1
    if sealed:
        off = len(body)
        last_seq = first_seq + len(ops) - 1
        body += _seal_footer(KEY, SCOPE, seg_id, last_seq, off, hashlib.sha256(body).digest()[:16])
    return body


def _seg_desc(ordinal, segment_id, first_seq, blob, rc, sealed, prev=ZERO16, key_id=0):
    sha = hashlib.sha256(blob).digest()
    st = STATE_SEALED if sealed else STATE_ACTIVE
    return WalSegmentDescriptor(ordinal, segment_id, first_seq, rc, st, len(blob), sha,
                                len(blob), sha, prev, key_id)


def _base_descs(store, H, *, through=None, drop=None):
    through = H if through is None else through
    field = ResidualField.build(FC, {1: 9}, 128, SCOPE, GEN, KEY).serialize()
    tomb = TombstoneLayer.build(FC, {2}, SCOPE, GEN, KEY).serialize()
    reg = FactRegistry.build({1: (1, 1), 2: (2, 1), 3: (3, 1)}, SCOPE, GEN, FC, KEY).serialize()
    bulk = BulkSnapshot.build(FC, {1: 9, 2: 1, 3: 1}, SCOPE, GEN, KEY).serialize()
    dedup = DedupTable.build(SCOPE, GEN, through + 1, through, 2, [], {}, KEY).serialize()
    blobs = {ArtifactKind.REGISTRY: reg, ArtifactKind.BULK: bulk, ArtifactKind.RESIDUAL: field,
             ArtifactKind.TOMBSTONE: tomb, ArtifactKind.DEDUP: dedup}
    for k, b in blobs.items():
        if k != drop:
            store.put_object(b)
    return {k: make_descriptor(k, b) for k, b in blobs.items()}


def _manifest(base, segs, H, R, *, parent=-1, parent_digest=ZERO32):
    return EpochManifest(SCOPE, GEN, FC, H, R, parent, parent_digest, base, tuple(segs), 0)


def _valid_case(store, H=10):
    """Base em H, um segmento SEALED cobrindo H+1..H+3 com 3 fatos novos."""
    base = _base_descs(store, H)
    ops = [(OP_PUT, 100, 1, 900, make_operation_id(1, 1)),
           (OP_PUT, 101, 1, 901, make_operation_id(1, 2)),
           (OP_PUT, 102, 1, 902, make_operation_id(1, 3))]
    blob = _seg_blob(1, H + 1, ops, sealed=True)      # segment_id=1 (identidade), ordinal=0 (posição)
    store.put_object(blob)
    seg = _seg_desc(0, 1, H + 1, blob, 3, True)
    return base, [seg]


class ManifestTests(unittest.TestCase):
    # G1 -----------------------------------------------------------------
    def test_valid_opens_and_reads(self):
        st = _store()
        base, segs = _valid_case(st)
        man = _manifest(base, segs, 10, 13)
        res = open_generation(man.serialize(KEY), st, st, KR)
        self.assertEqual(res.state, OpenGenerationState.VALID)
        self.assertEqual(res.handle.lineage, Lineage.GENESIS)
        self.assertEqual(res.handle.read_fact(100).value, 900)   # do WAL
        self.assertEqual(res.handle.read_fact(1).value, 9)       # da base
        self.assertEqual(res.handle.read_fact(2).status, "deleted")  # tombstone da base

    # G2 -----------------------------------------------------------------
    def test_missing_required_base_is_required_missing(self):
        st = _store()
        base = _base_descs(st, 10, drop=ArtifactKind.DEDUP)   # dedup não publicado
        man = _manifest(base, [], 10, 10)
        res = open_generation(man.serialize(KEY), st, st, KR)
        self.assertEqual(res.state, OpenGenerationState.REQUIRED_MISSING)

    # G3 -----------------------------------------------------------------
    def test_store_errors_are_distinct(self):
        st = _store()
        base, segs = _valid_case(st)
        blob = _manifest(base, segs, 10, 13).serialize(KEY)
        # COUNT_LIMIT → RESOURCE_LIMIT
        res = open_generation(blob, st, st, KR, ArtifactLimits(max_blob_bytes=8))
        self.assertEqual(res.state, OpenGenerationState.RESOURCE_LIMIT)

        # CONCURRENT_CHANGE → CORRUPT (nunca 'missing')
        target = base[ArtifactKind.DEDUP].sha256.hex()

        class _CC:
            def __init__(self, inner):
                self.inner = inner

            def get_limited(self, d, m):
                if d == target:
                    return ObjectReadResult(False, None, "CONCURRENT_CHANGE")
                return self.inner.get_limited(d, m)
        res2 = open_generation(blob, _CC(st), st, KR)
        self.assertEqual(res2.state, OpenGenerationState.CORRUPT)

    # G4 -----------------------------------------------------------------
    def test_coverage_gap(self):
        st = _store()
        base, segs = _valid_case(st)
        bad = replace(segs[0], first_seq=12)     # esperado 11
        res = open_generation(_manifest(base, [bad], 10, 13).serialize(KEY), st, st, KR)
        self.assertEqual(res.state, OpenGenerationState.COVERAGE_ERROR)

    # G5 -----------------------------------------------------------------
    def test_coverage_sum_vs_R(self):
        st = _store()
        base, segs = _valid_case(st)
        res = open_generation(_manifest(base, segs, 10, 14).serialize(KEY), st, st, KR)  # R errado
        self.assertEqual(res.state, OpenGenerationState.COVERAGE_ERROR)

    # G6 -----------------------------------------------------------------
    def test_active_and_sealed_rules(self):
        st = _store()
        base = _base_descs(st, 10)
        s0 = _seg_blob(1, 11, [(OP_PUT, 100, 1, 900, make_operation_id(1, 1))], sealed=False)
        s1 = _seg_blob(2, 12, [(OP_PUT, 101, 1, 901, make_operation_id(1, 2))], sealed=True)
        st.put_object(s0); st.put_object(s1)
        d0 = _seg_desc(0, 1, 11, s0, 1, False)                    # ACTIVE não-final
        d1 = _seg_desc(1, 2, 12, s1, 1, True, prev=hashlib.sha256(s0).digest()[:16])
        res = open_generation(_manifest(base, [d0, d1], 10, 12).serialize(KEY), st, st, KR)
        self.assertEqual(res.state, OpenGenerationState.COVERAGE_ERROR)
        # último SEALED vazio
        empty = _seg_blob(1, 11, [], sealed=True)
        st.put_object(empty)
        de = _seg_desc(0, 1, 11, empty, 0, True)
        res2 = open_generation(_manifest(base, [de], 10, 10).serialize(KEY), st, st, KR)
        self.assertEqual(res2.state, OpenGenerationState.COVERAGE_ERROR)

    # G7 -----------------------------------------------------------------
    def test_broken_chain(self):
        st = _store()
        base = _base_descs(st, 10)
        s0 = _seg_blob(1, 11, [(OP_PUT, 100, 1, 900, make_operation_id(1, 1))], sealed=True)
        s1 = _seg_blob(2, 12, [(OP_PUT, 101, 1, 901, make_operation_id(1, 2))], sealed=True,
                       prev=hashlib.sha256(s0).digest()[:16])   # blob ok; o prev do DESCRIPTOR é que quebra
        st.put_object(s0); st.put_object(s1)
        d0 = _seg_desc(0, 1, 11, s0, 1, True)
        d1 = _seg_desc(1, 2, 12, s1, 1, True, prev=b"\x22" * 16)   # não encadeia s0
        res = open_generation(_manifest(base, [d0, d1], 10, 13).serialize(KEY), st, st, KR)
        self.assertEqual(res.state, OpenGenerationState.COVERAGE_ERROR)

    # G8 -----------------------------------------------------------------
    def test_no_wal_requires_R_eq_H(self):
        st = _store()
        base = _base_descs(st, 10)
        ok = open_generation(_manifest(base, [], 10, 10).serialize(KEY), st, st, KR)
        self.assertEqual(ok.state, OpenGenerationState.VALID)
        bad = open_generation(_manifest(base, [], 10, 11).serialize(KEY), st, st, KR)
        self.assertEqual(bad.state, OpenGenerationState.COVERAGE_ERROR)

    # G9 -----------------------------------------------------------------
    def test_dedup_through_must_equal_H(self):
        st = _store()
        base = _base_descs(st, 10, through=9)     # dedup cobre só até 9, mas H=10
        res = open_generation(_manifest(base, [], 10, 10).serialize(KEY), st, st, KR)
        self.assertEqual(res.state, OpenGenerationState.COVERAGE_ERROR)

    # G10 ----------------------------------------------------------------
    def test_manifest_mac_and_version(self):
        st = _store()
        base, segs = _valid_case(st)
        good = _manifest(base, segs, 10, 13).serialize(KEY)
        tampered = bytearray(good); tampered[-1] ^= 0xFF
        self.assertEqual(open_generation(bytes(tampered), st, st, KR).state,
                         OpenGenerationState.CORRUPT)
        badver = bytearray(good); badver[4:6] = (99).to_bytes(2, "little")   # format_version
        self.assertEqual(open_generation(bytes(badver), st, st, KR).state,
                         OpenGenerationState.INCOMPATIBLE)

    # G11 ----------------------------------------------------------------
    def test_conflicting_frame_is_corrupt(self):
        st = _store()
        base = _base_descs(st, 10)
        # PUT no fato 1 (base fver=1, valor 9) com fver=1 e valor diferente → conflito vs base
        ops = [(OP_PUT, 1, 1, 777, make_operation_id(9, 9))]
        blob = _seg_blob(1, 11, ops, sealed=True)
        st.put_object(blob)
        seg = _seg_desc(0, 1, 11, blob, 1, True)
        res = open_generation(_manifest(base, [seg], 10, 11).serialize(KEY), st, st, KR)
        self.assertEqual(res.state, OpenGenerationState.CORRUPT)

    def test_corrupted_segment_object_is_corrupt(self):
        st = _store()
        base, segs = _valid_case(st)
        seg_hex = segs[0].sealed_object_sha256.hex()
        p = st._path(seg_hex)
        data = bytearray(p.read_bytes()); data[-20] ^= 0xFF      # mesmo tamanho, conteúdo diverge
        p.write_bytes(bytes(data))
        res = open_generation(_manifest(base, segs, 10, 13).serialize(KEY), st, st, KR)
        self.assertEqual(res.state, OpenGenerationState.CORRUPT)

    # G12 ----------------------------------------------------------------
    def test_lineage_unverified_and_current_abstains(self):
        st = _store()
        base, segs = _valid_case(st)
        man = _manifest(base, segs, 10, 13, parent=4, parent_digest=b"\x11" * 32)
        res = open_generation(man.serialize(KEY), st, st, KR)     # sem blob do pai
        self.assertEqual(res.state, OpenGenerationState.VALID)
        self.assertEqual(res.handle.lineage, Lineage.UNVERIFIED)  # não conferida, não fatal
        # CURRENT inválido → ABSTAIN (nunca abre o pai)
        bad = bytearray(man.serialize(KEY)); bad[-1] ^= 0xFF
        dec = resolve_current(bytes(bad), st, st, KR)
        self.assertTrue(dec.abstained)
        self.assertFalse(dec.opened)


if __name__ == "__main__":
    unittest.main()
