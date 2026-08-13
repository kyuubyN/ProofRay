# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""FH-04 — contract tests da fachada pública `horizon_memory`.

Exercita TODOS os estados públicos: escrita (applied/dedup/conflict/stale/idempotent/scope), leitura
(present/deleted/not_found/abstain/scope), snapshot isolation, query (evidence/abstention/scope),
compaction (deleted terminal + live from bulk), recovery, export, audit + ledger (marginal/total,
fecha contabilmente). Nenhuma importação do VTE.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from horizon_memory import (
    HorizonMemory, HorizonConfig, WriteState, ReadState, CompactState, QueryState, RecoverState)

KEY = b"contract-horizon-standalone-key1"
SCOPE = 7


def _mk(tmp, scope=SCOPE, name="hm"):
    cfg = HorizonConfig(root=str(Path(tmp) / name), scope_id=scope, key=KEY)
    return HorizonMemory.create(cfg)


class WriteContractTests(unittest.TestCase):
    def test_put_applied_and_get_present(self):
        hm = _mk(tempfile.mkdtemp())
        self.assertEqual(hm.get(SCOPE, 100).state, ReadState.NOT_FOUND)
        self.assertEqual(hm.put(SCOPE, 100, 1, 90).state, WriteState.APPLIED)
        r = hm.get(SCOPE, 100)
        self.assertEqual(r.state, ReadState.PRESENT)
        self.assertEqual(r.value, 90)
        self.assertEqual(r.version, 1)
        hm.close()

    def test_dedup_replay_same_operation_id(self):
        hm = _mk(tempfile.mkdtemp())
        opid = b"\x02" * 16
        self.assertEqual(hm.put(SCOPE, 200, 1, 5, operation_id=opid).state, WriteState.APPLIED)
        r2 = hm.put(SCOPE, 200, 1, 5, operation_id=opid)
        self.assertEqual(r2.state, WriteState.DEDUP_REPLAY)
        hm.close()

    def test_version_conflict(self):
        hm = _mk(tempfile.mkdtemp())
        self.assertEqual(hm.put(SCOPE, 100, 1, 90).state, WriteState.APPLIED)
        # mesma versão, conteúdo diferente -> conflito de versão
        self.assertEqual(hm.put(SCOPE, 100, 1, 7).state, WriteState.VERSION_CONFLICT)
        hm.close()

    def test_stale_rejected(self):
        hm = _mk(tempfile.mkdtemp())
        self.assertEqual(hm.put(SCOPE, 100, 2, 90).state, WriteState.APPLIED)
        # versão <= vigente com conteúdo distinto -> stale/conflito (nunca silenciosamente aplicado)
        self.assertIn(hm.put(SCOPE, 100, 1, 9).state,
                      (WriteState.STALE_REJECTED, WriteState.VERSION_CONFLICT))
        hm.close()

    def test_value_domain_guard_raises(self):
        hm = _mk(tempfile.mkdtemp())
        with self.assertRaises(ValueError):
            hm.put(SCOPE, 1, 1, 900)
        with self.assertRaises(ValueError):
            hm.put(SCOPE, 1, 1, -1)
        hm.close()

    def test_scope_isolation_write(self):
        hm = _mk(tempfile.mkdtemp())
        self.assertEqual(hm.put(999, 1, 1, 1).state, WriteState.REJECTED_SCOPE)
        hm.close()


class ReadContractTests(unittest.TestCase):
    def test_delete_terminal_and_not_found(self):
        hm = _mk(tempfile.mkdtemp())
        hm.put(SCOPE, 100, 1, 90)
        self.assertEqual(hm.delete(SCOPE, 100, 2).state, WriteState.APPLIED)
        self.assertEqual(hm.get(SCOPE, 100).state, ReadState.DELETED)     # terminal, não NOT_FOUND
        self.assertEqual(hm.get(SCOPE, 555).state, ReadState.NOT_FOUND)
        hm.close()

    def test_scope_isolation_read(self):
        hm = _mk(tempfile.mkdtemp())
        hm.put(SCOPE, 100, 1, 90)
        self.assertEqual(hm.get(999, 100).state, ReadState.ABSTAIN_SCOPE)
        hm.close()

    def test_snapshot_isolation(self):
        hm = _mk(tempfile.mkdtemp())
        hm.put(SCOPE, 100, 1, 90)
        rv = hm.capture_read_view(SCOPE)
        self.assertIsNotNone(rv)
        hm.delete(SCOPE, 100, 2)
        # a leitura corrente vê deletado; o snapshot capturado antes vê o valor
        self.assertEqual(hm.get(SCOPE, 100).state, ReadState.DELETED)
        rr = hm.get(SCOPE, 100, read_view=rv)
        self.assertEqual(rr.state, ReadState.PRESENT)
        self.assertEqual(rr.value, 90)
        hm.close()

    def test_read_view_wrong_scope(self):
        hm = _mk(tempfile.mkdtemp())
        hm.put(SCOPE, 100, 1, 90)
        rv = hm.capture_read_view(SCOPE)
        self.assertIsNone(hm.capture_read_view(999))
        hm.close()


