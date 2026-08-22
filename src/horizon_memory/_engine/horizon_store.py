# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""horizon_store.py — camada de sistema do Horizon Memory (V23.0 + V23.0.1).

Compõe L0 (WAL, por **FactId**) + geração imutável {registry, campo(L1 por ordinal), bulk} +
tombstones num caminho de leitura fail-closed, ligado a uma `ReadView`.

Correções de V23.0.1 (auditoria):
1. `SequentialModel` unifica put/delete numa linha de versão — **DELETE não pode sofrer
   downgrade** (versão menor é stale; versão igual com payload diferente é conflito).
2. `FactRegistry` valida **identidade completa**: fact_id crescente, **ordinais únicos e em
   `[0, fact_count)`**, versões válidas, sem alias, `n_entries` sob limite antes de alocar; o
   header carrega `fact_count`.
3. `read()` liga **registry/campo/bulk à `ReadView`** (mesmo scope+generation) e é **L0-first
   por FactId**: uma operação recente (PUT/DELETE) é vista antes do registry base — senão um
   fato criado após a geração não existiria no registry e um DELETE recente seria ignorado.

Regra arquitetural (para V23-A): L0/WAL usa **sempre FactId** (identidade estável); L1 usa
**Ordinal** (local da geração); só a compaction traduz FactId→Ordinal.
"""

from __future__ import annotations

import hashlib
import hmac
import struct
from dataclasses import dataclass

from horizon_memory._engine.residual_field import (
    ABSTAIN_EPOCH,
    ABSTAIN_INTEGRITY,
    ABSTAIN_SCOPE,
    CORRECT,
    DELETED,
    FALLBACK,
    NO_RESIDUAL,
    OUT_OF_RANGE,
    TAG_BYTES,
    IncompatibleError,
    IntegrityError,
    ResidualField,
    resolve,
)

REG_MAGIC = b"HRG1"
REG_FORMAT_VERSION = 2                       # V23.0.1: header ganhou fact_count
_REG_HEADER = struct.Struct("<4sHIIII")     # magic, ver, scope, generation_id, fact_count, n_entries
_REG_ENTRY = struct.Struct("<QII")          # fact_id u64, ordinal u32, fact_version u32

# limites (resource exhaustion): validados ANTES de calcular offsets/alocar
MAX_REGISTRY_ENTRIES = 1 << 24
MAX_FACT_COUNT = 1 << 30

# operações lógicas (L0/WAL e modelo)
OP_PUT = "put"
OP_DELETE = "delete"


@dataclass(frozen=True)
class ReadView:
    """Snapshot imutável de leitura com dois watermarks: `base_seq` (incorporado à geração na
    compaction) e `read_seq` (último commit L0 visível). A leitura aplica, por fato, o último
    registro L0 com `seq <= read_seq`, e cai na geração para o resto."""
    generation_id: int
    scope_id: int
    base_seq: int
    read_seq: int


@dataclass(frozen=True)
class ReadResult:
    status: str            # correct | deleted | from_bulk | fallback_bulk | abstain | not_found
    value: int | None
    authoritative: bool
    source: str            # l0 | residual | tombstone | bulk | none


class FactRegistry:
    """Mapa autenticado FactId→(ordinal, fact_version), validado por inteiro no open."""

    def __init__(self, blob: bytes, key: bytes):
        self._blob = blob
        self._key = key
        if len(blob) < _REG_HEADER.size:
            raise IntegrityError("registro menor que o header")
        magic, ver, scope, gen, fact_count, n = _REG_HEADER.unpack_from(blob, 0)
        if magic != REG_MAGIC:
            raise IntegrityError("magic de registro inválido")
        if ver != REG_FORMAT_VERSION:
            raise IncompatibleError(f"versão de registro não suportada: {ver}")
        # limites ANTES de calcular offsets (parser canônico sem limites vira DoS)
        if n > MAX_REGISTRY_ENTRIES:
            raise IntegrityError("n_entries acima do limite")
        if fact_count > MAX_FACT_COUNT:
            raise IntegrityError("fact_count acima do limite")
        self.scope_id = scope
        self.generation_id = gen
        self.fact_count = fact_count
        self.n_entries = n
        self._entries_off = _REG_HEADER.size
        self._tag_off = self._entries_off + n * _REG_ENTRY.size
        self._expected_len = self._tag_off + TAG_BYTES
        if len(blob) != self._expected_len:
            raise IntegrityError(f"comprimento de registro não canônico: {len(blob)} != {self._expected_len}")
        if not self._verify():
            raise IntegrityError("MAC de registro inválido")
        # invariantes de identidade (o MAC prova autenticidade dos bytes, não a semântica)
        prev_fid = -1
        seen_ordinals: set[int] = set()
        for i in range(n):
            fid, ordinal, fver = _REG_ENTRY.unpack_from(blob, self._entries_off + i * _REG_ENTRY.size)
            if fid <= prev_fid:
                raise IntegrityError("fact_ids não estritamente crescentes")
            prev_fid = fid
            if not (0 <= ordinal < fact_count):
                raise IntegrityError("ordinal fora de [0, fact_count)")
            if ordinal in seen_ordinals:
                raise IntegrityError("alias de ordinal (dois FactId → mesmo ordinal)")
            seen_ordinals.add(ordinal)
            if fver < 1:
                raise IntegrityError("fact_version inválida (<1)")

    @classmethod
    def build(cls, mapping: dict[int, tuple[int, int]], scope_id: int, generation_id: int,
              fact_count: int, key: bytes) -> "FactRegistry":
        """mapping: fact_id → (ordinal, fact_version)."""
        items = sorted(mapping.items())
        header = _REG_HEADER.pack(REG_MAGIC, REG_FORMAT_VERSION, scope_id, generation_id,
                                  fact_count, len(items))
        body = b"".join(_REG_ENTRY.pack(fid, ordv[0], ordv[1]) for fid, ordv in items)
        tag = cls._mac(key, header, body)
        return cls(header + body + tag, key)

    @staticmethod
    def _mac(key: bytes, header: bytes, body: bytes) -> bytes:
        m = hmac.new(key, digestmod=hashlib.sha256)
        m.update(b"REGISTRY")
        m.update(header)
        m.update(body)
        return m.digest()[:TAG_BYTES]

    def _verify(self) -> bool:
        header = self._blob[:_REG_HEADER.size]
        body = self._blob[self._entries_off:self._tag_off]
        expected = self._mac(self._key, header, body)
        got = self._blob[self._tag_off:self._tag_off + TAG_BYTES]
        return hmac.compare_digest(expected, got)

    def lookup(self, fact_id: int) -> tuple[int, int] | None:
        """Busca binária pelos bytes → (ordinal, fact_version) ou None."""
        lo, hi = 0, self.n_entries
        while lo < hi:
            mid = (lo + hi) // 2
            fid, ordinal, fver = _REG_ENTRY.unpack_from(self._blob, self._entries_off + mid * _REG_ENTRY.size)
            if fid == fact_id:
                return (int(ordinal), int(fver))
            if fid < fact_id:
                lo = mid + 1
            else:
                hi = mid
        return None

    def serialize(self) -> bytes:
        return self._blob

    def iter_entries(self):
        """Itera (fact_id, ordinal, fact_version) na ordem canônica (fact_id crescente). Usado pela
        compaction (B4) para enumerar os fatos da base ao materializar a geração nova."""
        for i in range(self.n_entries):
            fid, ordinal, fver = _REG_ENTRY.unpack_from(self._blob, self._entries_off + i * _REG_ENTRY.size)
            yield (int(fid), int(ordinal), int(fver))

    def ordinals(self) -> set[int]:
        out = set()
        for i in range(self.n_entries):
            _fid, ordinal, _fver = _REG_ENTRY.unpack_from(self._blob, self._entries_off + i * _REG_ENTRY.size)
            out.add(int(ordinal))
        return out

    @classmethod
    def try_open(cls, blob: bytes | None, key: bytes) -> "FactRegistry | None":
        if blob is None:
            return None
        try:
            return cls(blob, key)
        except (struct.error, IndexError, ValueError, IntegrityError, IncompatibleError):
            return None

    @property
    def n_bits(self) -> int:
        return len(self._blob) * 8


@dataclass(frozen=True)
class BaseValidation:
    valid: bool
    reason: str


def validate_base_artifacts(registry, bulk, residual, tombstone, scope_id: int,
                            generation_id: int, fact_count: int) -> BaseValidation:
    """Prova que os quatro artefatos formam UMA base coerente antes de montar o GenerationBundle.
    Componentes abertos individualmente não devem ser combinados sem esta validação cruzada."""
    parts = [("registry", registry.scope_id, registry.generation_id, registry.fact_count),
             ("bulk", bulk.scope_id, bulk.generation_id, bulk.fact_count),
             ("residual", residual.scope_id, residual.bulk_epoch, residual.fact_count),
             ("tombstone", tombstone.scope_id, tombstone.bulk_epoch, tombstone.fact_count)]
    for name, sc, gn, fc in parts:
        if sc != scope_id or gn != generation_id:
            return BaseValidation(False, f"{name}: scope/generation divergente")
        if fc != fact_count:
            return BaseValidation(False, f"{name}: fact_count divergente")
    reg_ord = registry.ordinals()
    bulk_sup = bulk.support_ordinals()
    if bulk_sup != reg_ord:
        return BaseValidation(False, "support(bulk) != ordinais(registry)")
    res_sup = residual.support_ordinals()
    if not res_sup <= bulk_sup:
        return BaseValidation(False, "support(residual) ⊄ support(bulk)")
    tomb_sup = tombstone.support_ordinals()
    if not tomb_sup <= bulk_sup:
        return BaseValidation(False, "support(tombstone) ⊄ support(bulk)")
    if res_sup & tomb_sup:
        return BaseValidation(False, "support(residual) ∩ support(tombstone) != ∅ (correção morta)")
    return BaseValidation(True, "ok")


@dataclass(frozen=True)
class VersionedFact:
    version: int
    op: str
    value: int | None


class _BaseSentinel:
    __slots__ = ("name",)

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"<base:{self.name}>"


BASE_ABSENT = _BaseSentinel("ABSENT")     # fato não existe na base → versão nova é aceita
BASE_ABSTAIN = _BaseSentinel("ABSTAIN")   # base ilegível → escrita recusada fail-closed


def preflight(cur, op: str, version: int, value: int | None) -> str:
    """Regra de versão SEM mutação, compartilhada por prepare_batch e reduce_frames. `cur` é a
    versão vigente (do L0 ou da base) como (version, op, value), ou None se o fato é novo."""
    if cur is None:
        return "applied"
    cv, cop, cval = cur
    if version < cv:
        return "stale"
    if version == cv:
        return "idempotent" if (op, value) == (cop, cval) else "conflict"
    return "applied"


class EmptyBaseView:
    """Base vazia (sem geração anterior) — todo fato é novo, nenhum operation_id retido."""

    def lookup(self, fact_id: int):
        return BASE_ABSENT

    def dedup_probe(self, operation_id: bytes, digest: bytes):
        return ("new", None)


def classify_dedup(wal_check: str, wal_get_seq, base, operation_id: bytes, digest: bytes):
    """B4.2 — AUTORIDADE ÚNICA de deduplicação por `operation_id`, usada IDENTICAMENTE no ingresso
    (online, `prepare_batch`) e na recuperação (replay do reducer). Decide na ORDEM: o `TxIdIndex` do WAL
    CORRENTE primeiro (o retry no WAL vigente prevalece sobre a classificação conservadora da base),
    depois a `DedupTable` da base em caso de miss. Devolve `(decision, seq)`:
      "dedup_replay"        retry conhecido + mesmo digest (seq original devolvido);
      "txid_conflict"       mesmo operation_id + digest diferente (fail-closed);
      "idempotency_expired" conhecido na base mas sem memória viva (abaixo do retained_floor) — nunca
                            reaplicado;
      "new"                 genuinamente novo — segue para o preflight de versão.
    `wal_check` é o resultado de `tx.check(operation_id, digest)`; `wal_get_seq` recupera o seq do WAL."""
    if wal_check == "dedup_replay":
        return ("dedup_replay", wal_get_seq(operation_id))
    if wal_check == "txid_conflict":
        return ("txid_conflict", None)
    kind, seq = base.dedup_probe(operation_id, digest)     # WAL miss → base
    if kind == "replay":
        return ("dedup_replay", seq)
    if kind == "conflict":
        return ("txid_conflict", None)
    if kind == "expired":
        return ("idempotency_expired", None)
    if kind == "abstain":                                  # base de outra geração/scope → fail-closed
        return ("abstain_base", None)
    return ("new", None)


class BundleBaseView:
    """Visão imutável da versão vigente na geração base: FactId → Registry → Ordinal → Tombstone →
    Residual/Bulk, devolvendo `VersionedFact`/`BASE_ABSENT`/`BASE_ABSTAIN`. É consultada só no
    primeiro write de cada fato — a base nunca é copiada para o L0."""

    def __init__(self, view: ReadView, bundle: "GenerationBundle", base_dedup=None):
        self.view = view
        self.bundle = bundle
        self.base_dedup = base_dedup      # DedupTable da base (B4.2) ou None (sem memória de dedup)

    def dedup_probe(self, operation_id: bytes, digest: bytes):
        """B4.2/FH-00.1 — probe de dedup contra a `DedupTable` da base (mesma geração/scope). Sem tabela
        → sem memória (`new`). Uma tabela de OUTRA geração/scope NÃO pode virar `new` (deixaria passar um
        duplicado): devolve `abstain` → FAIL-CLOSED no ingresso (ABSTAIN_BASE)."""
        d = self.base_dedup
        if d is None:
            return ("new", None)
        if d.scope_id != self.view.scope_id or d.generation_id != self.view.generation_id:
            return ("abstain", None)          # base de outra geração/scope → fail-closed, nunca "new"
        return d.probe(operation_id, digest)

    def lookup(self, fact_id: int):
        b = self.bundle
        if b.scope_id != self.view.scope_id or b.generation_id != self.view.generation_id:
            return BASE_ABSTAIN
        if b.registry is None:
            return BASE_ABSTAIN
        ent = b.registry.lookup(fact_id)
        if ent is None:
            return BASE_ABSENT
        ordinal, fver = ent
        status, value = resolve(b.field, b.tombstone, ordinal, self.view.scope_id,
                                self.view.generation_id)
        if status == DELETED:
            return VersionedFact(fver, OP_DELETE, None)
        if status == CORRECT:
            return VersionedFact(fver, OP_PUT, int(value))
        if status == NO_RESIDUAL:
            bst, bval = b.bulk.lookup(ordinal)          # bulk é indexado por Ordinal (V23-B1.1)
            if bst == "value":
                return VersionedFact(fver, OP_PUT, bval)
            return BASE_ABSTAIN     # Registry afirma existência; ausência/oob no bulk = corrupção
        return BASE_ABSTAIN     # FALLBACK / abstain_* → base não confiável


def apply_versioned(state: dict, fact_id: int, version: int, op: str, value: int | None) -> str:
    """Aplica uma operação na linha de versão de um fato. Regra congelada:
    versão maior vence; versão igual → idempotente se (op,payload) iguais, senão conflito;
    versão menor → stale. Fecha o downgrade de tombstone (DELETE v=5 após DELETE v=10 é stale)."""
    cur = state.get(fact_id)
    if cur is None or version > cur[0]:
        state[fact_id] = (version, op, value)
        return "applied"
    if version == cur[0]:
        if (op, value) == (cur[1], cur[2]):
            return "idempotent"
        return "conflict"
    return "stale"


class SequentialModel:
    """Modelo sequencial autoritativo. Linha de versão única por fato; deleção terminal para
    aquela versão; sem downgrade."""

    def __init__(self):
        self._state: dict[int, tuple[int, str, int | None]] = {}  # fact_id → (version, op, value)
        self.conflicts = 0

    def put(self, fact_id: int, value: int, version: int) -> str:
        r = apply_versioned(self._state, fact_id, version, OP_PUT, int(value))
        if r == "conflict":
            self.conflicts += 1
        return r

    def delete(self, fact_id: int, version: int) -> str:
        r = apply_versioned(self._state, fact_id, version, OP_DELETE, None)
        if r == "conflict":
            self.conflicts += 1
        return r

    def read(self, fact_id: int) -> tuple[str, int | None]:
        cur = self._state.get(fact_id)
        if cur is None:
            return ("not_found", None)
        if cur[1] == OP_DELETE:
            return ("deleted", None)
        return ("value", cur[2])


class WalIndex:
    """L0 em memória, reconstruído do WAL, keyed por **FactId**: fact_id → (version, op, value).

    `apply()` usa a mesma regra de versão do modelo. O gating por `read_seq` acontece no replay
    (o recovery só aplica frames com seq ≤ read_seq), então `get()` já devolve o estado visível.
    """

    def __init__(self):
        self._state: dict[int, tuple[int, str, int | None]] = {}
        self.conflicts = 0

    def apply(self, fact_id: int, version: int, op: str, value: int | None) -> str:
        r = apply_versioned(self._state, fact_id, version, op, value)
        if r == "conflict":
            self.conflicts += 1
        return r

    def get(self, fact_id: int) -> tuple[int, str, int | None] | None:
        return self._state.get(fact_id)

    def items(self):
        """Iterador READ-ONLY (fact_id, (version, op, value)) — mesma interface do `ShardedWalIndex`,
        para que a compaction (B4) funcione idêntica com índice simples ou sharded, sem tocar `_state`."""
        return self._state.items()

    def clone(self) -> "WalIndex":
        """Cópia profunda o suficiente para shadow state (as tuplas de valor são imutáveis)."""
        c = WalIndex()
        c._state = dict(self._state)
        c.conflicts = self.conflicts
        return c

    def begin_mutation(self) -> "_WalCloneBuilder":
        """Baseline de clone integral com a mesma API do COW sharded (begin_mutation/freeze)."""
        return _WalCloneBuilder(self.clone())


class _WalCloneBuilder:
    def __init__(self, idx: "WalIndex"):
        self._i = idx

    def get(self, fact_id):
        return self._i.get(fact_id)

    def apply(self, fact_id, version, op, value):
        return self._i.apply(fact_id, version, op, value)

    def freeze(self) -> "WalIndex":
        return self._i


class TxIdIndex:
    """Deduplicação de retry por `operation_id` (128 bits), reconstruída do WAL.

    Resolve `fsync feito → crash antes do ACK → cliente repete`:
    - id novo → executa;
    - id existente + mesmo digest → `dedup_replay` (devolve o resultado original, sem novo frame);
    - id existente + digest diferente → `txid_conflict` (recusa).

    Horizonte de sobrevivência à compaction (por quantas gerações o id é lembrado) é um contrato
    do `EpochManifest` em V23-B; aqui o índice é por segmento.
    """

    def __init__(self):
        self._m: dict[bytes, tuple[bytes, int]] = {}   # operation_id → (content_digest, seq)

    def check(self, operation_id: bytes, digest: bytes) -> str:
        e = self._m.get(operation_id)
        if e is None:
            return "new"
        return "dedup_replay" if hmac.compare_digest(e[0], digest) else "txid_conflict"

    def record(self, operation_id: bytes, digest: bytes, seq: int) -> None:
        self._m[operation_id] = (digest, seq)

    def items(self):
        """Iterador READ-ONLY (operation_id, (content_digest, seq)) — mesma interface do
        `ShardedTxIdIndex`, para a compaction (B4) tratar índice simples e sharded identicamente."""
        return self._m.items()

    def get_seq(self, operation_id: bytes) -> int | None:
        e = self._m.get(operation_id)
        return None if e is None else e[1]

    def clone(self) -> "TxIdIndex":
        c = TxIdIndex()
        c._m = dict(self._m)
        return c

    def begin_mutation(self) -> "_TxCloneBuilder":
        return _TxCloneBuilder(self.clone())


class _TxCloneBuilder:
    def __init__(self, idx: "TxIdIndex"):
        self._i = idx

    def check(self, operation_id, digest):
        return self._i.check(operation_id, digest)

    def get_seq(self, operation_id):
        return self._i.get_seq(operation_id)

    def record(self, operation_id, digest, seq):
        return self._i.record(operation_id, digest, seq)

    def freeze(self) -> "TxIdIndex":
        return self._i


@dataclass
class GenerationBundle:
    """Componentes imutáveis de uma geração; todos devem casar com a `ReadView`."""
    scope_id: int
    generation_id: int
    field: ResidualField | None
    registry: FactRegistry | None
    tombstone: object              # OpenResult de open_tombstone (L0, por ordinal)
    bulk: object                   # BulkSnapshot (por ordinal) — nunca dict[FactId]


def _view_matches(view: ReadView, bundle: GenerationBundle) -> bool:
    if bundle.scope_id != view.scope_id or bundle.generation_id != view.generation_id:
        return False
    reg = bundle.registry
    if reg is not None and (reg.scope_id != view.scope_id or reg.generation_id != view.generation_id):
        return False
    fld = bundle.field
    if fld is not None and (fld.scope_id != view.scope_id or fld.bulk_epoch != view.generation_id):
        return False
    blk = bundle.bulk
    if blk is not None and (blk.scope_id != view.scope_id or blk.generation_id != view.generation_id):
        return False
    tomb = bundle.tombstone
    layer = getattr(tomb, "layer", None)
    if layer is not None and (layer.scope_id != view.scope_id or layer.bulk_epoch != view.generation_id):
        return False
    return True


def read(view: ReadView, bundle: GenerationBundle, l0: WalIndex, fact_id: int) -> ReadResult:
    """O CONSUMIDOR completo, ligado à ReadView. L0-first por FactId, depois a geração.

    `deleted` (L0 ou L1) é **terminal** — jamais consulta bulk. `abstain_*` também não. Só
    `no_residual` e `FALLBACK` vão ao bulk; `FALLBACK` é não autoritativo.
    """
    # 0) binding: registry/campo/bulk têm que pertencer à mesma visão (geração+scope)
    if not _view_matches(view, bundle):
        return ReadResult("abstain", None, False, "none")

    # 1) L0 primeiro (por FactId, já limitado a seq ≤ read_seq no replay)
    l0e = l0.get(fact_id)
    if l0e is not None:
        _ver, op, value = l0e
        if op == OP_DELETE:
            return ReadResult("deleted", None, True, "l0")   # TERMINAL
        return ReadResult("correct", int(value), True, "l0")

    # 2) identidade da geração: FactId → ordinal (autenticado)
    if bundle.registry is None:
        return ReadResult("abstain", None, False, "none")
    ent = bundle.registry.lookup(fact_id)
    if ent is None:
        return ReadResult("not_found", None, True, "none")
    ordinal, _fact_version = ent

    # 3) L1: tombstone da geração (por ordinal) + campo residual
    status, value = resolve(bundle.field, bundle.tombstone, ordinal, view.scope_id, view.generation_id)
    if status == CORRECT:
        return ReadResult("correct", value, True, "residual")
    if status == DELETED:
        return ReadResult("deleted", None, True, "tombstone")   # TERMINAL
    if status == NO_RESIDUAL:
        bst, bval = bundle.bulk.lookup(ordinal)          # bulk indexado por Ordinal (V23-B1.1)
        if bst == "value":
            return ReadResult("from_bulk", bval, True, "bulk")
        # Registry conhece o fato mas o bulk não tem seu ordinal → base inconsistente → abstain
        return ReadResult("abstain", None, False, "none")
    if status == FALLBACK:
        bst, bval = bundle.bulk.lookup(ordinal)
        if bst == "value":
            return ReadResult("fallback_bulk", bval, False, "bulk")
        # Registry conhece o fato (chegamos aqui via ordinal) mas o bulk não o tem → inconsistente
        return ReadResult("abstain", None, False, "none")
    assert status in (ABSTAIN_SCOPE, ABSTAIN_EPOCH, ABSTAIN_INTEGRITY, OUT_OF_RANGE), status
    return ReadResult("abstain", None, False, "none")
