# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Testes do BulkSnapshot ordinal e da validação cruzada da base (V23-B1.1)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from horizon_memory._engine.horizon_bulk import (
    BULK_ABSENT,
    BULK_OUT_OF_RANGE,
    BULK_VALUE,
    BulkSnapshot,
)
from horizon_memory._engine.horizon_store import (
    BASE_ABSENT,
    BASE_ABSTAIN,
    BundleBaseView,
    FactRegistry,
    GenerationBundle,
    OP_PUT,
    ReadView,
    VersionedFact,
    validate_base_artifacts,
)
from horizon_memory._engine.residual_field import (
    IntegrityError,
    ResidualField,
    TombstoneLayer,
    open_tombstone,
)

KEY = b"unit-test-bulk-session-key-01234"
SCOPE, GEN, FC = 7, 5, 2000


class BulkSnapshotTests(unittest.TestCase):
    def test_roundtrip_and_states(self):
        vals = {3: 30, 7: 70, 100: 200}
        b = BulkSnapshot.build(FC, vals, SCOPE, GEN, KEY)
        r = BulkSnapshot.try_open(b.serialize(), KEY)
        self.assertEqual(r.serialize(), b.serialize())
        self.assertEqual(r.lookup(7), (BULK_VALUE, 70))
        self.assertEqual(r.lookup(8), (BULK_ABSENT, None))
        self.assertEqual(r.lookup(FC + 1), (BULK_OUT_OF_RANGE, None))
        self.assertEqual(r.support_ordinals(), set(vals))

    def test_ordinal_and_value_validation(self):
        with self.assertRaises(IntegrityError):
            BulkSnapshot.build(100, {-1: 5}, SCOPE, GEN, KEY)
        with self.assertRaises(IntegrityError):
            BulkSnapshot.build(100, {200: 5}, SCOPE, GEN, KEY)
        with self.assertRaises(IntegrityError):
            BulkSnapshot.build(100, {5: 999}, SCOPE, GEN, KEY)      # valor > u8

    def test_corruption_and_framing_rejected(self):
        blob = bytearray(BulkSnapshot.build(FC, {5: 9, 6: 8}, SCOPE, GEN, KEY).serialize())
        blob[-1] ^= 0xFF                                            # flip no MAC
        self.assertIsNone(BulkSnapshot.try_open(bytes(blob), KEY))
        good = BulkSnapshot.build(FC, {5: 9}, SCOPE, GEN, KEY).serialize()
        self.assertIsNone(BulkSnapshot.try_open(good + b"\x00", KEY))   # bytes extras
        self.assertIsNone(BulkSnapshot.try_open(good[:-2], KEY))        # truncado


class BuilderOrdinalTests(unittest.TestCase):
    def test_residual_and_tombstone_reject_bad_ordinal(self):
        with self.assertRaises(IntegrityError):
            ResidualField.build(100, {-1: 5}, 64, SCOPE, GEN, KEY)
        with self.assertRaises(IntegrityError):
            TombstoneLayer.build(100, {150}, SCOPE, GEN, KEY)


class CrossValidationTests(unittest.TestCase):
    def _parts(self, known, corrected, deleted, bulk_values):
        field = ResidualField.build(FC, corrected, 128, SCOPE, GEN, KEY)
        tomb = TombstoneLayer.build(FC, set(deleted), SCOPE, GEN, KEY)
        reg = FactRegistry.build({o: (o, 1) for o in known}, SCOPE, GEN, FC, KEY)
        bulk = BulkSnapshot.build(FC, bulk_values, SCOPE, GEN, KEY)
        return reg, bulk, field, tomb

    def test_valid_base(self):
        reg, bulk, field, tomb = self._parts([1, 2, 3], {1: 9}, [2], {1: 9, 2: 1, 3: 1})
        self.assertTrue(validate_base_artifacts(reg, bulk, field, tomb, SCOPE, GEN, FC).valid)

    def test_support_mismatch(self):
        reg, bulk, field, tomb = self._parts([1, 2, 3], {1: 9}, [2], {1: 9, 2: 1})   # falta 3
        self.assertFalse(validate_base_artifacts(reg, bulk, field, tomb, SCOPE, GEN, FC).valid)

    def test_residual_tombstone_overlap(self):
        reg, bulk, field, tomb = self._parts([1, 2, 3], {1: 9}, [1, 2], {1: 9, 2: 1, 3: 1})  # 1 corrigido+apagado
        self.assertFalse(validate_base_artifacts(reg, bulk, field, tomb, SCOPE, GEN, FC).valid)


class BaseViewBulkTests(unittest.TestCase):
    def test_registry_known_bulk_missing_is_abstain(self):
        field = ResidualField.build(FC, {}, 128, SCOPE, GEN, KEY)
        tomb = open_tombstone(TombstoneLayer.build(FC, set(), SCOPE, GEN, KEY).serialize(), KEY, required=True)
        reg = FactRegistry.build({5: (5, 10)}, SCOPE, GEN, FC, KEY)
        bulk = BulkSnapshot.build(FC, {}, SCOPE, GEN, KEY)
        base = BundleBaseView(ReadView(GEN, SCOPE, 0, 0),
                              GenerationBundle(SCOPE, GEN, field, reg, tomb, bulk))
        self.assertIs(base.lookup(5), BASE_ABSTAIN)
        self.assertIs(base.lookup(999), BASE_ABSENT)


if __name__ == "__main__":
    unittest.main()