class QueryContractTests(unittest.TestCase):
    def test_query_evidence(self):
        hm = _mk(tempfile.mkdtemp())
        hm.put(SCOPE, 101, 1, 91)
        q = hm.query(SCOPE, 101)
        self.assertEqual(q.state, QueryState.EVIDENCE)
        self.assertEqual(q.value, 91)
        self.assertIsNotNone(q.provenance)
        self.assertEqual(q.provenance.verifier_state, "verified")
        self.assertEqual(q.provenance.version, 1)
        hm.close()

    def test_query_abstention_deleted_and_absent(self):
        hm = _mk(tempfile.mkdtemp())
        hm.put(SCOPE, 100, 1, 90)
        hm.delete(SCOPE, 100, 2)
        qd = hm.query(SCOPE, 100)
        self.assertEqual(qd.state, QueryState.ABSTENTION)
        self.assertNotEqual(qd.abstention_reason, "")
        self.assertEqual(hm.query(SCOPE, 555).state, QueryState.ABSTENTION)
        hm.close()

    def test_query_semantic_is_abstention_in_fh04(self):
        # a rota semântica real é FH-06; FH-04 só resolve chave explícita.
        hm = _mk(tempfile.mkdtemp())
        self.assertEqual(hm.query(SCOPE, "texto").state, QueryState.ABSTENTION)
        hm.close()

    def test_query_scope_isolation(self):
        hm = _mk(tempfile.mkdtemp())
        self.assertEqual(hm.query(999, 1).state, QueryState.ABSTAIN_SCOPE)
        hm.close()


class CompactionContractTests(unittest.TestCase):
    def test_compact_preserves_delete_terminal_and_live(self):
        hm = _mk(tempfile.mkdtemp())
        hm.put(SCOPE, 100, 1, 90)
        hm.put(SCOPE, 101, 1, 91)
        hm.delete(SCOPE, 100, 2)
        res = hm.compact(SCOPE)
        self.assertEqual(res.state, CompactState.COMPACTED)
        # fato vivo permanece; deleção continua terminal na nova geração
        self.assertEqual(hm.get(SCOPE, 101).state, ReadState.PRESENT)
        self.assertEqual(hm.get(SCOPE, 100).state, ReadState.DELETED)
        hm.close()

    def test_compact_empty_scope_nothing_to_do(self):
        hm = _mk(tempfile.mkdtemp())
        self.assertEqual(hm.compact(SCOPE).state, CompactState.NOTHING_TO_DO)
        hm.close()


class RecoveryContractTests(unittest.TestCase):
    def test_recover_recovered_after_writes(self):
        hm = _mk(tempfile.mkdtemp())
        hm.put(SCOPE, 100, 1, 90)
        self.assertEqual(hm.recover(SCOPE).state, RecoverState.RECOVERED)
        hm.close()

    def test_recover_is_read_only(self):
        # recovery não altera o estado observável
        hm = _mk(tempfile.mkdtemp())
        hm.put(SCOPE, 100, 1, 90)
        before = hm.get(SCOPE, 100).value
        hm.recover(SCOPE)
        self.assertEqual(hm.get(SCOPE, 100).value, before)
        hm.close()


class ExportAuditLedgerTests(unittest.TestCase):
    def test_config_repr_and_redacted_view_never_expose_key(self):
        secret = b"contract-horizon-standalone-key1"
        cfg = HorizonConfig(root="/tmp/horizon-redaction", scope_id=SCOPE, key=secret)
        self.assertNotIn(secret.decode(), repr(cfg))
        self.assertNotIn(secret.decode(), str(cfg.redacted()))
        self.assertEqual(cfg.redacted()["key"], "<redacted>")

    def test_export_contents_and_checksums(self):
        hm = _mk(tempfile.mkdtemp())
        hm.put(SCOPE, 100, 1, 90)
        hm.put(SCOPE, 101, 1, 91)
        hm.delete(SCOPE, 100, 2)
        exp = hm.export(SCOPE)
        by_id = {f.fact_id: f for f in exp.facts}
        self.assertEqual(by_id[100].deletion_state, "deleted")
        self.assertEqual(by_id[101].deletion_state, "live")
        self.assertEqual(by_id[101].value, 91)
        self.assertEqual(by_id[101].version, 1)
        self.assertTrue(all(len(f.checksum) == 64 for f in exp.facts))
        self.assertEqual(len(exp.manifest_sha256), 64)
        hm.close()

    def test_audit_invariants_and_ledger_accounts(self):
        hm = _mk(tempfile.mkdtemp())
        hm.put(SCOPE, 100, 1, 90)
        hm.put(SCOPE, 101, 1, 91)
        hm.delete(SCOPE, 100, 2)
        aud = hm.audit(SCOPE)
        self.assertTrue(aud.invariants_ok, aud.findings)
        self.assertTrue(aud.ledger["accounts"])
        # ledger total inclui registry+dedup; marginal os remove -> marginal <= total
        self.assertLessEqual(aud.ledger["marginal_bytes"], aud.ledger["total_bytes"])
        self.assertGreater(aud.ledger["total_bytes"], 0)
        self.assertEqual(aud.ledger["live_facts"], 1)     # só 101 (100 foi deletado)
        self.assertEqual(aud.ledger["deleted_facts"], 1)  # 100
        hm.close()

    def test_context_manager_closes(self):
        with _mk(tempfile.mkdtemp()) as hm:
            hm.put(SCOPE, 1, 1, 1)
        # após sair do context manager, operações levantam
        with self.assertRaises(RuntimeError):
            hm.get(SCOPE, 1)


if __name__ == "__main__":
    unittest.main()
