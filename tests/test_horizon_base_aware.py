# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Testes do preflight/recovery conscientes da base (V23-B0): base+WAL como uma linha de versão."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from horizon_memory._engine.horizon_batch import ABSTAIN_BASE, GroupCommitStore
from horizon_memory._engine.horizon_bulk import BulkSnapshot
from horizon_memory._engine.horizon_store import (
    BASE_ABSENT,
    BundleBaseView,
    FactRegistry,
    GenerationBundle,
    OP_DELETE,
    OP_PUT,
    ReadView,
    VersionedFact,
    preflight,
)
from horizon_memory._engine.horizon_wal import (
    APPLIED,
    CONFLICT,
    IDEMPOTENT,
    STALE_REJECTED,
    VERSION_CONFLICT,
    make_operation_id,
    reduce_frames,
)
from horizon_memory._engine.residual_field import ResidualField, TombstoneLayer, open_tombstone

KEY = b"unit-test-b0-session-key-0123456"
SCOPE, GEN, FC = 7, 3, 2000


def _base(facts, corrupt_registry=False):
    corrections = {f: v[2] for f, v in facts.items() if v[1] == OP_PUT}
    deleted = {f for f, v in facts.items() if v[1] == OP_DELETE}
    field = ResidualField.build(FC, corrections, 128, SCOPE, GEN, KEY)
    tomb = open_tombstone(TombstoneLayer.build(FC, deleted, SCOPE, GEN, KEY).serialize(), KEY, required=True)
    reg = FactRegistry.build({f: (f, v[0]) for f, v in facts.items()}, SCOPE, GEN, FC, KEY)
    registry = None if corrupt_registry else FactRegistry.try_open(reg.serialize(), KEY)
    bulk_values = {f: (v[2] if v[2] is not None else 1) for f, v in facts.items()}
    bulk = BulkSnapshot.build(FC, bulk_values, SCOPE, GEN, KEY)
    bundle = GenerationBundle(SCOPE, GEN, field, registry, tomb, bulk)
    return BundleBaseView(ReadView(GEN, SCOPE, 0, 0), bundle)


class PreflightTests(unittest.TestCase):
    def test_pure_rules(self):
        self.assertEqual(preflight(None, OP_PUT, 1, 5), "applied")
        self.assertEqual(preflight((10, OP_PUT, 5), OP_PUT, 1, 9), "stale")
        self.assertEqual(preflight((5, OP_PUT, 9), OP_PUT, 5, 9), "idempotent")
        self.assertEqual(preflight((5, OP_PUT, 9), OP_PUT, 5, 8), "conflict")
        self.assertEqual(preflight((5, OP_PUT, 9), OP_PUT, 6, 1), "applied")


class BaseViewTests(unittest.TestCase):
    def test_lookup_put_delete_absent(self):
        b = _base({5: (10, OP_PUT, 100), 6: (4, OP_DELETE, None)})
        self.assertEqual(b.lookup(5), VersionedFact(10, OP_PUT, 100))
        self.assertEqual(b.lookup(6), VersionedFact(4, OP_DELETE, None))
        self.assertIs(b.lookup(999), BASE_ABSENT)


class StorePreflightTests(unittest.TestCase):
    def test_downgrade_and_upgrade(self):
        tmp = Path(tempfile.mkdtemp())
        s = GroupCommitStore(str(tmp / "s"), KEY, SCOPE, base=_base({5: (10, OP_PUT, 100)}))
        self.assertEqual(s.submit(OP_PUT, 5, 1, 7, make_operation_id(0, 1)).result(timeout=5)[0], STALE_REJECTED)
        self.assertEqual(s.submit(OP_PUT, 5, 11, 8, make_operation_id(0, 2)).result(timeout=5)[0], APPLIED)
        s.close()

    def test_delete_base_no_resurrection(self):
        tmp = Path(tempfile.mkdtemp())
        s = GroupCommitStore(str(tmp / "d"), KEY, SCOPE, base=_base({5: (10, OP_DELETE, None)}))
        self.assertEqual(s.submit(OP_PUT, 5, 7, 9, make_operation_id(0, 1)).result(timeout=5)[0], STALE_REJECTED)
        s.close()

    def test_base_abstain_refuses(self):
        tmp = Path(tempfile.mkdtemp())
        s = GroupCommitStore(str(tmp / "a"), KEY, SCOPE, base=_base({5: (10, OP_PUT, 1)}, corrupt_registry=True))
        self.assertEqual(s.submit(OP_PUT, 5, 11, 1, make_operation_id(0, 1)).result(timeout=5)[0], ABSTAIN_BASE)
        s.close()


class RecoveryBaseTests(unittest.TestCase):
    def test_empty_segment_initial_seq(self):
        rr = reduce_frames([], initial_seq=100)
        self.assertEqual((rr.classification, rr.applied_seq), ("OK", 100))


if __name__ == "__main__":
    unittest.main()
