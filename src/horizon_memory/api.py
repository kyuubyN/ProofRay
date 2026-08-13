# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""FH-04 — fachada pública estável da Horizon Memory standalone.

`HorizonMemory` COMPÕE os módulos validados (não os move nem os reescreve). Uma instância está ligada a
UM scope (o `PublicationStore` é por-scope). Toda operação exige o `scope` e recusa fail-closed um scope
diferente — isolamento de scope é gate não-compensável.

Nenhuma importação do VTE. Valores são u8 [0,255] (domínio do substrato validado).
"""
from __future__ import annotations

import hashlib
import threading
from pathlib import Path

from horizon_memory._engine.horizon_store import OP_DELETE, OP_PUT
from horizon_memory._engine.horizon_wal import make_operation_id
from horizon_memory._engine.horizon_batch import GroupCommitStore
from horizon_memory._engine.horizon_publication import (
    CursorState, publish_maintenance)
from horizon_memory._engine import horizon_compaction as _CP
from horizon_memory._engine import horizon_recovery as _REC

from . import _bootstrap as _bs
from .config import HorizonConfig, VALUE_MAX, VALUE_MIN
from .ledger import compute_ledger
from .types import (
    AuditReport, CompactResult, CompactState, ExportResult, ExportedFact, Provenance,
    QueryResult, QueryState, ReadResult, ReadState, ReadViewHandle, RecoverResult, RecoverState,
    WriteResult, WriteState,
)

# mapeamento dos receipts do group commit -> WriteState
_WRITE_MAP = {
    "APPLIED": WriteState.APPLIED,
    "DEDUP_REPLAY": WriteState.DEDUP_REPLAY,
    "VERSION_CONFLICT": WriteState.VERSION_CONFLICT,
    "STALE_REJECTED": WriteState.STALE_REJECTED,
    "IDEMPOTENT": WriteState.IDEMPOTENT,
}

_READ_MAP = {
    "correct": ReadState.PRESENT,
    "from_bulk": ReadState.PRESENT,
    "fallback_bulk": ReadState.PRESENT,
    "deleted": ReadState.DELETED,
    "not_found": ReadState.NOT_FOUND,
    "abstain": ReadState.ABSTAIN,
}


class HorizonMemory:
    """Subsistema standalone de memória. Use `create`/`open` para instanciar; `close` para encerrar."""

    def __init__(self, cfg: HorizonConfig, ws, pub, base, genesis_store, writer):
        self._cfg = cfg
        self._ws = ws
        self._pub = pub
        self._base = base
        self._genesis_store = genesis_store   # writer sem cursor até a 1ª escrita
        self._writer = writer                 # GroupCommitStore publicado (ou None)
        self._lock = threading.RLock()
        self._closed = False
        # contadores lógicos p/ ledger (fatos escritos por esta instância)
        self._seen_facts: dict[int, str] = {}  # fact_id -> "live"|"deleted"
        self._opid_counter = 0

    # ---------------------------------------------------------------- ciclo de vida
    @classmethod
    def create(cls, config: HorizonConfig) -> "HorizonMemory":
        ws, pub, base, genesis_store = _bs.create_genesis(config)
        return cls(config, ws, pub, base, genesis_store, writer=None)

    @classmethod
    def open(cls, config: HorizonConfig) -> "HorizonMemory":
        ws, pub = _bs.open_existing(config)
        # base descriptors são lidos da geração viva quando necessário (compaction/rotação usam o cursor)
        return cls(config, ws, pub, base=None, genesis_store=None, writer=None)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            for st in (self._writer, self._genesis_store):
                if st is not None:
                    try:
                        st.close()
                    except Exception:
                        pass
            self._writer = None
            self._genesis_store = None
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # ---------------------------------------------------------------- utilidades internas
    def _check_open(self):
        if self._closed:
            raise RuntimeError("HorizonMemory já foi fechada")

    def _next_opid(self) -> bytes:
        self._opid_counter += 1
        return make_operation_id(self._cfg.scope_id & 0xFFFF, self._opid_counter)

    def _scope_ok(self, scope: int) -> bool:
        return scope == self._cfg.scope_id

    def _submit(self, op, fact_id, version, value, operation_id) -> WriteResult:
        """Roteia uma mutação; ativa o writer publicado na 1ª escrita (ativação preguiçosa)."""
        opid = operation_id if operation_id is not None else self._next_opid()
        if self._writer is None:
            # 1ª escrita: seed + rotação + ativação
            seed = [(op, fact_id, version, value, opid)]
            try:
                writer, receipts = _bs.activate_first_writer(
                    self._cfg, self._ws, self._pub, self._base, self._genesis_store, seed)
            except Exception as e:  # noqa: BLE001
                return WriteResult(WriteState.OVERLOAD, None, f"ativação falhou: {e!r}")
            self._writer = writer
            self._genesis_store = None
            status, seq = receipts[0]
            return WriteResult(_WRITE_MAP.get(status, WriteState.OVERLOAD), seq, status)
        try:
            status, seq = self._writer.submit(op, fact_id, version, value, opid).result(5.0)
        except Exception as e:  # noqa: BLE001
            return WriteResult(WriteState.OVERLOAD, None, f"submit falhou: {e!r}")
        return WriteResult(_WRITE_MAP.get(status, WriteState.OVERLOAD), seq if status in ("APPLIED", "DEDUP_REPLAY") else None, status)

    # ---------------------------------------------------------------- escrita
    def put(self, scope: int, fact_id: int, version: int, value: int,
            operation_id: bytes | None = None) -> WriteResult:
        self._check_open()
        if not self._scope_ok(scope):
            return WriteResult(WriteState.REJECTED_SCOPE, None, "scope != scope da instância")
        if not isinstance(value, int) or not (VALUE_MIN <= value <= VALUE_MAX):
            # uso inválido: valor fora do domínio u8 é erro de programação do chamador
            raise ValueError(f"value deve ser int em [{VALUE_MIN},{VALUE_MAX}]; recebido {value!r}")
        with self._lock:
            r = self._submit(OP_PUT, fact_id, version, value, operation_id)
        if r.state in (WriteState.APPLIED, WriteState.DEDUP_REPLAY):
            self._seen_facts[fact_id] = "live"
        return r

    def delete(self, scope: int, fact_id: int, version: int,
               operation_id: bytes | None = None) -> WriteResult:
        self._check_open()
        if not self._scope_ok(scope):
            return WriteResult(WriteState.REJECTED_SCOPE, None, "scope != scope da instância")
        with self._lock:
            r = self._submit(OP_DELETE, fact_id, version, None, operation_id)
        if r.state in (WriteState.APPLIED, WriteState.DEDUP_REPLAY):
            self._seen_facts[fact_id] = "deleted"
        return r

    # ---------------------------------------------------------------- leitura
    def _read(self, fact_id: int, manifest_blob: bytes | None) -> ReadResult:
        if manifest_blob is None:
            st, cursor = _bs.current_cursor(self._cfg, self._ws, self._pub)
            if st != CursorState.VALID:
                return ReadResult(ReadState.ABSTAIN, None, "none", reason=f"cursor:{st.name}")
            manifest_blob = cursor.manifest_blob
            gen_id, rseq = cursor.manifest.generation_id, cursor.read_seq
        else:
            gen_id, rseq = None, None
        gstate, handle = _bs.read_generation(self._cfg, self._ws, manifest_blob)
        if handle is None:
            return ReadResult(ReadState.ABSTAIN, None, "none", reason=f"open_generation:{gstate.name}")
        rr = handle.read_fact(fact_id)
        state = _READ_MAP.get(rr.status, ReadState.ABSTAIN)
        l0_entry = handle.l0.get(fact_id)
        if l0_entry is not None:
            version = l0_entry[0]
        else:
            registry = getattr(handle.bundle, "registry", None)
            registry_entry = registry.lookup(fact_id) if registry is not None else None
            version = registry_entry[1] if registry_entry is not None else None
        return ReadResult(state, rr.value, rr.source,
                          generation_id=gen_id if gen_id is not None else getattr(handle, "generation_id", None),
                          read_seq=rseq if rseq is not None else getattr(handle, "read_seq", None),
                          version=version)

    def get(self, scope: int, fact_id: int, read_view: ReadViewHandle | None = None) -> ReadResult:
        self._check_open()
        if not self._scope_ok(scope):
            return ReadResult(ReadState.ABSTAIN_SCOPE, None, "none", reason="scope != instância")
        manifest_blob = None
        if read_view is not None:
            if read_view.scope_id != self._cfg.scope_id:
                return ReadResult(ReadState.ABSTAIN_SCOPE, None, "none", reason="read_view de outro scope")
            manifest_blob = read_view.manifest_blob
        return self._read(fact_id, manifest_blob)

    # ---------------------------------------------------------------- consulta (chave explícita)
    def query(self, scope: int, query, context=None, limit: int = 1) -> QueryResult:
        """FH-04: consulta por CHAVE EXPLÍCITA (fact_id). A rota semântica real é FH-06. Toda query
        devolve proveniência + verificação + motivo de abstenção."""
        self._check_open()
        if not self._scope_ok(scope):
            return QueryResult(QueryState.ABSTAIN_SCOPE, None, None, "scope != instância")
        if not isinstance(query, int):
            return QueryResult(QueryState.ABSTENTION, None, None,
                               "FH-04 só resolve chave explícita (fact_id int); rota semântica é FH-06")
        rr = self._read(query, None)
        if rr.state == ReadState.PRESENT:
            prov = Provenance(query, rr.version, rr.source, rr.generation_id, rr.read_seq, "verified")
            return QueryResult(QueryState.EVIDENCE, rr.value, prov, "")
        if rr.state == ReadState.DELETED:
            prov = Provenance(query, rr.version, rr.source, rr.generation_id, rr.read_seq, "rejected")
            return QueryResult(QueryState.ABSTENTION, None, prov, "fato deletado (terminal)")
        prov = Provenance(query, None, "none", rr.generation_id, rr.read_seq, "absent")
        return QueryResult(QueryState.ABSTENTION, None, prov,
                           f"{rr.state.value}" + (f":{rr.reason}" if rr.reason else ""))

    # ---------------------------------------------------------------- read view
    def capture_read_view(self, scope: int) -> ReadViewHandle | None:
        self._check_open()
        if not self._scope_ok(scope):
            return None
        st, cursor = _bs.current_cursor(self._cfg, self._ws, self._pub)
        if st != CursorState.VALID:
            return None
        return ReadViewHandle(self._cfg.scope_id, cursor.manifest.generation_id,
                              cursor.read_seq, cursor.manifest_blob)

    # ---------------------------------------------------------------- compaction
    def compact(self, scope: int) -> CompactResult:
        self._check_open()
        if not self._scope_ok(scope):
            return CompactResult(CompactState.FAILED, None, "scope != instância")
        with self._lock:
            if self._writer is None:
                return CompactResult(CompactState.NOTHING_TO_DO, None, "sem writer ativo (scope vazio)")
            kr = _bs.keyring_for(self._cfg)
            res = _CP.prepare_compaction(self._writer, self._ws, self._ws, kr)
            if res.state != _CP.CompactionState.PREPARED:
                mapping = {
                    _CP.CompactionState.FAILED: CompactState.FAILED,
                    _CP.CompactionState.POISONED: CompactState.POISONED,
                    _CP.CompactionState.INCOMPATIBLE: CompactState.INCOMPATIBLE,
                }
                return CompactResult(mapping.get(res.state, CompactState.FAILED), None, res.reason)
            st, cursor = _bs.current_cursor(self._cfg, self._ws, self._pub)
            if st != CursorState.VALID:
                return CompactResult(CompactState.FAILED, None, f"cursor:{st.name}")
            pr = publish_maintenance(self._pub, self._ws, self._ws, kr, cursor, res.prepared)
            if pr.proof is None:
                return CompactResult(CompactState.FAILED, None, f"publish_maintenance:{pr.state.name}")
            act = GroupCommitStore.activate_compacted(res.prepared, pr.proof, self._pub,
                                                      self._ws, self._ws, kr)
            if act.store is None:
                return CompactResult(CompactState.FAILED, None, f"activate_compacted:{act.state}")
            self._writer = act.store
            new_gen = getattr(res.prepared, "generation_id", None)
            return CompactResult(CompactState.COMPACTED, new_gen, "ok")

    # ---------------------------------------------------------------- recovery
    def recover(self, scope: int) -> RecoverResult:
        """Recovery é READ-ONLY (nunca reparo silencioso): observa o CURRENT e classifica a cauda não
        publicada. Não retoma o writer aqui (o resume adquire o lease)."""
        self._check_open()
        if not self._scope_ok(scope):
            return RecoverResult(RecoverState.CORRUPT, None, None, reason="scope != instância")
        kr = _bs.keyring_for(self._cfg)
        rr = _REC.recover(self._pub, self._ws, self._ws, kr)
        _map = {
            _REC.RecoveryState.RECOVERED: RecoverState.RECOVERED,
            _REC.RecoveryState.NO_CURRENT: RecoverState.NO_CURRENT,
            _REC.RecoveryState.ABSTAIN_CURRENT: RecoverState.ABSTAIN_CURRENT,
            _REC.RecoveryState.MISSING_REQUIRED: RecoverState.MISSING_REQUIRED,
            _REC.RecoveryState.CORRUPT: RecoverState.CORRUPT,
            _REC.RecoveryState.INCOMPATIBLE: RecoverState.INCOMPATIBLE,
            _REC.RecoveryState.RESOURCE_LIMIT: RecoverState.RESOURCE_LIMIT,
        }
        gh = rr.generation_handle
        plan = rr.writer_resume_plan
        return RecoverResult(
            _map.get(rr.state, RecoverState.CORRUPT),
            getattr(gh, "generation_id", None) if gh is not None else None,
            getattr(rr.published_cursor, "read_seq", None) if rr.published_cursor is not None else None,
            unpublished_tail_bytes=getattr(plan, "unpublished_tail_bytes", 0) if plan is not None else 0,
            process_crash_tested=True,
            power_loss_tested=False,
            reason=rr.reason_code,
        )

    # ---------------------------------------------------------------- export / audit
    def _enumerate_facts(self):
        """Lê o estado atual de cada fact_id conhecido por esta instância. (Enumeração completa da
        geração exigiria varrer o registry; para a fachada standalone basta o conjunto observado.)"""
        st, cursor = _bs.current_cursor(self._cfg, self._ws, self._pub)
        if st != CursorState.VALID:
            return st, None, []
        out = []
        for fid in sorted(self._seen_facts):
            rr = self._read(fid, cursor.manifest_blob)
            out.append((fid, rr))
        return st, cursor, out

    def export(self, scope: int, policy=None) -> ExportResult:
        self._check_open()
        if not self._scope_ok(scope):
            return ExportResult(scope, None, None, (), "", "scope != instância")
        st, cursor, facts = self._enumerate_facts()
        if cursor is None:
            return ExportResult(scope, None, None, (), "", f"cursor:{st.name}")
        exported = []
        for fid, rr in facts:
            deletion = "deleted" if rr.state == ReadState.DELETED else "live"
            payload = f"{fid}|{rr.value}|{rr.source}|{deletion}".encode()
            checksum = hashlib.sha256(payload).hexdigest()
            exported.append(ExportedFact(fid, rr.version, rr.value, rr.source, deletion, checksum))
        return ExportResult(scope, cursor.manifest.generation_id, cursor.read_seq,
                            tuple(exported), cursor.proof.manifest_sha256.hex(), "ok")

    def audit(self, scope: int) -> AuditReport:
        self._check_open()
        if not self._scope_ok(scope):
            return AuditReport(scope, None, None, "scope_mismatch", False, ("scope != instância",), {})
        st, cursor = _bs.current_cursor(self._cfg, self._ws, self._pub)
        findings = []
        live = sum(1 for v in self._seen_facts.values() if v == "live")
        deleted = sum(1 for v in self._seen_facts.values() if v == "deleted")
        if cursor is None:
            return AuditReport(scope, None, None, st.name, False,
                               (f"cursor inválido: {st.name}",), {})
        ledger = compute_ledger(self._cfg, self._ws, self._pub, cursor,
                                live_facts=live, deleted_facts=deleted)
        if not ledger.accounts():
            findings.append("ledger não fecha contabilmente")
        # invariante barata: deleções permanecem terminais na geração viva
        for fid, state in self._seen_facts.items():
            if state == "deleted":
                rr = self._read(fid, cursor.manifest_blob)
                if rr.state != ReadState.DELETED:
                    findings.append(f"delete não-terminal em fact {fid}: {rr.state.value}")
        return AuditReport(scope, cursor.manifest.generation_id, cursor.read_seq,
                           st.name, len(findings) == 0, tuple(findings), ledger.as_dict())

    # ---------------------------------------------------------------- introspecção
    @property
    def config(self) -> HorizonConfig:
        return self._cfg

    @property
    def scope_id(self) -> int:
        return self._cfg.scope_id
