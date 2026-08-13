# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Testes do group commit (V23-A2): prepare_batch puro, visibilidade atômica, dedup, backpressure."""

from __future__ import annotations

import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from horizon_memory._engine.horizon_batch import (
    COMMIT_UNKNOWN,
    NOT_ACCEPTED,
    OVERLOADED,
    CommitCommand,
    GroupCommitStore,
    Limits,
    ShutdownTimeout,
    prepare_batch,
)
from horizon_memory._engine.horizon_store import OP_DELETE, OP_PUT, SequentialModel
from horizon_memory._engine.horizon_wal import (
    APPLIED,
    CLEAN,
    DEDUP_REPLAY,
    IDEMPOTENT,
    STALE_REJECTED,
    TXID_CONFLICT,
    VERSION_CONFLICT,
    make_operation_id,
    recover,
)

KEY = b"unit-test-a2-session-key-0123456"
SCOPE, SEG = 7, 1


def _store(tmp, name, limits=None):
    return GroupCommitStore(str(tmp / name), KEY, SCOPE, SEG, limits=limits)


class PrepareBatchTests(unittest.TestCase):
    def test_preflight_sequential_shadow(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        s = _store(tmp, "p")
        snap = s.capture_read_view()
        cmds = [
            CommitCommand(make_operation_id(1, 1), OP_PUT, 7, 2, 100, 1),
            CommitCommand(make_operation_id(1, 2), OP_DELETE, 7, 3, None, 2),
            CommitCommand(make_operation_id(1, 3), OP_PUT, 7, 1, 5, 3),   # stale vs v3
        ]
        plan = prepare_batch(snap, s._hasher, cmds, KEY, SCOPE, SEG)
        s.close()
        self.assertEqual([r.status for r in plan.receipts], [APPLIED, APPLIED, STALE_REJECTED])
        self.assertEqual(len(plan.frames), 2)

    def test_intra_batch_dedup_and_txid(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        s = _store(tmp, "d")
        snap = s.capture_read_view()
        A = make_operation_id(2, 1)
        cmds = [
            CommitCommand(A, OP_PUT, 1, 1, 9, 1),
            CommitCommand(A, OP_PUT, 1, 1, 9, 2),     # dedup
            CommitCommand(A, OP_PUT, 1, 2, 8, 3),     # txid conflict
        ]
        plan = prepare_batch(snap, s._hasher, cmds, KEY, SCOPE, SEG)
        s.close()
        self.assertEqual([r.status for r in plan.receipts], [APPLIED, DEDUP_REPLAY, TXID_CONFLICT])
        self.assertEqual(len(plan.frames), 1)


class StoreTests(unittest.TestCase):
    def test_differential_matches_sequential_ticket_order(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        s = _store(tmp, "diff")
        reqs = [s.submit(OP_PUT, i % 5, i, i * 2, make_operation_id(0, i)) for i in range(1, 60)]
        for r in reqs:
            r.result(timeout=5)
        snap = s.capture_read_view()
        s.close()
        model = SequentialModel()
        for r in sorted(reqs, key=lambda r: r.cmd.enqueue_ticket):
            model.put(r.cmd.fact_id, r.cmd.value, r.cmd.fact_version)
        for fid in range(5):
            kind, val = model.read(fid)
            idx = snap.index.get(fid)
            if kind == "value":
                self.assertEqual(idx[2], val)

    def test_old_snapshot_immutable(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        s = _store(tmp, "imm")
        s.submit(OP_PUT, 1, 1, 111, make_operation_id(0, 1)).result(timeout=5)
        old = s.capture_read_view()
        for i in range(2, 20):
            s.submit(OP_PUT, 1, i, i, make_operation_id(0, i)).result(timeout=5)
        s.close()
        self.assertEqual(old.index.get(1), (1, OP_PUT, 111))

    def test_dedup_across_batches_same_seq(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        s = _store(tmp, "dd")
        X = make_operation_id(3, 1)
        st1, seq1 = s.submit(OP_PUT, 1, 1, 10, X).result(timeout=5)
        st2, seq2 = s.submit(OP_PUT, 1, 1, 10, X).result(timeout=5)
        s.close()
        self.assertEqual((st1, st2), (APPLIED, DEDUP_REPLAY))
        self.assertEqual(seq1, seq2)

    def test_atomic_visibility(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        s = _store(tmp, "vis")
        reached, release = threading.Event(), threading.Event()
        s.failpoint = lambda stage: (reached.set(), release.wait(timeout=5)) if stage == "after_fsync" else None
        before = s.capture_read_view().visible_through_seq
        reqs = [s.submit(OP_PUT, i, 1, i, make_operation_id(0, i)) for i in range(1, 4)]
        reached.wait(timeout=5)
        mid = s.capture_read_view()
        self.assertEqual(mid.visible_through_seq, before)   # nada visível antes do swap
        release.set()
        for r in reqs:
            r.result(timeout=5)
        after = s.capture_read_view()
        self.assertTrue(all(after.index.get(i) == (1, OP_PUT, i) for i in range(1, 4)))
        s.close()

    def test_timeout_post_claim_is_commit_unknown(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        s = _store(tmp, "to")
        hold = threading.Event()
        s.failpoint = lambda stage: hold.wait(timeout=5) if stage == "after_fsync" else None
        r = s.submit(OP_PUT, 1, 1, 1, make_operation_id(0, 1))
        st, _ = r.result(timeout=0.2)
        hold.set()
        r.result(timeout=5)
        s.close()
        self.assertEqual(st, COMMIT_UNKNOWN)

    def test_failpoint_no_false_ack_and_poisoned(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        s = _store(tmp, "fp")
        s.failpoint = lambda stage: (_ for _ in ()).throw(RuntimeError("x")) if stage == "after_fsync" else None
        st, _ = s.submit(OP_PUT, 1, 1, 42, make_operation_id(0, 1)).result(timeout=5)
        self.assertNotEqual(st, APPLIED)
        self.assertTrue(s.poisoned)
        s.close()

    def test_backpressure_overloaded(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        s = _store(tmp, "bp", limits=Limits(max_queue_commands=2, max_queue_bytes=10_000,
                                            max_batch_commands=1, max_batch_bytes=10_000))
        hold = threading.Event()
        s.failpoint = lambda stage: hold.wait(timeout=5) if stage == "prepare_done" else None
        statuses = [s.submit(OP_PUT, i, 1, 1, make_operation_id(0, i)).status for i in range(1, 10)]
        hold.set()
        s.close()
        self.assertTrue(any(x == OVERLOADED for x in statuses))


class ConcurrencyAuditTests(unittest.TestCase):
    """V23-A2.1: cancel/claim sob lock, poison para writes, limite de bytes, janela, shutdown."""

    def test_poison_stops_further_writes(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        p = tmp / "poison"
        s = GroupCommitStore(str(p), b"k" * 16 if False else KEY, SCOPE, SEG,
                             limits=Limits(max_batch_commands=1))
        hold = threading.Event()

        def fp(stage):
            if stage == "prepare_done":
                hold.wait(timeout=5)
            if stage == "before_write":
                raise RuntimeError("device")

        s.failpoint = fp
        first = s.submit(OP_PUT, 1, 1, 1, make_operation_id(0, 1))
        import time as _t
        _t.sleep(0.05)
        queued = [s.submit(OP_PUT, i, 1, 1, make_operation_id(0, i)) for i in range(2, 6)]
        size_before = p.stat().st_size
        hold.set()
        self.assertEqual(first.result(timeout=5)[0], COMMIT_UNKNOWN)
        self.assertTrue(all(q.result(timeout=5)[0] == NOT_ACCEPTED for q in queued))
        _t.sleep(0.05)
        self.assertEqual(p.stat().st_size, size_before)   # nenhum write após poison
        self.assertTrue(s.poisoned)
        try:
            s.close()
        except ShutdownTimeout:
            pass

    def test_oversized_rejected_at_admission(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        s = GroupCommitStore(str(tmp / "big"), KEY, SCOPE, SEG, limits=Limits(max_batch_bytes=10))
        st, _ = s.submit(OP_PUT, 1, 1, 1, make_operation_id(0, 1)).result(timeout=5)
        s.close()
        self.assertEqual(st, OVERLOADED)

    def test_window_groups_by_count(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        metrics = []
        s = GroupCommitStore(str(tmp / "w"), KEY, SCOPE, SEG, telemetry_sink=metrics.append,
                             limits=Limits(max_batch_commands=3, window_ns=5_000_000_000))
        rs = [s.submit(OP_PUT, i, 1, i, make_operation_id(0, i)) for i in range(1, 4)]
        for r in rs:
            r.result(timeout=10)
        s.close()
        self.assertTrue(any(m.command_count == 3 for m in metrics))   # janela agrupou por quantidade

    def test_shutdown_blocked_raises_and_keeps_fd(self):
        import tempfile, time as _t
        tmp = Path(tempfile.mkdtemp())
        s = GroupCommitStore(str(tmp / "sd"), KEY, SCOPE, SEG)
        hold = threading.Event()
        s.failpoint = lambda stage: hold.wait(timeout=10) if stage == "prepare_done" else None
        r = s.submit(OP_PUT, 1, 1, 1, make_operation_id(0, 1))
        _t.sleep(0.05)
        with self.assertRaises(ShutdownTimeout):
            s.close(timeout=0.2)
        self.assertTrue(s._fd_open)               # FD preservado
        hold.set()
        r.result(timeout=5)
        s.close(timeout=5)

    def test_cancel_via_store_lock(self):
        import tempfile, time as _t
        tmp = Path(tempfile.mkdtemp())
        s = GroupCommitStore(str(tmp / "c"), KEY, SCOPE, SEG)
        hold = threading.Event()
        s.failpoint = lambda stage: hold.wait(timeout=5) if stage == "prepare_done" else None
        first = s.submit(OP_PUT, 1, 1, 1, make_operation_id(0, 1))  # reivindicado
        _t.sleep(0.05)
        later = s.submit(OP_PUT, 2, 1, 2, make_operation_id(0, 2))  # ainda ADMITTED
        self.assertTrue(later.cancel())
        self.assertEqual(later.result(timeout=5)[0], NOT_ACCEPTED)
        hold.set()
        self.assertEqual(first.result(timeout=5)[0], APPLIED)
        s.close()


if __name__ == "__main__":
    unittest.main()
