# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""V23-D0 / FH-02 — checker de linearizabilidade puro. Aceita histórias lineares; DETECTA impossíveis;
InvocationId ≠ operation_id; contraexemplo reproduzível."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from horizon_memory._engine.horizon_linearizability import (
    APPLIED, BUDGET_EXHAUSTED, CAPTURE, COMPACT, DEDUP_REPLAY, DELETE, DELETED, IDEMPOTENT, LINEARIZABLE,
    NON_LINEARIZABLE, NOT_FOUND, Op, PRESENT, PUT, READ, ROTATE, STALE_REJECTED, TXID_CONFLICT,
    VERSION_CONFLICT, apply_op, check_linearizable, history_to_json, json_to_history)


class LinearAcceptedTests(unittest.TestCase):
    def test_put_then_read(self):
        h = [Op(1, "w", PUT, 0, 1, operation_id=1, fact_id=1, version=1, value=10, result=(APPLIED, 1)),
             Op(2, "r", READ, 2, 3, fact_id=1, result=(PRESENT, 10))]
        self.assertTrue(check_linearizable(h).linearizable)

    def test_concurrent_read_can_order_before_write(self):
        h = [Op(1, "w", PUT, 0, 3, operation_id=1, fact_id=1, version=1, value=10, result=(APPLIED, 1)),
             Op(2, "r", READ, 1, 2, fact_id=1, result=(NOT_FOUND, None))]
        self.assertTrue(check_linearizable(h).linearizable)

    def test_retry_shares_operation_id_distinct_invocation(self):
        # InvocationId 1 e 2 distintos, MESMO operation_id 7 → o 2º é DEDUP_REPLAY do seq original
        h = [Op(1, "w", PUT, 0, 1, operation_id=7, fact_id=1, version=1, value=10, result=(APPLIED, 1)),
             Op(2, "w", PUT, 2, 3, operation_id=7, fact_id=1, version=1, value=10, result=(DEDUP_REPLAY, 1))]
        self.assertTrue(check_linearizable(h).linearizable)

    def test_snapshot_immutable(self):
        h = [Op(1, "w", PUT, 0, 1, operation_id=1, fact_id=1, version=1, value=10, result=(APPLIED, 1)),
             Op(2, "s", CAPTURE, 2, 3, result=(0, 1)),
             Op(3, "w", PUT, 4, 5, operation_id=2, fact_id=1, version=2, value=20, result=(APPLIED, 2)),
             Op(4, "r", READ, 6, 7, fact_id=1, snapshot_of=2, result=(PRESENT, 10))]
        self.assertTrue(check_linearizable(h).linearizable)

    def test_compaction_preserves_state(self):
        h = [Op(1, "w", PUT, 0, 1, operation_id=1, fact_id=1, version=1, value=10, result=(APPLIED, 1)),
             Op(2, "c", COMPACT, 2, 3, result=(1,)),
             Op(3, "r", READ, 4, 5, fact_id=1, result=(PRESENT, 10))]
        self.assertTrue(check_linearizable(h).linearizable)

    def test_incomplete_write_omitted(self):
        h = [Op(1, "w", PUT, 0, None, operation_id=1, fact_id=1, version=1, value=10, result=None),
             Op(2, "r", READ, 2, 3, fact_id=1, result=(NOT_FOUND, None))]
        self.assertTrue(check_linearizable(h).linearizable)


class ImpossibleDetectedTests(unittest.TestCase):
    def test_read_stale_after_two_writes(self):
        h = [Op(1, "w", PUT, 0, 1, operation_id=1, fact_id=1, version=1, value=10, result=(APPLIED, 1)),
             Op(2, "w", PUT, 2, 3, operation_id=2, fact_id=1, version=2, value=20, result=(APPLIED, 2)),
             Op(3, "r", READ, 4, 5, fact_id=1, result=(PRESENT, 10))]
        r = check_linearizable(h)
        self.assertFalse(r.linearizable)
        self.assertIn(3, r.counterexample)

    def test_phantom_value(self):
        h = [Op(1, "w", PUT, 0, 1, operation_id=1, fact_id=1, version=1, value=10, result=(APPLIED, 1)),
             Op(2, "r", READ, 2, 3, fact_id=1, result=(PRESENT, 99))]
        self.assertFalse(check_linearizable(h).linearizable)

    def test_resurrected_delete(self):
        h = [Op(1, "w", PUT, 0, 1, operation_id=1, fact_id=1, version=1, value=10, result=(APPLIED, 1)),
             Op(2, "w", DELETE, 2, 3, operation_id=2, fact_id=1, version=2, value=None, result=(APPLIED, 2)),
             Op(3, "r", READ, 4, 5, fact_id=1, result=(PRESENT, 10))]
        self.assertFalse(check_linearizable(h).linearizable)

    def test_duplicate_seq_two_writers(self):
        h = [Op(1, "a", PUT, 0, 2, operation_id=1, fact_id=1, version=1, value=10, result=(APPLIED, 1)),
             Op(2, "b", PUT, 0, 2, operation_id=2, fact_id=2, version=1, value=20, result=(APPLIED, 1))]
        self.assertFalse(check_linearizable(h).linearizable)

    def test_snapshot_mutated_by_later_write(self):
        h = [Op(1, "w", PUT, 0, 1, operation_id=1, fact_id=1, version=1, value=10, result=(APPLIED, 1)),
             Op(2, "s", CAPTURE, 2, 3, result=(0, 1)),
             Op(3, "w", PUT, 4, 5, operation_id=2, fact_id=1, version=2, value=20, result=(APPLIED, 2)),
             Op(4, "r", READ, 6, 7, fact_id=1, snapshot_of=2, result=(PRESENT, 20))]
        self.assertFalse(check_linearizable(h).linearizable)

    def test_dedup_without_original_rejected(self):
        h = [Op(1, "w", PUT, 0, 1, operation_id=9, fact_id=1, version=1, value=10, result=(DEDUP_REPLAY, 1))]
        self.assertFalse(check_linearizable(h).linearizable)


