# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Testes do copy-on-write sharded (V23-A3): equivalência, compartilhamento estrutural, store COW."""

from __future__ import annotations

import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from horizon_memory._engine.horizon_batch import GroupCommitStore
from horizon_memory._engine.horizon_sharded import (
    ShardedTxIdIndex,
    ShardedWalIndex,
    shard_of_fact,
)
from horizon_memory._engine.horizon_store import OP_DELETE, OP_PUT, SequentialModel, WalIndex
from horizon_memory._engine.horizon_wal import APPLIED, make_operation_id

KEY = b"unit-test-a3-session-key-0123456"
SCOPE, SEG = 7, 1


class ShardedEquivalenceTests(unittest.TestCase):
    def test_matches_plain_index(self):
        import numpy as np
        rng = np.random.default_rng(3)
        plain = WalIndex()
        sh = ShardedWalIndex.empty(8)
        for _ in range(40):
            b = sh.begin_mutation()
            for _ in range(200):
                fid = int(rng.integers(0, 1500)); v = int(rng.integers(1, 30))
                op = OP_DELETE if rng.random() < 0.3 else OP_PUT
                val = None if op == OP_DELETE else int(rng.integers(1, 250))
                self.assertEqual(plain.apply(fid, v, op, val), b.apply(fid, v, op, val))
            sh = b.freeze()
        self.assertTrue(all(plain.get(f) == sh.get(f) for f in range(1500)))

    def test_structural_sharing_and_abort(self):
        base = ShardedWalIndex.empty(8)
        b0 = base.begin_mutation()
        for f in range(40):
            b0.apply(f, 1, OP_PUT, f)
        pop = b0.freeze()
        b = pop.begin_mutation(); b.apply(0, 2, OP_PUT, 9); frozen = b.freeze()
        touched = shard_of_fact(0, pop.shard_seed, (1 << pop.shard_bits) - 1)
        self.assertTrue(all(frozen.shards[i] is pop.shards[i]
                            for i in range(len(frozen.shards)) if i != touched))
        self.assertIsNot(frozen.shards[touched], pop.shards[touched])
        b2 = pop.begin_mutation(); b2.apply(0, 3, OP_PUT, -1); del b2   # abort
        self.assertEqual(pop.get(0), (1, OP_PUT, 0))

    def test_tx_dedup_semantics(self):
        tx = ShardedTxIdIndex.empty(8)
        opid = make_operation_id(1, 1)
        b = tx.begin_mutation()
        self.assertEqual(b.check(opid, b"d"), "new")
        b.record(opid, b"d", 5)
        tx = b.freeze()
        self.assertEqual(tx.check(opid, b"d"), "dedup_replay")
        self.assertEqual(tx.check(opid, b"other"), "txid_conflict")
        self.assertEqual(tx.get_seq(opid), 5)


class ShardedStoreTests(unittest.TestCase):
    def test_store_with_shards_matches_sequential(self):
        tmp = Path(tempfile.mkdtemp())
        s = GroupCommitStore(str(tmp / "cow"), KEY, SCOPE, SEG, shards=1024)
        reqs = [s.submit(OP_PUT, i % 40, i, i * 2, make_operation_id(0, i)) for i in range(1, 120)]
        for r in reqs:
            r.result(timeout=5)
        snap = s.capture_read_view()
        s.close()
        model = SequentialModel()
        for r in sorted(reqs, key=lambda r: r.cmd.enqueue_ticket):
            if r.status == APPLIED:
                model.put(r.cmd.fact_id, r.cmd.value, r.cmd.fact_version)
        for fid in range(40):
            kind, val = model.read(fid)
            if kind == "value":
                self.assertEqual(snap.index.get(fid)[2], val)

    def test_store_shards_dedup_same_seq(self):
        tmp = Path(tempfile.mkdtemp())
        s = GroupCommitStore(str(tmp / "d"), KEY, SCOPE, SEG, shards=256)
        X = make_operation_id(3, 1)
        st1, seq1 = s.submit(OP_PUT, 1, 1, 10, X).result(timeout=5)
        st2, seq2 = s.submit(OP_PUT, 1, 1, 10, X).result(timeout=5)
        s.close()
        self.assertEqual((st1, st2), (APPLIED, "DEDUP_REPLAY"))
        self.assertEqual(seq1, seq2)

    def test_resume_sharded_dedup_and_continue(self):
        """V23-A3.1: escrever sharded → fechar → resume sharded → dedup do último → continua."""
        tmp = Path(tempfile.mkdtemp())
        p = str(tmp / "resume")
        s = GroupCommitStore(p, KEY, SCOPE, SEG, wal_shards=1024, tx_shards=1024)
        for i in range(1, 40):
            s.submit(OP_PUT, i % 8, i, i, make_operation_id(0, i)).result(timeout=5)
        last = make_operation_id(0, 39)
        s.close()
        r = GroupCommitStore.resume_existing(p, KEY, SCOPE, SEG, wal_shards=1024, tx_shards=1024)
        snap = r.capture_read_view()
        self.assertEqual(snap.wal_head.durable_through_seq, 39)
        st, seq = r.submit(OP_PUT, 39 % 8, 39, 39, last).result(timeout=5)   # retry último
        self.assertEqual((st, seq), ("DEDUP_REPLAY", 39))
        st2, seq2 = r.submit(OP_PUT, 99, 1, 1, make_operation_id(0, 40)).result(timeout=5)
        self.assertEqual((st2, seq2), (APPLIED, 40))                          # continua sem gap
        r.close()

    def test_non_power_of_two_shards_rejected(self):
        tmp = Path(tempfile.mkdtemp())
        with self.assertRaises(ValueError):
            GroupCommitStore(str(tmp / "bad"), KEY, SCOPE, SEG, wal_shards=1000)


if __name__ == "__main__":
    unittest.main()
