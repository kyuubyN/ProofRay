# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""horizon_artifacts.py — abertura tipada, object store content-addressed e DedupTable (V23-B1.2/3/4).

Fecha o B1: nenhuma decisão de política pode depender de `None`. Cada artefato abre num estado
**tipado** (`ArtifactOpenState`), com `reason_code` estável (não a mensagem da exceção). Os objetos
são endereçados por conteúdo (`objects/<sha256-hex>.hobj`) — nome derivado só do digest, path
traversal irrepresentável. E só um conjunto **validado** (`ValidatedBaseArtifacts`) monta um bundle.

`ArtifactLimits` impõe limites configuráveis **antes** de qualquer alocação (nada de "1<<30 permite
tentar 1 GB").
"""

from __future__ import annotations

import hashlib
import hmac
import os
import struct
from dataclasses import dataclass
from enum import Enum, IntEnum
from pathlib import Path

from horizon_memory._engine.horizon_bulk import BULK_MAGIC, BulkSnapshot, _BH
from horizon_memory._engine.horizon_durability import fsync_dir
from horizon_memory._engine.horizon_store import (
    FactRegistry,
    GenerationBundle,
    ReadView,
    validate_base_artifacts,
    _REG_HEADER,
)
from horizon_memory._engine.residual_field import (
    TAG_BYTES,
    IncompatibleError,
    IntegrityError,
    ResidualField,
    TombstoneLayer,
    _HEADER as _RES_HEADER,
    _TOMB_HEADER,
    open_tombstone,
)


# ------------------------------ tipos ------------------------------
class ArtifactKind(IntEnum):
    REGISTRY = 1
    BULK = 2
    RESIDUAL = 3
    TOMBSTONE = 4
    DEDUP = 5


class ArtifactOpenState(Enum):
    VALID = 1
    ABSENT_ALLOWED = 2
    REQUIRED_MISSING = 3
    CORRUPT = 4
    INCOMPATIBLE = 5
    RESOURCE_LIMIT = 6


# reason_codes estáveis (contrato; nunca a mensagem da exceção)
BAD_MAGIC = "BAD_MAGIC"
UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
NON_CANONICAL_LENGTH = "NON_CANONICAL_LENGTH"
DIGEST_MISMATCH = "DIGEST_MISMATCH"
MAC_MISMATCH = "MAC_MISMATCH"
COUNT_LIMIT = "COUNT_LIMIT"
CROSS_SCOPE = "CROSS_SCOPE"
MISSING = "MISSING"
BAD_DESCRIPTOR = "BAD_DESCRIPTOR"
KEY_UNKNOWN = "KEY_UNKNOWN"
KIND_MISMATCH = "KIND_MISMATCH"
OK = "OK"


class Keyring:
    """Resolve `key_id` → chave. `None` para id desconhecido (nunca lança)."""

    def __init__(self, keys: dict[int, bytes]):
        self._k = dict(keys)

    def get(self, key_id: int) -> bytes | None:
        return self._k.get(key_id)


class ArtifactResourceLimitError(Exception):
    pass


@dataclass(frozen=True)
class ArtifactLimits:
    max_blob_bytes: int = 64 << 20
    max_fact_count: int = 1 << 24
    max_registry_entries: int = 1 << 24
    max_corrections: int = 1 << 22
    max_pages: int = 1 << 20
    max_dedup_entries: int = 1 << 22
    max_clients: int = 1 << 20


# versão do FORMATO real de cada artefato (não do envelope): o descriptor tem que coincidir com a
# versão do header interno. REGISTRY já está no formato 2.
DESCRIPTOR_VERSION = {
    ArtifactKind.REGISTRY: 2,
    ArtifactKind.BULK: 1,
    ArtifactKind.RESIDUAL: 1,
    ArtifactKind.TOMBSTONE: 1,
    ArtifactKind.DEDUP: 1,
}
WAL_DESCRIPTOR_VERSION = 2
IO_ERROR = "IO_ERROR"
CONCURRENT_CHANGE = "CONCURRENT_CHANGE"


@dataclass(frozen=True)
class ArtifactDescriptor:
    kind: ArtifactKind
    format_version: int
    required: bool
    byte_length: int
    sha256: bytes            # 32 bytes completos
    key_id: int


def make_descriptor(kind: ArtifactKind, blob: bytes, required: bool = True,
                    key_id: int = 0) -> ArtifactDescriptor:
    """Descriptor com a versão de formato correta para o `kind` (coincide com o header interno)."""
    return ArtifactDescriptor(kind, DESCRIPTOR_VERSION[kind], required, len(blob),
                              hashlib.sha256(blob).digest(), key_id)


@dataclass(frozen=True)
class OpenArtifact:
    state: ArtifactOpenState
    kind: ArtifactKind
    component: object | None
    reason_code: str


# ------------------------------ object store content-addressed ------------------------------
@dataclass(frozen=True)
class ObjectReadResult:
    ok: bool
    blob: bytes | None
    reason: str


def _os_write_all(fd: int, data: bytes) -> None:
    mv, n = memoryview(data), 0
    while n < len(data):
        try:
            w = os.write(fd, mv[n:])
        except InterruptedError:          # EINTR
            continue
        if w <= 0:
            raise IntegrityError("write incompleto")
        n += w


class ObjectStore:
    def __init__(self, root: str):
        self.root = Path(root)
        (self.root / "objects").mkdir(parents=True, exist_ok=True)

    def _path(self, digest_hex: str) -> Path:
        # nome derivado SÓ do digest (hex de 64 chars) — path traversal irrepresentável
        if len(digest_hex) != 64 or any(c not in "0123456789abcdef" for c in digest_hex):
            raise ValueError("digest_hex inválido")
        return self.root / "objects" / f"{digest_hex}.hobj"

    def put_object(self, blob: bytes) -> str:
        """Publica content-addressed SEM substituição: temp O_EXCL → write_all → fsync →
        hard-link exclusivo temp→final. Se o final já existe, verifica bytes/digest e nunca
        sobrescreve conteúdo divergente. Temporário é sempre removido."""
        digest_hex = hashlib.sha256(blob).hexdigest()
        final = self._path(digest_hex)
        tmp = final.with_name(f"{digest_hex}.tmp.{os.getpid()}.{id(blob)}")
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            _os_write_all(fd, blob)
            os.fsync(fd)
            os.close(fd)
            fd = -1
            try:
                os.link(str(tmp), str(final))         # criação exclusiva: falha se `final` existe
                fsync_dir(final.parent)                # V23-C0: o NOME do objeto tem que ser durável
            except FileExistsError:
                existing = self.get_limited(digest_hex, len(blob) + 1)
                if not existing.ok or existing.blob != blob:
                    raise IntegrityError("objeto existente diverge do conteúdo (não sobrescrever)")
        finally:
            if fd != -1:
                os.close(fd)
            try:
                os.unlink(str(tmp))
            except FileNotFoundError:
                pass
        return digest_hex

    def get_limited(self, digest_hex: str, max_bytes: int) -> ObjectReadResult:
        """Abre por FD, checa o tamanho por `fstat` ANTES de ler, lê exatamente o tamanho declarado
        e re-`fstat` para detectar alteração concorrente."""
        try:
            fd = os.open(str(self._path(digest_hex)), os.O_RDONLY)
        except FileNotFoundError:
            return ObjectReadResult(False, None, MISSING)
        try:
            st = os.fstat(fd)
            if st.st_size > max_bytes:
                return ObjectReadResult(False, None, COUNT_LIMIT)
            data = b""
            while len(data) < st.st_size:
                try:
                    chunk = os.read(fd, st.st_size - len(data))
                except InterruptedError:      # EINTR
                    continue
                if not chunk:
                    break
                data += chunk
            st2 = os.fstat(fd)
            if st2.st_size != st.st_size or st2.st_mtime_ns != st.st_mtime_ns or len(data) != st.st_size:
                return ObjectReadResult(False, None, "CONCURRENT_CHANGE")
        finally:
            os.close(fd)
        return ObjectReadResult(True, data, OK)

    def get(self, digest_hex: str, max_bytes: int = 1 << 30) -> bytes | None:
        r = self.get_limited(digest_hex, max_bytes)
        return r.blob if r.ok else None


# ------------------------------ peeks de contagem (limites antes de alocar) ------------------------------
def _peek_counts(kind: ArtifactKind, blob: bytes) -> dict:
    if kind == ArtifactKind.REGISTRY:
        _m, _v, _s, _g, fc, n = _REG_HEADER.unpack_from(blob, 0)
        return {"fact_count": fc, "entries": n}
    if kind == ArtifactKind.BULK:
        _m, _v, _k, _s, _g, fc, sup, _rb = _BH.unpack_from(blob, 0)
        return {"fact_count": fc, "support": sup}
    if kind == ArtifactKind.RESIDUAL:
        _m, _v, _s, _e, fc, page, ncorr, _kd = _RES_HEADER.unpack_from(blob, 0)
        pages = ((ncorr + page - 1) // page) if page else 0
        return {"fact_count": fc, "corrections": ncorr, "pages": pages}
    if kind == ArtifactKind.TOMBSTONE:
        _m, _v, _s, _e, fc = _TOMB_HEADER.unpack_from(blob, 0)
        return {"fact_count": fc}
    if kind == ArtifactKind.DEDUP:
        _m, _v, _s, _g, _f, _t, _r, ne, nc = _DEDUP_HEADER.unpack_from(blob, 0)
        return {"entries": ne, "clients": nc}
    return {}


def _check_limits(kind, counts, limits: ArtifactLimits):
    fc = counts.get("fact_count", 0)
    if fc > limits.max_fact_count:
        raise ArtifactResourceLimitError(COUNT_LIMIT)
    if counts.get("entries", 0) > (limits.max_dedup_entries if kind == ArtifactKind.DEDUP
                                   else limits.max_registry_entries):
        raise ArtifactResourceLimitError(COUNT_LIMIT)
    if counts.get("corrections", 0) > limits.max_corrections:
        raise ArtifactResourceLimitError(COUNT_LIMIT)
    if counts.get("pages", 0) > limits.max_pages:
        raise ArtifactResourceLimitError(COUNT_LIMIT)
    if counts.get("clients", 0) > limits.max_clients:
        raise ArtifactResourceLimitError(COUNT_LIMIT)


_PARSERS = {
    ArtifactKind.REGISTRY: lambda b, k: FactRegistry(b, k),
    ArtifactKind.BULK: lambda b, k: BulkSnapshot(b, k),
    ArtifactKind.RESIDUAL: lambda b, k: ResidualField(b, k),
    ArtifactKind.TOMBSTONE: lambda b, k: TombstoneLayer(b, k),
}


def open_artifact(descriptor: ArtifactDescriptor, blob: bytes | None, key: bytes = b"",
                  limits: ArtifactLimits | None = None, keyring: "Keyring | None" = None) -> OpenArtifact:
    """Fluxo obrigatório: descriptor autovalidado → key_id → presença → limites → comprimento →
    sha256 externo → parser → MAC interno. Nunca lança; devolve OpenArtifact tipado."""
    limits = limits or ArtifactLimits()
    kind = descriptor.kind
    # descriptor autovalidado ANTES de tocar bytes
    if not isinstance(kind, ArtifactKind) or len(descriptor.sha256) != 32 or descriptor.byte_length < 0:
        return OpenArtifact(ArtifactOpenState.CORRUPT, kind, None, BAD_DESCRIPTOR)
    # a versão do descriptor tem que coincidir com o formato REAL do artefato (não do envelope);
    # o header interno é reconferido pelo parser (IncompatibleError → INCOMPATIBLE).
    expected_version = DESCRIPTOR_VERSION.get(kind)
    if expected_version is None or descriptor.format_version != expected_version:
        return OpenArtifact(ArtifactOpenState.INCOMPATIBLE, kind, None, UNSUPPORTED_VERSION)
    if keyring is not None:                                   # key_id resolvido pelo keyring
        key = keyring.get(descriptor.key_id)
        if key is None:
            return OpenArtifact(ArtifactOpenState.INCOMPATIBLE, kind, None, KEY_UNKNOWN)
    if blob is None:
        st = ArtifactOpenState.REQUIRED_MISSING if descriptor.required else ArtifactOpenState.ABSENT_ALLOWED
        return OpenArtifact(st, kind, None, MISSING)
    if len(blob) > limits.max_blob_bytes:
        return OpenArtifact(ArtifactOpenState.RESOURCE_LIMIT, kind, None, COUNT_LIMIT)
    if len(blob) != descriptor.byte_length:
        return OpenArtifact(ArtifactOpenState.CORRUPT, kind, None, NON_CANONICAL_LENGTH)
    if hashlib.sha256(blob).digest() != descriptor.sha256:
        return OpenArtifact(ArtifactOpenState.CORRUPT, kind, None, DIGEST_MISMATCH)
    # limites de contagem ANTES do parser (que aloca)
    try:
        _check_limits(kind, _peek_counts(kind, blob), limits)
    except (struct.error, IndexError):
        return OpenArtifact(ArtifactOpenState.CORRUPT, kind, None, NON_CANONICAL_LENGTH)
    except ArtifactResourceLimitError:
        return OpenArtifact(ArtifactOpenState.RESOURCE_LIMIT, kind, None, COUNT_LIMIT)
    parser = _PARSERS.get(kind) or (lambda b, k: DedupTable(b, k))
    try:
        component = parser(blob, key)
    except IncompatibleError:
        return OpenArtifact(ArtifactOpenState.INCOMPATIBLE, kind, None, UNSUPPORTED_VERSION)
    except ArtifactResourceLimitError:
        return OpenArtifact(ArtifactOpenState.RESOURCE_LIMIT, kind, None, COUNT_LIMIT)
    except (struct.error, IndexError, ValueError, IntegrityError):
        return OpenArtifact(ArtifactOpenState.CORRUPT, kind, None, MAC_MISMATCH)
    return OpenArtifact(ArtifactOpenState.VALID, kind, component, OK)


# ------------------------------ DedupTable (B1.4) ------------------------------
DEDUP_MAGIC = b"HDT1"
DEDUP_FORMAT_VERSION = 1
_DEDUP_HEADER = struct.Struct("<4sHIIQQIII")  # magic,ver,scope,gen,floor(Q),through(Q),retention,entry_count,client_count
_DEDUP_ENTRY = struct.Struct("<16s16sQ")      # operation_id, content_digest, original_seq
_DEDUP_CLIENT = struct.Struct("<QQQ")         # client_id, highest_seen_txid, retained_floor_txid


class DedupTable:
    def __init__(self, blob: bytes, key: bytes):
        self._blob = blob
        self._key = key
        self._parse_and_validate()

    @classmethod
    def build(cls, scope_id, generation_id, dedup_floor_seq, dedup_through_seq, retention,
              entries, clients, key) -> "DedupTable":
        """entries: iterável de (operation_id[16], content_digest[16], original_seq).
        clients: dict client_id → (highest_seen_txid, retained_floor_txid)."""
        ents = sorted(entries, key=lambda e: e[0])
        cls_list = sorted(clients.items())
        header = _DEDUP_HEADER.pack(DEDUP_MAGIC, DEDUP_FORMAT_VERSION, scope_id, generation_id,
                                    dedup_floor_seq, dedup_through_seq, retention, len(ents), len(cls_list))
        body = b"".join(_DEDUP_ENTRY.pack(o, d, s) for o, d, s in ents)
        body += b"".join(_DEDUP_CLIENT.pack(c, hi, lo) for c, (hi, lo) in cls_list)
        mac = cls._mac(key, header, body)
        return cls(header + body + mac, key)

    @staticmethod
    def _mac(key, header, body):
        m = hmac.new(key, digestmod=hashlib.sha256)
        m.update(b"DEDUPTABLE")
        m.update(header)
        m.update(body)
        return m.digest()[:TAG_BYTES]

    def _parse_and_validate(self):
        b = self._blob
        if len(b) < _DEDUP_HEADER.size:
            raise IntegrityError("dedup menor que header")
        magic, ver, scope, gen, floor, through, retention, ne, nc = _DEDUP_HEADER.unpack_from(b, 0)
        if magic != DEDUP_MAGIC:
            raise IntegrityError("magic de dedup inválido")
        if ver != DEDUP_FORMAT_VERSION:
            raise IncompatibleError("versão de dedup não suportada")
        self.scope_id, self.generation_id = scope, gen
        self.dedup_floor_seq, self.dedup_through_seq, self.retention = floor, through, retention
        self.entry_count, self.client_count = ne, nc
        off = _DEDUP_HEADER.size
        ent_end = off + ne * _DEDUP_ENTRY.size
        cli_end = ent_end + nc * _DEDUP_CLIENT.size
        if len(b) != cli_end + TAG_BYTES:
            raise IntegrityError("comprimento de dedup não canônico")
        expected = self._mac(self._key, b[:_DEDUP_HEADER.size], b[_DEDUP_HEADER.size:cli_end])
        if not hmac.compare_digest(expected, b[cli_end:cli_end + TAG_BYTES]):
            raise IntegrityError("MAC de dedup inválido")
        # tabela vazia canônica
        if ne == 0 and floor != through + 1:
            raise IntegrityError("tabela vazia deve ter floor == through+1")
        # clients ordenados, únicos
        clients = {}
        prev_c = -1
        for i in range(nc):
            c, hi, lo = _DEDUP_CLIENT.unpack_from(b, ent_end + i * _DEDUP_CLIENT.size)
            if c <= prev_c:
                raise IntegrityError("clients não estritamente crescentes")
            prev_c = c
            if lo > hi:
                raise IntegrityError("retained_floor_txid > highest_seen_txid")
            clients[c] = (hi, lo)
        # entries ordenadas, únicas, consistentes com clients e intervalos
        prev_o = b""
        for i in range(ne):
            o, d, s = _DEDUP_ENTRY.unpack_from(b, off + i * _DEDUP_ENTRY.size)
            if o <= prev_o:
                raise IntegrityError("operation_ids não estritamente crescentes")
            prev_o = o
            if not (floor <= s <= through):
                raise IntegrityError("original_seq fora do intervalo declarado")
            client_id, txid = struct.unpack("<QQ", o)
            if client_id not in clients:
                raise IntegrityError("entry sem ClientDedupState")
            hi, lo = clients[client_id]
            if not (lo <= txid <= hi):
                raise IntegrityError("txid da entry fora de [retained_floor, highest_seen]")
        self._clients = clients

    def serialize(self) -> bytes:
        return self._blob

    def iter_entries(self):
        """(operation_id, content_digest, original_seq) na ordem canônica — para o merge de dedup entre
        compactions (B4): nunca esquecer um operation_id que a base ainda retém."""
        off = _DEDUP_HEADER.size
        for i in range(self.entry_count):
            o, d, s = _DEDUP_ENTRY.unpack_from(self._blob, off + i * _DEDUP_ENTRY.size)
            yield (bytes(o), bytes(d), int(s))

    def clients(self):
        """client_id → (highest_seen_txid, retained_floor_txid)."""
        return dict(self._clients)

    def _entry(self, operation_id: bytes):
        """Busca binária pela entry canônica (operation_ids estritamente crescentes) → (digest, seq) ou
        None. O blob é imutável e ordenado, então não há custo de índice em memória."""
        lo, hi, off, sz = 0, self.entry_count, _DEDUP_HEADER.size, _DEDUP_ENTRY.size
        target = bytes(operation_id)
        while lo < hi:
            mid = (lo + hi) // 2
            o, d, s = _DEDUP_ENTRY.unpack_from(self._blob, off + mid * sz)
            ob = bytes(o)
            if ob == target:
                return (bytes(d), int(s))
            if ob < target:
                lo = mid + 1
            else:
                hi = mid
        return None

    def probe(self, operation_id: bytes, digest: bytes):
        """B4.2 — classificação de dedup contra a base, por `operation_id` (= client_id, txid):
          ("replay", seq)  entry presente + MESMO digest → retry idempotente (devolve o seq original);
          ("conflict", None) entry presente + digest DIFERENTE → TXID_CONFLICT (fail-closed);
          ("expired", None)  cliente conhecido e `txid <= highest_seen`, mas sem entry viva (abaixo do
                             `retained_floor`, ou conhecido-mas-removido) → IDEMPOTENCY_EXPIRED, JAMAIS
                             reaplicado (não se sabe se foi aplicado; fail-closed);
          ("new", None)      cliente desconhecido, ou `txid > highest_seen` → genuinamente novo.
        O `retained_floor` é o menor txid ainda retido; abaixo dele a memória de dedup foi coletada."""
        ent = self._entry(operation_id)
        if ent is not None:
            d, s = ent
            return ("replay", s) if hmac.compare_digest(d, bytes(digest)) else ("conflict", None)
        client_id, txid = struct.unpack("<QQ", operation_id)
        c = self._clients.get(client_id)
        if c is None:
            return ("new", None)                 # cliente nunca visto
        highest_seen, _retained_floor = c
        if txid > highest_seen:
            return ("new", None)                 # acima de tudo que já se viu → novo
        return ("expired", None)                 # conhecido (<= highest_seen) sem entry viva → expirado

    @classmethod
    def try_open(cls, blob, key):
        try:
            return cls(blob, key)
        except (struct.error, IndexError, ValueError, IntegrityError, IncompatibleError):
            return None

    @property
    def n_bits(self) -> int:
        return len(self._blob) * 8


# ------------------------------ factory do conjunto base ------------------------------
_BASE_SEAL = object()   # selo privado: só open_base_artifacts o produz


@dataclass(frozen=True)
class ValidatedBaseArtifacts:
    scope_id: int
    generation_id: int
    fact_count: int
    registry: object
    bulk: object
    residual: object
    tombstone: object    # OpenResult (VALID) da camada L0/tombstone da base
    seal: object = None  # deve ser _BASE_SEAL; instância direta (seal=None) não monta bundle


class OpenBaseState(Enum):
    VALID = 1
    INVALID = 2


@dataclass(frozen=True)
class OpenBaseResult:
    state: OpenBaseState
    validated: ValidatedBaseArtifacts | None
    reason: str


def open_base_artifacts(descriptors: dict, object_store: ObjectStore, scope_id: int,
                        generation_id: int, fact_count: int, keyring: "Keyring",
                        limits: ArtifactLimits | None = None) -> OpenBaseResult:
    """Abre os 4 artefatos obrigatórios da base via `get_limited`, exige VALID e roda a validação
    cruzada. Só um resultado VALID contém `ValidatedBaseArtifacts` (com selo privado)."""
    limits = limits or ArtifactLimits()
    opened = {}
    for kind in (ArtifactKind.REGISTRY, ArtifactKind.BULK, ArtifactKind.RESIDUAL, ArtifactKind.TOMBSTONE):
        desc = descriptors.get(kind)
        if desc is None:
            return OpenBaseResult(OpenBaseState.INVALID, None, f"descriptor ausente: {kind.name}")
        if desc.kind != kind:                                  # chave do dict tem que casar o kind
            return OpenBaseResult(OpenBaseState.INVALID, None, f"{kind.name}: {KIND_MISMATCH}")
        if not desc.required:                                  # componentes da base são obrigatórios
            return OpenBaseResult(OpenBaseState.INVALID, None, f"{kind.name}: não marcado required")
        rr = object_store.get_limited(desc.sha256.hex(), limits.max_blob_bytes)
        # erro de leitura NUNCA vira "missing": só MISSING pode virar ausência (→ REQUIRED_MISSING
        # via open_artifact, respeitando required); os demais são falhas distintas do store.
        if not rr.ok and rr.reason != MISSING:
            return OpenBaseResult(OpenBaseState.INVALID, None, f"{kind.name}: STORE/{rr.reason}")
        blob = rr.blob if rr.ok else None
        oa = open_artifact(desc, blob, limits=limits, keyring=keyring)
        if oa.state != ArtifactOpenState.VALID:
            return OpenBaseResult(OpenBaseState.INVALID, None,
                                  f"{kind.name}: {oa.state.name}/{oa.reason_code}")
        opened[kind] = oa.component
    registry, bulk = opened[ArtifactKind.REGISTRY], opened[ArtifactKind.BULK]
    residual, tombstone_layer = opened[ArtifactKind.RESIDUAL], opened[ArtifactKind.TOMBSTONE]
    bv = validate_base_artifacts(registry, bulk, residual, tombstone_layer, scope_id,
                                 generation_id, fact_count)
    if not bv.valid:
        return OpenBaseResult(OpenBaseState.INVALID, None, f"cross: {bv.reason}")
    tomb_open = open_tombstone(tombstone_layer.serialize(), keyring.get(descriptors[ArtifactKind.TOMBSTONE].key_id),
                               required=True)
    validated = ValidatedBaseArtifacts(scope_id, generation_id, fact_count, registry, bulk,
                                       residual, tomb_open, _BASE_SEAL)
    return OpenBaseResult(OpenBaseState.VALID, validated, "ok")


def bundle_from_validated(v: ValidatedBaseArtifacts) -> GenerationBundle:
    """Único caminho p/ um GenerationBundle operacional: exige o selo privado de open_base_artifacts
    (uma instância construída à mão, sem selo, é recusada)."""
    if getattr(v, "seal", None) is not _BASE_SEAL:
        raise IntegrityError("ValidatedBaseArtifacts sem selo — não construído por open_base_artifacts")
    return GenerationBundle(v.scope_id, v.generation_id, v.residual, v.registry, v.tombstone, v.bulk)
