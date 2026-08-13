# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""V23-B3-0 — Manifest/WAL Contract Freeze: binding físico header↔descriptor, identidade vs posição
do segmento, linhagem v2 (SHA-256 completo + genesis exclusivo) e footer como única autoridade de
selagem. Exercita justamente as lacunas que os testes do B2 não cobriam."""

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
    DedupTable,
    Keyring,
    ObjectStore,
    make_descriptor,
)
from horizon_memory._engine.horizon_bulk import BulkSnapshot
from horizon_memory._engine.horizon_manifest import (
    EpochManifest,
    Lineage,
    OpenGenerationState as S,
    WalSegmentDescriptor,
    open_generation,
)
from horizon_memory._engine.horizon_store import FactRegistry
from horizon_memory._engine.horizon_walstore import WalStore
from horizon_memory._engine.residual_field import ResidualField, TombstoneLayer
from horizon_memory._engine.horizon_wal import (
    OP_PUT,
    STATE_ACTIVE,
    STATE_SEALED,
    _seal_footer,
    encode_frame,
    encode_segment_header,
    make_operation_id,
)

KEY = b"qhdre-v23-b30-binding-key-0123456"
SCOPE, GEN, FC = 7, 5, 2000
KR = Keyring({0: KEY})
Z16, Z32 = b"\x00" * 16, b"\x00" * 32


def _store():
    return WalStore(str(Path(tempfile.mkdtemp())), KR)


def _seg_blob(seg_id, first_seq, ops, *, sealed, prev=Z16, key_id=0, key=KEY):
    body = encode_segment_header(key, SCOPE, seg_id, first_seq, key_id=key_id,
                                 segment_state=STATE_SEALED if sealed else STATE_ACTIVE,
                                 previous_segment_digest=prev)
    seq = first_seq
    for (op, fid, fver, val, opid) in ops:
        payload = struct.pack("<I", val) if op == OP_PUT else b""
        body += encode_frame(key, SCOPE, seg_id, op, seq, opid, fid, fver, payload)
        seq += 1
    if sealed:
        off = len(body)
        last = first_seq + len(ops) - 1
        body += _seal_footer(key, SCOPE, seg_id, last, off, hashlib.sha256(body).digest()[:16])
    return body


def _desc(ordinal, segment_id, first_seq, blob, rc, sealed, *, prev=Z16, key_id=0,
          durable_len=None, durable_sha=None):
    sha = hashlib.sha256(blob).digest()
    st = STATE_SEALED if sealed else STATE_ACTIVE
    dl = durable_len if durable_len is not None else len(blob)
    dsha = durable_sha if durable_sha is not None else sha
    return WalSegmentDescriptor(ordinal, segment_id, first_seq, rc, st, dl, dsha, len(blob), sha,
                                prev, key_id)


def _base(store, H, *, through=None):
    through = H if through is None else through
    field = ResidualField.build(FC, {1: 9}, 128, SCOPE, GEN, KEY).serialize()
    tomb = TombstoneLayer.build(FC, {2}, SCOPE, GEN, KEY).serialize()
    reg = FactRegistry.build({1: (1, 1), 2: (2, 1), 3: (3, 1)}, SCOPE, GEN, FC, KEY).serialize()
    bulk = BulkSnapshot.build(FC, {1: 9, 2: 1, 3: 1}, SCOPE, GEN, KEY).serialize()
    dedup = DedupTable.build(SCOPE, GEN, through + 1, through, 2, [], {}, KEY).serialize()
    blobs = {ArtifactKind.REGISTRY: reg, ArtifactKind.BULK: bulk, ArtifactKind.RESIDUAL: field,
             ArtifactKind.TOMBSTONE: tomb, ArtifactKind.DEDUP: dedup}
    for b in blobs.values():
        store.put_object(b)
    return {k: make_descriptor(k, b) for k, b in blobs.items()}


def _man(base, segs, H, R, *, gen=GEN, scope=SCOPE, parent=-1, parent_digest=Z32, key_id=0):
    return EpochManifest(scope, gen, FC, H, R, parent, parent_digest, base, tuple(segs), key_id)


def _one_op(fid=100, val=900):
    return [(OP_PUT, fid, 1, val, make_operation_id(1, fid))]


class BindingTests(unittest.TestCase):
    """Correção 1: o header autenticado do WAL tem que casar TODOS os campos do descriptor."""

    def test_prev_digest_divergence_rejects(self):
        st = _store()
        base = _base(st, 10)
        blob = _seg_blob(1, 11, _one_op(), sealed=True, prev=b"\x33" * 16)  # header prev != ZERO
        st.put_object(blob)
        seg = _desc(0, 1, 11, blob, 1, True, prev=Z16)                      # descriptor prev == ZERO (coverage ok)
        r = open_generation(_man(base, [seg], 10, 11).serialize(KEY), st, st, KR)
        self.assertEqual(r.state, S.COVERAGE_ERROR)

    def test_first_seq_divergence_rejects(self):
        st = _store()
        base = _base(st, 10)
        blob = _seg_blob(1, 12, _one_op(), sealed=True)                     # header first_seq=12
        st.put_object(blob)
        seg = _desc(0, 1, 11, blob, 1, True)                                # descriptor first_seq=11 (coverage ok)
        r = open_generation(_man(base, [seg], 10, 11).serialize(KEY), st, st, KR)
        self.assertEqual(r.state, S.COVERAGE_ERROR)

    def test_key_id_divergence_rejects(self):
        kr = Keyring({0: KEY, 5: KEY})                                      # mesma chave, ids distintos
        st = WalStore(str(Path(tempfile.mkdtemp())), kr)                    # WalStore resolve ambos os ids
        base = _base(st, 10)
        blob = _seg_blob(1, 11, _one_op(), sealed=True, key_id=0)           # header key_id=0
        st.put_object(blob)
        seg = _desc(0, 1, 11, blob, 1, True, key_id=5)                      # descriptor key_id=5
        r = open_generation(_man(base, [seg], 10, 11, key_id=0).serialize(KEY), st, st, kr)
        self.assertEqual(r.state, S.COVERAGE_ERROR)

    def test_segment_id_divergence_rejects(self):
        st = _store()
        base = _base(st, 10)
        blob = _seg_blob(9, 11, _one_op(), sealed=True)                     # header segment_id=9
        st.put_object(blob)
        seg = _desc(0, 1, 11, blob, 1, True)                               # descriptor segment_id=1
        r = open_generation(_man(base, [seg], 10, 11).serialize(KEY), st, st, KR)
        self.assertNotEqual(r.state, S.VALID)                              # scan recusa (CORRUPT)


class IdentityTests(unittest.TestCase):
    """Correção 2: ordinal (posição) é distinto de segment_id (identidade persistente/monotônica)."""

    def test_ordinal_must_match_position(self):
        st = _store()
        base = _base(st, 10)
        blob = _seg_blob(1, 11, _one_op(), sealed=True)
        st.put_object(blob)
        seg = _desc(1, 1, 11, blob, 1, True)                               # ordinal=1, mas é o índice 0
        r = open_generation(_man(base, [seg], 10, 11).serialize(KEY), st, st, KR)
        self.assertEqual(r.state, S.COVERAGE_ERROR)

    def test_segment_id_must_be_monotonic(self):
        st = _store()
        base = _base(st, 10)
        s0 = _seg_blob(5, 11, _one_op(100), sealed=True)
        s1 = _seg_blob(3, 12, _one_op(101, 901), sealed=True, prev=hashlib.sha256(s0).digest()[:16])
        st.put_object(s0); st.put_object(s1)
        d0 = _desc(0, 5, 11, s0, 1, True)
        d1 = _desc(1, 3, 12, s1, 1, True, prev=hashlib.sha256(s0).digest()[:16])  # 3 <= 5 → não-monotônico
        r = open_generation(_man(base, [d0, d1], 10, 12).serialize(KEY), st, st, KR)
        self.assertEqual(r.state, S.COVERAGE_ERROR)


class LineageV2Tests(unittest.TestCase):
    """Correção 5: SHA-256 completo do pai, genesis exclusivo (-1), pai < filho, pai autenticado."""

    def _parent_blob(self, *, gen, scope=SCOPE):
        # manifesto pai mínimo (parse não abre os artefatos, só confere estrutura/MAC)
        base = {k: make_descriptor(k, b"x" * 8) for k in
                (ArtifactKind.REGISTRY, ArtifactKind.BULK, ArtifactKind.RESIDUAL,
                 ArtifactKind.TOMBSTONE, ArtifactKind.DEDUP)}
        return EpochManifest(scope, gen, FC, 0, 0, -1, Z32, base, (), 0).serialize(KEY)

    def test_genesis_is_exclusively_minus_one(self):
        st = _store()
        base = _base(st, 10)
        r = open_generation(_man(base, [], 10, 10, parent=-2, parent_digest=Z32).serialize(KEY),
                            st, st, KR)
        self.assertEqual(r.state, S.CORRUPT)

    def test_parent_must_be_lower_generation(self):
        st = _store()
        base = _base(st, 10)
        r = open_generation(_man(base, [], 10, 10, parent=GEN, parent_digest=b"\x11" * 32).serialize(KEY),
                            st, st, KR)
        self.assertEqual(r.state, S.CORRUPT)

    def test_parent_authenticated_and_matched_is_verified(self):
        st = _store()
        base = _base(st, 10)
        pblob = self._parent_blob(gen=4)
        pd = hashlib.sha256(pblob).digest()                                # SHA-256 COMPLETO (32B)
        man = _man(base, [], 10, 10, parent=4, parent_digest=pd)
        r = open_generation(man.serialize(KEY), st, st, KR, parent_manifest_blob=pblob)
        self.assertEqual(r.state, S.VALID)
        self.assertEqual(r.handle.lineage, Lineage.VERIFIED)

    def test_parent_sha_mismatch_rejects(self):
        st = _store()
        base = _base(st, 10)
        pblob = self._parent_blob(gen=4)
        man = _man(base, [], 10, 10, parent=4, parent_digest=b"\x11" * 32)  # digest não corresponde
        r = open_generation(man.serialize(KEY), st, st, KR, parent_manifest_blob=pblob)
        self.assertEqual(r.state, S.CORRUPT)

    def test_parent_wrong_generation_rejects(self):
        st = _store()
        base = _base(st, 10)
        pblob = self._parent_blob(gen=99)                                  # pai declara gen 99
        pd = hashlib.sha256(pblob).digest()
        man = _man(base, [], 10, 10, parent=4, parent_digest=pd)           # mas o filho aponta parent=4
        r = open_generation(man.serialize(KEY), st, st, KR, parent_manifest_blob=pblob)
        self.assertEqual(r.state, S.CORRUPT)


class FooterAuthorityTests(unittest.TestCase):
    """Correção 4: o footer é a única prova de selagem."""

    def test_sealed_without_footer_rejects(self):
        st = _store()
        base = _base(st, 10)
        blob = _seg_blob(1, 11, _one_op(), sealed=False)                   # sem footer
        st.put_object(blob)
        seg = _desc(0, 1, 11, blob, 1, True)                              # mas descriptor diz SEALED
        r = open_generation(_man(base, [seg], 10, 11).serialize(KEY), st, st, KR)
        # SEALED sem footer é objeto inválido (read_validated_segment recusa no scan) → CORRUPT
        self.assertEqual(r.state, S.CORRUPT)

    def test_active_noncanonical_sealed_rejects(self):
        # HEM2 congelado: ACTIVE tem sealed_object VAZIO (length==0, sha==zero32); um descriptor
        # ACTIVE com sealed_object preenchido (content-addressed) é não canônico → COVERAGE_ERROR.
        # A detecção de footer-dentro-do-ACTIVE (→ INCOMPATIBLE) exige LER o arquivo e é do B3-2.
        st = _store()
        base = _base(st, 10)
        blob = _seg_blob(1, 11, _one_op(), sealed=True)
        st.put_object(blob)
        seg = _desc(0, 1, 11, blob, 1, False)                             # ACTIVE com sealed_object != vazio
        r = open_generation(_man(base, [seg], 10, 11).serialize(KEY), st, st, KR)
        self.assertEqual(r.state, S.COVERAGE_ERROR)


class FormatVersionTests(unittest.TestCase):
    def test_v1_version_is_incompatible(self):
        st = _store()
        base = _base(st, 10)
        good = _man(base, [], 10, 10).serialize(KEY)
        v1 = bytearray(good); v1[4:6] = (1).to_bytes(2, "little")          # força format_version=1
        r = open_generation(bytes(v1), st, st, KR)
        self.assertEqual(r.state, S.INCOMPATIBLE)


if __name__ == "__main__":
    unittest.main()
