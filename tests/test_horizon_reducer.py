# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""V23-B3-3 — reducer incremental (`ReducerState`): uma máquina de replay única, streaming, que
NUNCA expõe estado parcial. Equivalência com a concatenação antiga, invariantes de fronteira,
limites por dimensão, estado terminal idempotente, selo obrigatório e paridade sharded↔simples."""

from __future__ import annotations

import random
import sys
import unittest
from types import SimpleNamespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from horizon_memory._engine.horizon_manifest import WalSegmentDescriptor
from horizon_memory._engine.horizon_sharded import ShardedTxIdIndex, ShardedWalIndex
from horizon_memory._engine.horizon_store import OP_DELETE, OP_PUT, BundleBaseView  # noqa: F401
from horizon_memory._engine.horizon_store import EmptyBaseView, VersionedFact
from horizon_memory._engine.horizon_wal import (
    REDUCE_COVERAGE,
    REDUCE_LIMIT,
    REDUCE_OK,
    REDUCE_SEAL,
    ReducerLimits,
    ReducerState,
    TxIdIndex,
    WAL_SEGMENT_SEAL,
    WalIndex,
    make_operation_id,
    reduce_frames,
)
from horizon_memory._engine.horizon_wal import STATE_ACTIVE, STATE_SEALED

BIG = ReducerLimits(1 << 30, 1 << 30, 1 << 30, 1 << 30, 1 << 30)
Z32, Z16 = b"\x00" * 32, b"\x00" * 16


def _tok(frames, *, sealed=True, byte_length=None):
    return SimpleNamespace(frames=tuple(frames), header=None, is_sealed=True,
                           byte_length=byte_length if byte_length is not None else len(frames) * 80 + 64,
                           seal=WAL_SEGMENT_SEAL if sealed else None)


def _desc(ordinal, segment_id, first_seq, rc, status=STATE_SEALED):
    return WalSegmentDescriptor(ordinal, segment_id, first_seq, rc, status, 64, Z32, 64, Z32, Z16, 0)


def _fr(seq, fid, fver, val, op=OP_PUT, client=1):
    return (op, seq, make_operation_id(client, seq), fid, fver, val)


def _valid_sequence(n, k, rng):
    """Sequência canônica: seq contíguo 1..n, versão por-fato crescente, op_ids únicos."""
    ver = {}
    frames = []
    for seq in range(1, n + 1):
        fid = rng.randrange(k)
        ver[fid] = ver.get(fid, 0) + 1
        op = OP_PUT if rng.random() < 0.8 else OP_DELETE
        val = None if op == OP_DELETE else rng.randrange(1000)
        frames.append(_fr(seq, fid, ver[fid], val, op=op))
    return frames


def _partition(frames, rng):
    """Divide em segmentos contíguos; devolve [(descriptor, token)]."""
    segs, i, ordinal, sid = [], 0, 0, 1
    n = len(frames)
    while i < n:
        step = rng.randint(1, max(1, n // 3))
        chunk = frames[i:i + step]
        first_seq = chunk[0][1]
        segs.append((_desc(ordinal, sid, first_seq, len(chunk)), _tok(chunk)))
        i += step; ordinal += 1; sid += 1
    return segs


class ReducerTests(unittest.TestCase):
    # G1 -----------------------------------------------------------------
    def test_incremental_equals_concatenation(self):
        rng = random.Random(1)
        for _ in range(40):
            frames = _valid_sequence(rng.randint(1, 60), 8, rng)
            ref = reduce_frames(list(frames))                       # concatenação antiga (wrapper)
            reducer = ReducerState.begin(base=EmptyBaseView(), initial_seq=0,
                                         target_seq=len(frames), limits=BIG)
            for d, t in _partition(frames, rng):
                self.assertTrue(reducer.feed_segment(t, d).ok)
            got = reducer.finish()
            self.assertEqual(got.classification, REDUCE_OK)
            self.assertEqual((got.applied_count, got.applied_seq),
                             (ref.applied_count, ref.applied_seq))
            for fid in range(8):
                self.assertEqual(got.index.get(fid), ref.index.get(fid))

    # G2 -----------------------------------------------------------------
    def test_sealed_sealed_active_matches(self):
        frames = [_fr(1, 100, 1, 900), _fr(2, 101, 1, 901), _fr(3, 100, 2, 902),
                  _fr(4, 102, 1, 903), _fr(5, 100, 3, 904)]
        reducer = ReducerState.begin(base=EmptyBaseView(), initial_seq=0, target_seq=5, limits=BIG)
        reducer.feed_segment(_tok(frames[0:2]), _desc(0, 1, 1, 2, STATE_SEALED))
        reducer.feed_segment(_tok(frames[2:4]), _desc(1, 2, 3, 2, STATE_SEALED))
        reducer.feed_segment(_tok(frames[4:5]), _desc(2, 3, 5, 1, STATE_ACTIVE))
        r = reducer.finish()
        self.assertEqual(r.classification, REDUCE_OK)
        self.assertEqual(r.index.get(100), (3, OP_PUT, 904))       # última versão vence
        self.assertEqual(r.index.get(101), (1, OP_PUT, 901))

    # G3 -----------------------------------------------------------------
    def test_base_aware_between_segments(self):
        class _Base:
            def lookup(self, fid):
                return VersionedFact(5, OP_PUT, 50) if fid == 1 else EmptyBaseView().lookup(fid)
        # stale vs base (v3 < base v5) → conflito
        red = ReducerState.begin(base=_Base(), initial_seq=0, target_seq=1, limits=BIG)
        self.assertFalse(red.feed_segment(_tok([_fr(1, 1, 3, 7)]), _desc(0, 1, 1, 1)).ok)
        self.assertNotEqual(red.finish().classification, REDUCE_OK)
        # v7 > base v5 → aplica
        red2 = ReducerState.begin(base=_Base(), initial_seq=0, target_seq=1, limits=BIG)
        self.assertTrue(red2.feed_segment(_tok([_fr(1, 1, 7, 9)]), _desc(0, 1, 1, 1)).ok)
        self.assertEqual(red2.finish().index.get(1), (7, OP_PUT, 9))

    # G4 -----------------------------------------------------------------
    def test_duplicate_operation_id_between_segments(self):
        dup = make_operation_id(9, 9)
        f1 = (OP_PUT, 1, dup, 100, 1, 900)
        f2 = (OP_PUT, 2, dup, 101, 1, 901)                          # mesmo op_id, outro conteúdo
        red = ReducerState.begin(base=EmptyBaseView(), initial_seq=0, target_seq=2, limits=BIG)
        red.feed_segment(_tok([f1]), _desc(0, 1, 1, 1))
        self.assertFalse(red.feed_segment(_tok([f2]), _desc(1, 2, 2, 1)).ok)
        self.assertNotEqual(red.finish().classification, REDUCE_OK)

    # G5 -----------------------------------------------------------------
    def test_stale_in_last_frame_invalidates_all(self):
        frames = [_fr(i, 100, i, i * 10) for i in range(1, 20)] + [_fr(20, 100, 5, 1)]  # v5 stale no fim
        red = ReducerState.begin(base=EmptyBaseView(), initial_seq=0, target_seq=20, limits=BIG)
        red.feed_segment(_tok(frames), _desc(0, 1, 1, 20))
        r = red.finish()
        self.assertNotEqual(r.classification, REDUCE_OK)
        self.assertEqual(r.applied_count, 0)                        # nenhum índice parcial

    # G6/G7 --------------------------------------------------------------
    def test_gap_overlap_regression_and_out_of_order(self):
        base = lambda: ReducerState.begin(base=EmptyBaseView(), initial_seq=0, target_seq=4, limits=BIG)
        r = base(); r.feed_segment(_tok([_fr(1, 1, 1, 1)]), _desc(0, 1, 1, 1))
        self.assertEqual(r.feed_segment(_tok([_fr(3, 2, 1, 2)]), _desc(1, 2, 3, 1)).classification,
                         REDUCE_COVERAGE)                            # gap (first_seq 3 != 2)
        r = base(); r.feed_segment(_tok([_fr(1, 1, 1, 1)]), _desc(0, 1, 1, 1))
        self.assertEqual(r.feed_segment(_tok([_fr(2, 2, 1, 2)]), _desc(0, 2, 2, 1)).classification,
                         REDUCE_COVERAGE)                            # ordinal fora de ordem
        r = base(); r.feed_segment(_tok([_fr(1, 1, 1, 1)]), _desc(0, 5, 1, 1))
        self.assertEqual(r.feed_segment(_tok([_fr(2, 2, 1, 2)]), _desc(1, 3, 2, 1)).classification,
                         REDUCE_COVERAGE)                            # segment_id não-monotônico

    # G8 -----------------------------------------------------------------
    def test_empty_active_final_when_R_equals_prev(self):
        red = ReducerState.begin(base=EmptyBaseView(), initial_seq=10, target_seq=10, limits=BIG)
        self.assertTrue(red.feed_segment(_tok([]), _desc(0, 1, 11, 0, STATE_ACTIVE)).ok)
        r = red.finish()
        self.assertEqual((r.classification, r.applied_seq), (REDUCE_OK, 10))

    # G9 -----------------------------------------------------------------
    def test_limit_exact_passes_plus_one_fails(self):
        # max_segments
        lim = ReducerLimits(1, 1 << 30, 1 << 30, 1 << 30, 1 << 30)
        r = ReducerState.begin(base=EmptyBaseView(), initial_seq=0, target_seq=2, limits=lim)
        self.assertTrue(r.feed_segment(_tok([_fr(1, 1, 1, 1)]), _desc(0, 1, 1, 1)).ok)
        self.assertEqual(r.feed_segment(_tok([_fr(2, 2, 1, 2)]), _desc(1, 2, 2, 1)).classification,
                         REDUCE_LIMIT)
        # max_frames_per_segment
        lim = ReducerLimits(1 << 30, 1 << 30, 1 << 30, 2, 1 << 30)
        r = ReducerState.begin(base=EmptyBaseView(), initial_seq=0, target_seq=3, limits=lim)
        self.assertEqual(r.feed_segment(_tok([_fr(1, 1, 1, 1), _fr(2, 2, 1, 2), _fr(3, 3, 1, 3)]),
                                        _desc(0, 1, 1, 3)).classification, REDUCE_LIMIT)
        # max_total_frames
        lim = ReducerLimits(1 << 30, 2, 1 << 30, 1 << 30, 1 << 30)
        r = ReducerState.begin(base=EmptyBaseView(), initial_seq=0, target_seq=3, limits=lim)
        r.feed_segment(_tok([_fr(1, 1, 1, 1), _fr(2, 2, 1, 2)]), _desc(0, 1, 1, 2))
        self.assertEqual(r.feed_segment(_tok([_fr(3, 3, 1, 3)]), _desc(1, 2, 3, 1)).classification,
                         REDUCE_LIMIT)
        # max_segment_bytes
        lim = ReducerLimits(1 << 30, 1 << 30, 1 << 30, 1 << 30, 10)
        r = ReducerState.begin(base=EmptyBaseView(), initial_seq=0, target_seq=1, limits=lim)
        self.assertEqual(r.feed_segment(_tok([_fr(1, 1, 1, 1)], byte_length=11),
                                        _desc(0, 1, 1, 1)).classification, REDUCE_LIMIT)

    # G10 ----------------------------------------------------------------
    def test_error_is_terminal_and_idempotent(self):
        red = ReducerState.begin(base=EmptyBaseView(), initial_seq=0, target_seq=2, limits=BIG)
        red.feed_segment(_tok([_fr(1, 1, 5, 1)]), _desc(0, 1, 1, 1))
        first = red.feed_segment(_tok([_fr(2, 1, 5, 2)]), _desc(1, 2, 2, 1))   # v5==v5, conteúdo != → conflito
        self.assertFalse(first.ok)
        again = red.feed_segment(_tok([_fr(3, 3, 1, 3)]), _desc(2, 3, 3, 1))   # após terminal
        self.assertEqual((again.ok, again.classification, again.reason),
                         (False, first.classification, first.reason))
        self.assertEqual(red.finish().applied_count, 0)

    # G11 ----------------------------------------------------------------
    def test_finish_early_and_duplicate_reject(self):
        red = ReducerState.begin(base=EmptyBaseView(), initial_seq=0, target_seq=5, limits=BIG)
        red.feed_segment(_tok([_fr(1, 1, 1, 1)]), _desc(0, 1, 1, 1))
        self.assertEqual(red.finish().classification, REDUCE_COVERAGE)         # antecipado (não chegou a R)
        red2 = ReducerState.begin(base=EmptyBaseView(), initial_seq=0, target_seq=1, limits=BIG)
        red2.feed_segment(_tok([_fr(1, 1, 1, 1)]), _desc(0, 1, 1, 1))
        self.assertEqual(red2.finish().classification, REDUCE_OK)
        self.assertNotEqual(red2.finish().classification, REDUCE_OK)           # finish duplicado

    # G12 ----------------------------------------------------------------
    def test_forged_token_rejected(self):
        red = ReducerState.begin(base=EmptyBaseView(), initial_seq=0, target_seq=1, limits=BIG)
        self.assertEqual(red.feed_segment(_tok([_fr(1, 1, 1, 1)], sealed=False),
                                          _desc(0, 1, 1, 1)).classification, REDUCE_SEAL)

    # G13 ----------------------------------------------------------------
    def test_sharded_equals_simple(self):
        frames = _valid_sequence(50, 6, random.Random(7))
        outs = []
        for wf, tf in ((WalIndex, TxIdIndex),
                       (lambda: ShardedWalIndex.empty(10, 0), lambda: ShardedTxIdIndex.empty(12, 0))):
            red = ReducerState.begin(base=EmptyBaseView(), initial_seq=0, target_seq=50, limits=BIG,
                                     wal_index_factory=wf, tx_index_factory=tf)
            red.feed_segment(_tok(frames), _desc(0, 1, 1, 50))
            r = red.finish()
            outs.append({fid: r.index.get(fid) for fid in range(6)})
        self.assertEqual(outs[0], outs[1])

    # G15 ----------------------------------------------------------------
    def test_fuzz_never_raises_never_partial(self):
        rng = random.Random(20260812)
        for _ in range(2000):
            n = rng.randint(1, 20)
            frames = [(OP_PUT if rng.random() < 0.8 else OP_DELETE, rng.randint(0, 30),
                       make_operation_id(rng.randint(0, 3), rng.randint(0, 40)),
                       rng.randint(0, 10), rng.randint(1, 6),
                       rng.randrange(1000)) for _ in range(n)]
            red = ReducerState.begin(base=EmptyBaseView(), initial_seq=0, target_seq=n, limits=BIG)
            try:
                red.feed_segment(_tok(frames), _desc(0, 1, frames[0][1], n))
                r = red.finish()
            except Exception as e:  # noqa: BLE001
                self.fail(f"reducer lançou: {e!r}")
            if r.classification != REDUCE_OK:
                self.assertEqual(r.applied_count, 0)                # nunca índice parcial


if __name__ == "__main__":
    unittest.main()
