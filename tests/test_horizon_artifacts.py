# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Testes da abertura tipada, object store e DedupTable (V23-B1.2/1.3/1.4)."""

from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from horizon_memory._engine.horizon_artifacts import (
    ArtifactDescriptor,
    ArtifactKind,
    ArtifactLimits,
    ArtifactOpenState,
    DESCRIPTOR_VERSION,
    DedupTable,
    Keyring,
    ObjectStore,
    OpenBaseState,
    bundle_from_validated,
    open_artifact,
    open_base_artifacts,
)
from horizon_memory._engine.horizon_bulk import BulkSnapshot
from horizon_memory._engine.horizon_store import FactRegistry, ReadView, WalIndex, read
from horizon_memory._engine.horizon_wal import make_operation_id
from horizon_memory._engine.residual_field import ResidualField, TombstoneLayer

KEY = b"unit-test-artifacts-key-01234567"
SCOPE, GEN, FC = 7, 5, 2000


def _desc(kind, blob, required=True):
    return ArtifactDescriptor(kind, DESCRIPTOR_VERSION[kind], required, len(blob),
                              hashlib.sha256(blob).digest(), 0)


class TypedOpenTests(unittest.TestCase):
    def setUp(self):
        self.blob = BulkSnapshot.build(FC, {3: 30, 7: 70}, SCOPE, GEN, KEY).serialize()
        self.desc = _desc(ArtifactKind.BULK, self.blob)

    def test_valid(self):
        self.assertEqual(open_artifact(self.desc, self.blob, KEY).state, ArtifactOpenState.VALID)

    def test_missing_required_vs_optional(self):
        self.assertEqual(open_artifact(self.desc, None, KEY).state, ArtifactOpenState.REQUIRED_MISSING)
        opt = ArtifactDescriptor(ArtifactKind.BULK, 1, False, len(self.blob), self.desc.sha256, 0)
        self.assertEqual(open_artifact(opt, None, KEY).state, ArtifactOpenState.ABSENT_ALLOWED)

    def test_digest_and_length(self):
        self.assertEqual(open_artifact(
            ArtifactDescriptor(ArtifactKind.BULK, 1, True, len(self.blob) + 1, self.desc.sha256, 0),
            self.blob, KEY).state, ArtifactOpenState.CORRUPT)
        self.assertEqual(open_artifact(
            ArtifactDescriptor(ArtifactKind.BULK, 1, True, len(self.blob), b"\x00" * 32, 0),
            self.blob, KEY).state, ArtifactOpenState.CORRUPT)

    def test_incompatible_and_resource_limit(self):
        incompat = bytearray(self.blob); incompat[4:6] = (99).to_bytes(2, "little")
        self.assertEqual(open_artifact(_desc(ArtifactKind.BULK, bytes(incompat)), bytes(incompat), KEY).state,
                         ArtifactOpenState.INCOMPATIBLE)
        self.assertEqual(open_artifact(self.desc, self.blob, KEY, ArtifactLimits(max_blob_bytes=8)).state,
                         ArtifactOpenState.RESOURCE_LIMIT)

    def test_optional_corrupt_not_absent(self):
        bad = bytearray(self.blob); bad[-1] ^= 0xFF
        d = ArtifactDescriptor(ArtifactKind.BULK, 1, False, len(bad), hashlib.sha256(bytes(bad)).digest(), 0)
        self.assertEqual(open_artifact(d, bytes(bad), KEY).state, ArtifactOpenState.CORRUPT)


class ObjectStoreTests(unittest.TestCase):
    def test_content_addressed_and_no_overwrite(self):
        store = ObjectStore(str(Path(tempfile.mkdtemp())))
        blob = BulkSnapshot.build(FC, {1: 1}, SCOPE, GEN, KEY).serialize()
        d = store.put_object(blob)
        self.assertEqual(d, hashlib.sha256(blob).hexdigest())
        self.assertEqual(store.get(d), blob)
        self.assertEqual(store.put_object(blob), d)          # idempotente
        store._path(d).write_bytes(b"divergent")             # corrompe o objeto
        with self.assertRaises(Exception):
            store.put_object(blob)                           # nunca sobrescreve divergente

    def test_path_traversal_blocked(self):
        store = ObjectStore(str(Path(tempfile.mkdtemp())))
        with self.assertRaises(ValueError):
            store._path("../../etc/passwd")


