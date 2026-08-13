# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Testes da camada de sistema (V23.0/V23.0.1): identidade autenticada, consumidor ligado à
ReadView, deleção terminal, L0-first por FactId e invariantes do registry."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from horizon_memory._engine.horizon_bulk import BulkSnapshot
from horizon_memory._engine.horizon_store import (
    OP_DELETE,
    OP_PUT,
    FactRegistry,
    GenerationBundle,
    ReadView,
    SequentialModel,
    WalIndex,
    read,
)
from horizon_memory._engine.residual_field import (
    ResidualField,
    TombstoneLayer,
    open_tombstone,
)

KEY = b"unit-test-v23-session-key-012345"
SCOPE, GEN, PAGE, FC = 7, 3, 64, 2000


def _world():
    corrected = {10: 111, 20: 122, 30: 133}
    bulk_only = {40: 44, 50: 55, 60: 66}
    deleted = [70, 80]
    bulk_values = {**bulk_only, 10: 1, 20: 2, 30: 3, 70: 70, 80: 80}   # por ordinal (u8)
    bulk = BulkSnapshot.build(FC, bulk_values, SCOPE, GEN, KEY)
    field = ResidualField.build(FC, corrected, PAGE, SCOPE, GEN, KEY)
    tomb = open_tombstone(TombstoneLayer.build(FC, set(deleted), SCOPE, GEN, KEY).serialize(),
                          KEY, required=True)
    reg_map = {k: (k, 1) for k in list(corrected) + list(bulk_only) + deleted}
    registry = FactRegistry.build(reg_map, SCOPE, GEN, FC, KEY)
    bundle = GenerationBundle(SCOPE, GEN, field, registry, tomb, bulk)
    view = ReadView(GEN, SCOPE, base_seq=0, read_seq=0)
    return dict(bundle=bundle, view=view, l0=WalIndex(), corrected=corrected,
                bulk_only=bulk_only, deleted=deleted, bulk=bulk, registry=registry, field=field, tomb=tomb)


class RegistryInvariantTests(unittest.TestCase):
    def test_lookup_roundtrip(self):
        reg = FactRegistry.try_open(_world()["registry"].serialize(), KEY)
        self.assertEqual(reg.lookup(10), (10, 1))
        self.assertIsNone(reg.lookup(999))

    def test_corrupt_registry_fails_to_open(self):
        reg = _world()["registry"]
        b = bytearray(reg.serialize()); b[reg._tag_off] ^= 0xFF
        self.assertIsNone(FactRegistry.try_open(bytes(b), KEY))

    def test_trailing_bytes_rejected(self):
        reg = _world()["registry"]
        self.assertIsNone(FactRegistry.try_open(reg.serialize() + b"\x00", KEY))

    def test_ordinal_alias_rejected(self):
        # dois FactId → mesmo ordinal deve falhar a validação de identidade
        with self.assertRaises(Exception):
            FactRegistry.build({1: (5, 1), 2: (5, 1)}, SCOPE, GEN, FC, KEY)

    def test_ordinal_out_of_range_rejected(self):
        with self.assertRaises(Exception):
            FactRegistry.build({1: (FC + 3, 1)}, SCOPE, GEN, FC, KEY)


class ConsumerTests(unittest.TestCase):
    def test_corrected_returns_residual_value(self):
        w = _world()
        r = read(w["view"], w["bundle"], w["l0"], 10)
        self.assertEqual((r.status, r.value, r.authoritative), ("correct", 111, True))

    def test_bulk_only_reads_bulk(self):
        w = _world()
        r = read(w["view"], w["bundle"], w["l0"], 40)
        self.assertEqual((r.status, r.value, r.source), ("from_bulk", 44, "bulk"))

    def test_deleted_is_terminal_never_bulk(self):
        w = _world()
        for fid in w["deleted"]:
            r = read(w["view"], w["bundle"], w["l0"], fid)
            self.assertEqual(r.status, "deleted")
            self.assertIsNone(r.value)
            self.assertNotEqual(r.source, "bulk")

    def test_l0_delete_shadows_generation_first(self):
        """L0-first: um DELETE recente (por FactId) vence a geração, mesmo para um fato corrigido."""
        w = _world()
        l0 = WalIndex(); l0.apply(10, version=99, op=OP_DELETE, value=None)
        r = read(w["view"], w["bundle"], l0, 10)
        self.assertEqual(r.status, "deleted")
        self.assertEqual(r.source, "l0")

    def test_l0_put_for_fact_born_after_generation(self):
        """Um fato criado após a geração não existe no registry, mas o PUT L0 o resolve."""
        w = _world()
        l0 = WalIndex(); l0.apply(12345, version=100, op=OP_PUT, value=222)
        r = read(w["view"], w["bundle"], l0, 12345)
        self.assertEqual((r.status, r.value, r.source), ("correct", 222, "l0"))

    def test_generation_mismatch_abstains(self):
        w = _world()
        other = FactRegistry.build({10: (10, 1)}, SCOPE, GEN + 1, FC, KEY)  # outra geração
        bundle = GenerationBundle(SCOPE, GEN, w["field"], other, w["tomb"], w["bulk"])
        r = read(w["view"], bundle, w["l0"], 10)
        self.assertEqual(r.status, "abstain")

    def test_required_missing_tombstone_abstains(self):
        w = _world()
        bundle = GenerationBundle(SCOPE, GEN, w["field"], w["registry"],
                                  open_tombstone(None, KEY, required=True), w["bulk"])
        r = read(w["view"], bundle, w["l0"], 40)
        self.assertEqual(r.status, "abstain")

    def test_missing_registry_abstains(self):
        w = _world()
        bundle = GenerationBundle(SCOPE, GEN, w["field"], None, w["tomb"], w["bulk"])
        r = read(w["view"], bundle, w["l0"], 10)
        self.assertEqual(r.status, "abstain")


class SequentialModelTests(unittest.TestCase):
    def test_delete_downgrade_rejected(self):
        """O bug de downgrade: DELETE v=5 após DELETE v=10 é stale; PUT v=7 não ressuscita."""
        m = SequentialModel()
        m.delete(1, version=10)
        self.assertEqual(m.delete(1, version=5), "stale")
        self.assertEqual(m.put(1, 42, version=7), "stale")
        self.assertEqual(m.read(1), ("deleted", None))

    def test_higher_version_wins(self):
        m = SequentialModel()
        m.delete(1, version=3)
        m.put(1, 9, version=4)
        self.assertEqual(m.read(1), ("value", 9))

    def test_equal_version_conflict(self):
        m = SequentialModel()
        m.put(1, 1, version=5)
        self.assertEqual(m.put(1, 2, version=5), "conflict")
        self.assertEqual(m.put(1, 1, version=5), "idempotent")


if __name__ == "__main__":
    unittest.main()