class ModelHardeningTests(unittest.TestCase):
    """FH-02.1 — dedup por command_digest, apply_op 3 desfechos, checker 3 estados."""

    def test_same_operation_id_different_command_is_conflict(self):
        # mesmo operation_id, comando DIFERENTE (valor) → TXID_CONFLICT (não DEDUP_REPLAY)
        h = [Op(1, "w", PUT, 0, 1, operation_id=5, fact_id=1, version=1, value=10, result=(APPLIED, 1)),
             Op(2, "w", PUT, 2, 3, operation_id=5, fact_id=1, version=1, value=77, result=(TXID_CONFLICT, None))]
        self.assertEqual(check_linearizable(h).verdict, LINEARIZABLE)
        # se o runtime tivesse (erradamente) devolvido DEDUP_REPLAY, seria NÃO-linearizável
        bad = [h[0], Op(2, "w", PUT, 2, 3, operation_id=5, fact_id=1, version=1, value=77, result=(DEDUP_REPLAY, 1))]
        self.assertEqual(check_linearizable(bad).verdict, NON_LINEARIZABLE)

    def test_read_missing_snapshot_is_invalid_not_current_read(self):
        # READ com snapshot_of inexistente NÃO cai para leitura atual → op INVÁLIDA → não-linearizável
        state_ok, expected = apply_op(_fresh_state(), Op(9, "r", READ, 0, 1, fact_id=1, snapshot_of=999))
        self.assertEqual(state_ok, "INVALID")
        h = [Op(1, "w", PUT, 0, 1, operation_id=1, fact_id=1, version=1, value=10, result=(APPLIED, 1)),
             Op(2, "r", READ, 2, 3, fact_id=1, snapshot_of=999, result=(PRESENT, 10))]
        self.assertEqual(check_linearizable(h).verdict, NON_LINEARIZABLE)

    def test_budget_exhausted_is_not_non_linearizable(self):
        h = [Op(i, f"a{i%3}", PUT, 2 * i, 2 * i + 1, operation_id=i, fact_id=i % 2, version=1, value=i)
             for i in range(1, 8)]
        # resultados coerentes com uma ordem sequencial simples
        from horizon_memory._engine.horizon_linearizability import _State
        st = _State()
        hh = []
        for o in h:
            _oc, exp = apply_op(st, o)
            hh.append(Op(o.inv_id, o.actor, o.kind, o.invocation, o.response, o.operation_id, o.fact_id,
                         o.version, o.value, exp))
        r = check_linearizable(hh, max_states=3)                # orçamento minúsculo
        self.assertEqual(r.verdict, BUDGET_EXHAUSTED)
        self.assertFalse(r.linearizable)

    def test_non_applied_result_is_not_durably_deduplicated(self):
        # O runtime só persiste APPLIED no WAL/TxIdIndex. A chamada op_id=2 é inicialmente IDEMPOTENT;
        # depois de v2, seu retry é recalculado como STALE_REJECTED, não replay do no-op anterior.
        h = [
            Op(1, "w", PUT, 0, 1, operation_id=1, fact_id=1, version=1, value=10,
               result=(APPLIED, 1)),
            Op(2, "w", PUT, 2, 3, operation_id=2, fact_id=1, version=1, value=10,
               result=(IDEMPOTENT, None)),
            Op(3, "w", PUT, 4, 5, operation_id=3, fact_id=1, version=2, value=20,
               result=(APPLIED, 2)),
            Op(4, "w", PUT, 6, 7, operation_id=2, fact_id=1, version=1, value=10,
               result=(STALE_REJECTED, None)),
        ]
        self.assertEqual(check_linearizable(h).verdict, LINEARIZABLE)


def _fresh_state():
    from horizon_memory._engine.horizon_linearizability import _State
    return _State()


class ReproducibilityTests(unittest.TestCase):
    def test_json_roundtrip_preserves_verdict(self):
        h = [Op(1, "w", PUT, 0, 1, operation_id=1, fact_id=1, version=1, value=10, result=(APPLIED, 1)),
             Op(2, "r", READ, 2, 3, fact_id=1, result=(PRESENT, 99))]
        blob = history_to_json(h, seed="t")
        ops2, seed, _ = json_to_history(blob)
        self.assertEqual(seed, "t")
        self.assertFalse(check_linearizable(ops2).linearizable)


if __name__ == "__main__":
    unittest.main()