class DedupTableTests(unittest.TestCase):
    def test_roundtrip_and_empty(self):
        A = make_operation_id(1, 5)
        dt = DedupTable.build(SCOPE, GEN, 1, 20, 2, [(A, b"\x01" * 16, 10)], {1: (5, 1)}, KEY)
        self.assertIsNotNone(DedupTable.try_open(dt.serialize(), KEY))
        empty = DedupTable.build(SCOPE, GEN, 21, 20, 2, [], {}, KEY)   # floor==through+1
        self.assertIsNotNone(DedupTable.try_open(empty.serialize(), KEY))

    def test_invariants_and_framing(self):
        A = make_operation_id(1, 5)
        with self.assertRaises(Exception):                   # entry sem client
            DedupTable.build(SCOPE, GEN, 1, 20, 2, [(A, b"\x01" * 16, 10)], {}, KEY)
        good = DedupTable.build(SCOPE, GEN, 1, 20, 2, [(A, b"\x01" * 16, 10)], {1: (5, 1)}, KEY).serialize()
        bad = bytearray(good); bad[-1] ^= 0xFF
        self.assertIsNone(DedupTable.try_open(bytes(bad), KEY))
        self.assertIsNone(DedupTable.try_open(good + b"\x00", KEY))
        self.assertIsNone(DedupTable.try_open(good[:-2], KEY))


class FactoryTests(unittest.TestCase):
    def _put_base(self, store, bulk_values):
        field = ResidualField.build(FC, {1: 9}, 128, SCOPE, GEN, KEY).serialize()
        tomb = TombstoneLayer.build(FC, {2}, SCOPE, GEN, KEY).serialize()
        reg = FactRegistry.build({1: (1, 1), 2: (2, 1), 3: (3, 1)}, SCOPE, GEN, FC, KEY).serialize()
        bulk = BulkSnapshot.build(FC, bulk_values, SCOPE, GEN, KEY).serialize()
        for b in (field, tomb, reg, bulk):
            store.put_object(b)
        return {ArtifactKind.REGISTRY: _desc(ArtifactKind.REGISTRY, reg),
                ArtifactKind.BULK: _desc(ArtifactKind.BULK, bulk),
                ArtifactKind.RESIDUAL: _desc(ArtifactKind.RESIDUAL, field),
                ArtifactKind.TOMBSTONE: _desc(ArtifactKind.TOMBSTONE, tomb)}

    def test_valid_builds_bundle(self):
        store = ObjectStore(str(Path(tempfile.mkdtemp())))
        desc = self._put_base(store, {1: 9, 2: 1, 3: 1})
        res = open_base_artifacts(desc, store, SCOPE, GEN, FC, Keyring({0: KEY}))
        self.assertEqual(res.state, OpenBaseState.VALID)
        bundle = bundle_from_validated(res.validated)
        self.assertEqual(read(ReadView(GEN, SCOPE, 0, 0), bundle, WalIndex(), 1).value, 9)

    def test_incoherent_base_rejected(self):
        store = ObjectStore(str(Path(tempfile.mkdtemp())))
        desc = self._put_base(store, {1: 9, 2: 1})   # falta ordinal 3 → support != registry
        res = open_base_artifacts(desc, store, SCOPE, GEN, FC, Keyring({0: KEY}))
        self.assertEqual(res.state, OpenBaseState.INVALID)
        self.assertIsNone(res.validated)


class StoreBoundaryTests(unittest.TestCase):
    """V23-B2-0: get_limited, put_object sem substituição, descriptor autovalidado, base selada."""

    def test_get_limited_size_first(self):
        store = ObjectStore(str(Path(tempfile.mkdtemp())))
        blob = BulkSnapshot.build(FC, {1: 1}, SCOPE, GEN, KEY).serialize()
        d = store.put_object(blob)
        self.assertTrue(store.get_limited(d, len(blob)).ok)
        over = store.get_limited(d, len(blob) - 1)
        self.assertFalse(over.ok)
        self.assertEqual(over.reason, "COUNT_LIMIT")

    def test_put_no_overwrite_and_no_leftover(self):
        import os
        store = ObjectStore(str(Path(tempfile.mkdtemp())))
        blob = BulkSnapshot.build(FC, {1: 1}, SCOPE, GEN, KEY).serialize()
        d = store.put_object(blob)
        self.assertEqual(store.put_object(blob), d)          # idempotente
        store._path(d).unlink(); store._path(d).write_bytes(b"divergent")
        with self.assertRaises(Exception):
            store.put_object(blob)
        self.assertFalse(list((store.root / "objects").glob("*.tmp.*")))

    def test_descriptor_self_validation(self):
        from dataclasses import replace
        from horizon_memory._engine.horizon_artifacts import Keyring
        blob = BulkSnapshot.build(FC, {1: 1}, SCOPE, GEN, KEY).serialize()
        good = _desc(ArtifactKind.BULK, blob)
        kr = Keyring({0: KEY})
        self.assertEqual(open_artifact(replace(good, sha256=b"\x00" * 16), blob, keyring=kr).state,
                         ArtifactOpenState.CORRUPT)
        self.assertEqual(open_artifact(replace(good, format_version=2), blob, keyring=kr).state,
                         ArtifactOpenState.INCOMPATIBLE)
        self.assertEqual(open_artifact(replace(good, key_id=99), blob, keyring=kr).state,
                         ArtifactOpenState.INCOMPATIBLE)
        self.assertEqual(open_artifact(good, blob, keyring=kr).state, ArtifactOpenState.VALID)

    def test_sealed_bundle_only(self):
        from horizon_memory._engine.horizon_artifacts import Keyring, ValidatedBaseArtifacts
        store = ObjectStore(str(Path(tempfile.mkdtemp())))
        field = ResidualField.build(FC, {1: 9}, 128, SCOPE, GEN, KEY).serialize()
        tomb = TombstoneLayer.build(FC, set(), SCOPE, GEN, KEY).serialize()
        reg = FactRegistry.build({1: (1, 1)}, SCOPE, GEN, FC, KEY).serialize()
        bulk = BulkSnapshot.build(FC, {1: 9}, SCOPE, GEN, KEY).serialize()
        for b in (field, tomb, reg, bulk):
            store.put_object(b)
        desc = {ArtifactKind.REGISTRY: _desc(ArtifactKind.REGISTRY, reg),
                ArtifactKind.BULK: _desc(ArtifactKind.BULK, bulk),
                ArtifactKind.RESIDUAL: _desc(ArtifactKind.RESIDUAL, field),
                ArtifactKind.TOMBSTONE: _desc(ArtifactKind.TOMBSTONE, tomb)}
        res = open_base_artifacts(desc, store, SCOPE, GEN, FC, Keyring({0: KEY}))
        self.assertIsNotNone(bundle_from_validated(res.validated))
        forged = ValidatedBaseArtifacts(SCOPE, GEN, FC, res.validated.registry, res.validated.bulk,
                                        res.validated.residual, res.validated.tombstone)
        with self.assertRaises(Exception):
            bundle_from_validated(forged)                     # sem selo → recusado

    def test_max_pages_enforced(self):
        from horizon_memory._engine.horizon_artifacts import Keyring
        corr = {i: (i % 250) + 1 for i in range(300)}
        field = ResidualField.build(FC, corr, 16, SCOPE, GEN, KEY).serialize()
        d = _desc(ArtifactKind.RESIDUAL, field)
        kr = Keyring({0: KEY})
        self.assertEqual(open_artifact(d, field, keyring=kr, limits=ArtifactLimits(max_pages=4)).state,
                         ArtifactOpenState.RESOURCE_LIMIT)
        self.assertEqual(open_artifact(d, field, keyring=kr).state, ArtifactOpenState.VALID)


if __name__ == "__main__":
    unittest.main()
