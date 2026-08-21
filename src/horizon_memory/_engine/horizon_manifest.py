# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""EpochManifest — o mapa autoritativo de UMA geração da Horizon Memory (V23-B2).

Uma geração = base selada (5 artefatos obrigatórios: REGISTRY, BULK, RESIDUAL, TOMBSTONE, DEDUP)
+ zero ou mais segmentos de WAL que a estendem. O manifesto é o ÚNICO documento que declara a
cobertura: quais bytes são a base, quais seq o WAL cobre, e como base+WAL formam UMA linha de versão
contígua. Ele é canônico (comprimento exato), autenticado (HMAC) e endereçável por conteúdo.

Leis de cobertura (validadas só a partir do manifesto, ANTES de tocar o store):
  H = base_seq (o seq que a base já incorpora)   R = read_seq (o seq confirmado da geração)
  - sem segmentos  → R == H  (a base é a geração inteira)
  - expected = H+1; para cada segmento em ordem:
        first_seq == expected;   expected += record_count
        só o ÚLTIMO pode ser ACTIVE (os anteriores SEALED)
        só o último-ACTIVE pode ter record_count == 0 (SEALED vazio é ilegal)
        durable_prefix ⊆ sealed_object; SEALED ⇒ durable == sealed
        prev_segment_digest encadeia o sealed_object anterior (primeiro = zero)
  - expected == R+1   (R é o último seq confirmado)
  - DedupTable: scope/gen batem e dedup_through_seq == H  (dedup cobre tudo que virou base)

`open_generation` compõe: parse+MAC → coverage (puro) → base (erros do store propagados distintos:
MISSING→REQUIRED_MISSING, COUNT_LIMIT→RESOURCE_LIMIT, CONCURRENT_CHANGE→CORRUPT, I/O→CORRUPT) →
redução multissegmento por UMA autoridade única (`reduce_frames` sobre a concatenação dos prefixos
duráveis, base-aware, initial_seq=H) → link do dedup → linhagem. Só um resultado VALID devolve um
`GenerationHandle` selado. Manifesto CORRENTE inválido → ABSTAIN: NUNCA abre o pai automaticamente.

V23-B3-0 (contract freeze — formato FÍSICO congelado, magic HEM2/versão 2):
  - BINDING físico: `scan()` devolve `WalHeaderInfo` e `open_generation` casa TODOS os campos do
    header autenticado do WAL (scope_id, segment_id, first_seq, key_id, previous_segment_digest,
    versão) com o `WalSegmentDescriptor` — manifesto e WAL não podem descrever cadeias diferentes.
  - IDENTIDADE ≠ POSIÇÃO: `ordinal` (posição no manifesto, == índice) separado de `segment_id`
    (identidade persistente e monotônica do arquivo, u32).
  - FOOTER é a única autoridade de selagem: SEALED sem footer → recusado; ACTIVE com footer no
    prefixo → INCOMPATIBLE.
  - Linhagem v2: `parent_manifest_digest` é SHA-256 COMPLETO (32B); genesis é EXCLUSIVAMENTE
    parent==-1 (−2/−3… inválidos); `parent < generation_id`; `generation_id`/`scope_id` u32; pai
    fornecido é parseado+autenticado e tem que bater scope e generation correspondente.

CONTRATO CONGELADO para B3-1..B3-5 (ainda NÃO implementado aqui; ver o master plan (progresso V23-B3-0, §21.11)):
  (3) o ACTIVE final deixa de ser objeto content-addressed — vira um `ActivePrefix` num `WalStore`
      localizado por `(scope_id, segment_id)` (arquivo que cresce): menor que o prefixo declarado →
      MISSING_COMMITTED_PREFIX; digest do prefixo diferente → CORRUPT; bytes após o prefixo são
      permitidos mas ignorados pela ReadView; nada após R entra no handle. (Em B3-0 o ACTIVE ainda é
      resolvido por conteúdo, transitoriamente.)
  (6) redução INCREMENTAL: `ReducerState.begin/feed_segment/finish` (reduce_frames vira wrapper) com
      limites globais (máx. segmentos, bytes de WAL, frames, bytes por segmento, tamanho do manifesto).
"""

from __future__ import annotations

import hashlib
import hmac
import struct
from dataclasses import dataclass
from enum import IntEnum

from horizon_memory._engine.horizon_artifacts import (
    DESCRIPTOR_VERSION,
    ArtifactDescriptor,
    ArtifactKind,
    ArtifactLimits,
    Keyring,
    ObjectStore,
    OpenArtifact,
    ArtifactOpenState,
    MISSING,
    COUNT_LIMIT,
    CONCURRENT_CHANGE,
    open_artifact,
)
from horizon_memory._engine.horizon_store import (
    BundleBaseView,
    GenerationBundle,
    ReadView,
    VersionedFact,
    read,
    validate_base_artifacts,
)
from horizon_memory._engine.residual_field import open_tombstone
from horizon_memory._engine.horizon_wal import (
    MAX_SEQ,
    STATE_ACTIVE,
    STATE_SEALED,
    WAL_FORMAT_VERSION,
    ReducerLimits,
    ReducerState,
    REDUCE_OK,
    REDUCE_COVERAGE,
    REDUCE_LIMIT,
    scan,
    CLEAN,
    INCOMPATIBLE as WAL_INCOMPATIBLE,
    MISSING_REQUIRED,
)
from horizon_memory._engine.horizon_walstore import ActivePrefixState, WalStoreLimits

# limites generosos por padrão; testes/produção podem apertar cada dimensão
DEFAULT_REDUCER_LIMITS = ReducerLimits(max_segments=1 << 20, max_total_frames=1 << 24,
                                       max_total_wal_bytes=1 << 34, max_frames_per_segment=1 << 24,
                                       max_segment_bytes=1 << 32)


@dataclass(frozen=True)
class GenerationLimits:
    """Orçamento UNIFICADO de abertura de uma geração (B3-5). É conferido num preflight puramente
    sobre o manifesto (declaração autenticada) ANTES de abrir qualquer objeto; a realização física
    (WalStore/scan/ReducerState) reconfere — o manifesto não elimina a conferência física."""
    max_manifest_bytes: int
    max_base_bytes: int
    max_segments: int
    max_segment_bytes: int
    max_total_wal_bytes: int
    max_frames_per_segment: int
    max_total_frames: int
    max_total_generation_bytes: int


DEFAULT_GENERATION_LIMITS = GenerationLimits(
    max_manifest_bytes=1 << 24, max_base_bytes=1 << 34, max_segments=1 << 20,
    max_segment_bytes=1 << 32, max_total_wal_bytes=1 << 34, max_frames_per_segment=1 << 24,
    max_total_frames=1 << 26, max_total_generation_bytes=1 << 35)


def _generation_preflight(man: "EpochManifest", manifest_len: int, gl: GenerationLimits) -> tuple:
    """(ok, reason). Só o manifesto: soma dos 5 artefatos, comprimentos WAL, `record_count`, limites
    por segmento/segmentos/total, com aritmética `limit - current` (nunca soma antes de checar)."""
    if manifest_len > gl.max_manifest_bytes:
        return (False, "manifest excede o limite")
    base_bytes = 0
    for d in man.base_descriptors.values():
        if d.byte_length > gl.max_base_bytes - base_bytes:
            return (False, "soma dos artefatos da base excede o limite")
        base_bytes += d.byte_length
    if len(man.segments) > gl.max_segments:
        return (False, "número de segmentos excede o limite")
    total_wal, total_frames = 0, 0
    for s in man.segments:
        if s.durable_prefix_length > gl.max_segment_bytes:
            return (False, "segmento excede o limite de bytes")
        if s.record_count > gl.max_frames_per_segment:
            return (False, "segmento excede o limite de frames")
        if s.durable_prefix_length > gl.max_total_wal_bytes - total_wal:
            return (False, "bytes totais de WAL excedem o limite")
        total_wal += s.durable_prefix_length
        if s.record_count > gl.max_total_frames - total_frames:
            return (False, "frames totais excedem o limite")
        total_frames += s.record_count
    if total_wal > gl.max_total_generation_bytes - base_bytes:
        return (False, "bytes totais da geração excedem o limite")
    return (True, "ok")

MANIFEST_MAGIC = b"HEM2"                 # V23-B3-0: formato físico v2 (congelado)
MANIFEST_FORMAT_VERSION = 2
_U32_MAX = 0xFFFFFFFF                     # generation_id/scope_id/segment_id são u32 nesta fase

# ordem canônica FIXA dos 5 artefatos da base (o manifesto não aceita outra ordem nem faltantes)
BASE_KINDS = (ArtifactKind.REGISTRY, ArtifactKind.BULK, ArtifactKind.RESIDUAL,
              ArtifactKind.TOMBSTONE, ArtifactKind.DEDUP)

_ZERO16 = b"\x00" * 16
_ZERO32 = b"\x00" * 32
TAG_BYTES = 16

# header v2: magic, ver, scope(I), gen(I,u32), fact_count(I), base_seq H(Q), read_seq R(Q),
#            parent_gen(i,i32; -1=genesis), lineage(B), n_base(B), n_segments(I), key_id(I)
_MAN_HEADER = struct.Struct("<4sHIIIQQiBBII")
_MAN_PARENT_DIGEST = struct.Struct("<32s")   # SHA-256 COMPLETO do manifesto pai (zero se genesis)
# descriptor de artefato da base: kind(B), fmt(H), required(B), byte_length(Q), sha256(32s), key_id(I)
_MAN_DESC = struct.Struct("<BHBQ32sI")
# descriptor de segmento v2: ordinal(I)=posição, segment_id(I)=identidade persistente, first_seq(Q),
#   record_count(I), status(B), durable_len(Q), durable_sha(32s), sealed_len(Q), sealed_sha(32s),
#   prev_digest(16s), key_id(I)
_MAN_SEG = struct.Struct("<IIQIBQ32sQ32s16sI")


class Lineage(IntEnum):
    GENESIS = 0        # sem geração anterior
    VERIFIED = 1       # pai fornecido e o digest confere
    UNVERIFIED = 2     # pai declarado mas não fornecido — cadeia não conferida


class OpenGenerationState(IntEnum):
    VALID = 0
    REQUIRED_MISSING = 1     # artefato obrigatório ausente
    CORRUPT = 2              # bytes inconsistentes / MAC / conflito lógico / erro de store
    INCOMPATIBLE = 3         # versão de formato não suportada
    RESOURCE_LIMIT = 4       # excede limites declarados
    COVERAGE_ERROR = 5       # a lei de cobertura foi violada


@dataclass(frozen=True)
class WalSegmentDescriptor:
    """Um segmento de WAL. Distingue POSIÇÃO de IDENTIDADE (B3-0): `ordinal` é a posição no manifesto;
    `segment_id` é a identidade persistente e monotônica do arquivo — um segmento não muda de
    identidade só porque entrou em outro manifesto. O prefixo DURÁVEL (ACKado, que a recuperação
    reduz) é separado do OBJETO SELADO endereçado por conteúdo: em SEALED coincidem; em ACTIVE o
    objeto pode ter uma cauda não-ACKada além do prefixo durável.

    NOTA de congelamento (B3-1/B3-2): o ACTIVE ainda é resolvido por conteúdo (`sealed_object_*`) de
    forma TRANSITÓRIA. A representação final do ACTIVE é um `ActivePrefix` num `WalStore` localizado
    por `(scope_id, segment_id)` (arquivo que cresce), não um objeto imutável (B3-1/B3-2)."""
    ordinal: int                    # posição no manifesto (== índice na lista)
    segment_id: int                 # identidade persistente/monotônica do arquivo (u32)
    first_seq: int
    record_count: int
    status: int                     # STATE_SEALED | STATE_ACTIVE
    durable_prefix_length: int
    durable_prefix_sha256: bytes    # 32 bytes sobre blob[:durable_prefix_length]
    sealed_object_length: int
    sealed_object_sha256: bytes     # 32 bytes sobre o objeto inteiro no store
    prev_segment_digest: bytes      # 16 bytes: encadeia sealed_object_sha256[:16] do anterior
    key_id: int


@dataclass(frozen=True)
class EpochManifest:
    scope_id: int
    generation_id: int
    fact_count: int
    base_seq: int                   # H
    read_seq: int                   # R
    parent_generation_id: int       # -1 = genesis
    parent_manifest_digest: bytes   # SHA-256 completo, 32 bytes (zero se genesis)
    base_descriptors: dict          # ArtifactKind -> ArtifactDescriptor (exatamente os 5 BASE_KINDS)
    segments: tuple                 # tuple[WalSegmentDescriptor, ...] em ordem
    key_id: int

    # ---------------- serialização canônica + MAC ----------------
    def _body(self) -> bytes:
        parts = [_MAN_HEADER.pack(
            MANIFEST_MAGIC, MANIFEST_FORMAT_VERSION, self.scope_id, self.generation_id,
            self.fact_count, self.base_seq, self.read_seq, self.parent_generation_id,
            int(self._lineage_flag()), len(BASE_KINDS), len(self.segments), self.key_id)]
        parts.append(_MAN_PARENT_DIGEST.pack(self.parent_manifest_digest))
        for kind in BASE_KINDS:
            d = self.base_descriptors[kind]
            parts.append(_MAN_DESC.pack(int(kind), d.format_version, 1 if d.required else 0,
                                        d.byte_length, d.sha256, d.key_id))
        for s in self.segments:
            parts.append(_MAN_SEG.pack(s.ordinal, s.segment_id, s.first_seq, s.record_count, s.status,
                                       s.durable_prefix_length, s.durable_prefix_sha256,
                                       s.sealed_object_length, s.sealed_object_sha256,
                                       s.prev_segment_digest, s.key_id))
        return b"".join(parts)

    def _lineage_flag(self) -> int:
        # a serialização carrega só genesis vs não-genesis; VERIFIED/UNVERIFIED é decidido na abertura
        return int(Lineage.GENESIS) if self.parent_generation_id < 0 else int(Lineage.UNVERIFIED)

    def serialize(self, key: bytes) -> bytes:
        body = self._body()
        mac = hmac.new(key, b"HORIZONEPOCH" + body, hashlib.sha256).digest()[:TAG_BYTES]
        return body + mac


def _read_seq_ok(v: int) -> bool:
    return 0 <= v < MAX_SEQ


def parse_manifest(blob: bytes | None, keyring: Keyring) -> tuple:
    """(state, manifest|None, reason). Canônico: comprimento EXATO; MAC obrigatório. Nunca lança."""
    if blob is None:
        return (OpenGenerationState.REQUIRED_MISSING, None, "manifest ausente")
    if len(blob) < _MAN_HEADER.size + _MAN_PARENT_DIGEST.size + TAG_BYTES:
        return (OpenGenerationState.CORRUPT, None, "curto demais")
    try:
        (magic, ver, scope, gen, fc, H, R, parent, lineage, n_base, n_seg, key_id) = \
            _MAN_HEADER.unpack_from(blob, 0)
    except struct.error:
        return (OpenGenerationState.CORRUPT, None, "header ilegível")
    if magic != MANIFEST_MAGIC:
        return (OpenGenerationState.CORRUPT, None, "magic")
    if ver != MANIFEST_FORMAT_VERSION:
        return (OpenGenerationState.INCOMPATIBLE, None, "versão do manifesto")
    if n_base != len(BASE_KINDS):
        return (OpenGenerationState.CORRUPT, None, "n_base")
    if n_seg < 0 or n_seg > (1 << 20):
        return (OpenGenerationState.CORRUPT, None, "n_segments")
    # comprimento canônico EXATO
    expected_len = (_MAN_HEADER.size + _MAN_PARENT_DIGEST.size + n_base * _MAN_DESC.size
                    + n_seg * _MAN_SEG.size + TAG_BYTES)
    if len(blob) != expected_len:
        return (OpenGenerationState.CORRUPT, None, "comprimento não canônico")
    key = keyring.get(key_id)
    if key is None:
        return (OpenGenerationState.INCOMPATIBLE, None, "key_id desconhecido")
    body = blob[:-TAG_BYTES]
    exp = hmac.new(key, b"HORIZONEPOCH" + body, hashlib.sha256).digest()[:TAG_BYTES]
    if not hmac.compare_digest(exp, blob[-TAG_BYTES:]):
        return (OpenGenerationState.CORRUPT, None, "MAC")
    if not (_read_seq_ok(H) and _read_seq_ok(R)):
        return (OpenGenerationState.CORRUPT, None, "seq fora de faixa")
    if not (0 <= gen <= _U32_MAX and 0 <= scope <= _U32_MAX):
        return (OpenGenerationState.CORRUPT, None, "scope/generation fora de u32")
    # linhagem v2: genesis é EXCLUSIVAMENTE parent==-1; -2/-3/... são inválidos; senão pai < filho
    if parent < -1:
        return (OpenGenerationState.CORRUPT, None, "parent_generation_id inválido (< -1)")
    if parent >= 0 and not (parent < gen):
        return (OpenGenerationState.CORRUPT, None, "parent_generation_id >= generation_id")
    off = _MAN_HEADER.size
    (parent_digest,) = _MAN_PARENT_DIGEST.unpack_from(blob, off); off += _MAN_PARENT_DIGEST.size
    if parent < 0 and parent_digest != _ZERO32:
        return (OpenGenerationState.CORRUPT, None, "genesis com digest de pai")
    if parent >= 0 and parent_digest == _ZERO32:
        return (OpenGenerationState.CORRUPT, None, "pai sem digest")
    base_desc = {}
    for _ in range(n_base):
        k, fmt, req, blen, sha, kid = _MAN_DESC.unpack_from(blob, off); off += _MAN_DESC.size
        try:
            kind = ArtifactKind(k)
        except ValueError:
            return (OpenGenerationState.CORRUPT, None, "kind desconhecido")
        if kind in base_desc:
            return (OpenGenerationState.CORRUPT, None, "kind duplicado")
        base_desc[kind] = ArtifactDescriptor(kind, fmt, bool(req), blen, sha, kid)
    if set(base_desc) != set(BASE_KINDS):
        return (OpenGenerationState.CORRUPT, None, "conjunto de base incompleto")
    segments = []
    for i in range(n_seg):
        (ordn, sid, fs, rc, st, dl, dsha, sl, ssha, prev, kid) = _MAN_SEG.unpack_from(blob, off)
        off += _MAN_SEG.size
        segments.append(WalSegmentDescriptor(ordn, sid, fs, rc, st, dl, dsha, sl, ssha, prev, kid))
    man = EpochManifest(scope, gen, fc, H, R, parent, parent_digest, base_desc,
                        tuple(segments), key_id)
    return (OpenGenerationState.VALID, man, "ok")


def check_coverage(man: EpochManifest) -> tuple:
    """Valida a lei de cobertura SÓ a partir do manifesto (puro). (ok, reason)."""
    H, R = man.base_seq, man.read_seq
    if R < H:
        return (False, "R < H")
    if not man.segments:
        return (True, "sem WAL") if R == H else (False, "sem WAL mas R != H")
    expected = H + 1
    n = len(man.segments)
    prev_segment_id = -1
    for i, s in enumerate(man.segments):
        if s.ordinal != i:
            return (False, f"ordinal fora de ordem em {i}")
        if not (0 <= s.segment_id <= _U32_MAX):
            return (False, f"segment_id fora de u32 em {i}")
        # identidade PERSISTENTE e monotônica: a posição não é a identidade
        if s.segment_id <= prev_segment_id:
            return (False, f"segment_id não-monotônico em {i}")
        prev_segment_id = s.segment_id
        if s.status not in (STATE_ACTIVE, STATE_SEALED):
            return (False, f"status inválido em {i}")
        if s.record_count < 0:
            return (False, f"record_count negativo em {i}")
        if s.first_seq != expected:
            return (False, f"gap/overlap em {i}: first_seq={s.first_seq} != {expected}")
        # só o último pode ser ACTIVE
        is_last = (i == n - 1)
        if s.status == STATE_ACTIVE and not is_last:
            return (False, f"ACTIVE não-final em {i}")
        if s.status == STATE_SEALED and is_last and R > H and s.record_count == 0:
            return (False, "último SEALED vazio")
        # só o último-ACTIVE pode ter 0 registros
        if s.record_count == 0 and not (is_last and s.status == STATE_ACTIVE):
            return (False, f"segmento vazio ilegal em {i}")
        # codificação canônica ACTIVE/SEALED (HEM2 congelado — sem exigir HEM3):
        #   SEALED: durable == sealed, sealed_object_* obrigatórios (endereçado por conteúdo)
        #   ACTIVE: sealed_object vazio (length==0, sha==zero32); localização derivada de
        #           (scope_id, segment_id) — NUNCA content-addressed
        if s.durable_prefix_length <= 0:
            return (False, f"durable_prefix vazio em {i}")
        if s.status == STATE_SEALED:
            if (s.sealed_object_length != s.durable_prefix_length
                    or not hmac.compare_digest(s.sealed_object_sha256, s.durable_prefix_sha256)
                    or s.sealed_object_sha256 == _ZERO32):
                return (False, f"SEALED não canônico em {i}")
        else:  # ACTIVE
            if s.sealed_object_length != 0 or s.sealed_object_sha256 != _ZERO32:
                return (False, f"ACTIVE não canônico em {i} (sealed_object deve ser vazio)")
        # encadeamento
        prev_expected = _ZERO16 if i == 0 else man.segments[i - 1].sealed_object_sha256[:16]
        if not hmac.compare_digest(s.prev_segment_digest, prev_expected):
            return (False, f"cadeia quebrada em {i}")
        expected += s.record_count
        if expected >= MAX_SEQ:
            return (False, "overflow de seq")
    if expected != R + 1:
        return (False, f"soma de cobertura {expected - 1} != R={R}")
    return (True, "ok")


@dataclass(frozen=True)
class GenerationHandle:
    """Handle SELADO de uma geração aberta e validada. Único caminho para `read`: só
    `open_generation` cria o selo (uma instância à mão sem selo é recusada)."""
    scope_id: int
    generation_id: int
    fact_count: int
    base_seq: int
    read_seq: int
    lineage: Lineage
    bundle: GenerationBundle
    l0: object                      # WalIndex reduzido (base+WAL) até read_seq
    txindex: object
    dedup: object
    _seal: object

    def read_fact(self, fact_id: int):
        if self._seal is not _GEN_SEAL:
            raise ValueError("GenerationHandle sem selo")
        view = ReadView(self.generation_id, self.scope_id, self.base_seq, self.read_seq)
        return read(view, self.bundle, self.l0, fact_id)


_GEN_SEAL = object()


@dataclass(frozen=True)
class OpenGenerationResult:
    state: OpenGenerationState
    handle: GenerationHandle | None
    reason: str


def _fail(state, reason):
    return OpenGenerationResult(state, None, reason)


def _open_base_artifact(desc, store, limits, keyring):
    """Abre 1 artefato da base propagando erros de store DISTINTOS (nunca vira 'missing')."""
    rr = store.get_limited(desc.sha256.hex(), limits.max_blob_bytes)
    if not rr.ok and rr.reason != MISSING:
        if rr.reason == COUNT_LIMIT:
            return (OpenGenerationState.RESOURCE_LIMIT, None, f"store/{rr.reason}")
        # CONCURRENT_CHANGE / I/O → tratados como corrupção (nunca ausência)
        return (OpenGenerationState.CORRUPT, None, f"store/{rr.reason}")
    blob = rr.blob if rr.ok else None
    oa = open_artifact(desc, blob, limits=limits, keyring=keyring)
    if oa.state == ArtifactOpenState.VALID:
        return (OpenGenerationState.VALID, oa.component, "ok")
    _MAP = {
        ArtifactOpenState.REQUIRED_MISSING: OpenGenerationState.REQUIRED_MISSING,
        ArtifactOpenState.ABSENT_ALLOWED: OpenGenerationState.REQUIRED_MISSING,  # base é obrigatória
        ArtifactOpenState.CORRUPT: OpenGenerationState.CORRUPT,
        ArtifactOpenState.INCOMPATIBLE: OpenGenerationState.INCOMPATIBLE,
        ArtifactOpenState.RESOURCE_LIMIT: OpenGenerationState.RESOURCE_LIMIT,
    }
    return (_MAP.get(oa.state, OpenGenerationState.CORRUPT), None, f"{desc.kind.name}/{oa.reason_code}")


def _wal_limits(limits: ArtifactLimits) -> WalStoreLimits:
    return WalStoreLimits(max_active_bytes=limits.max_blob_bytes, max_sealed_bytes=limits.max_blob_bytes)


# mapas de estado: WalStore/reducer → estado tipado da geração
_SEGMENT_STATE_MAP = {
    ActivePrefixState.MISSING: OpenGenerationState.REQUIRED_MISSING,
    ActivePrefixState.MISSING_COMMITTED_PREFIX: OpenGenerationState.CORRUPT,
    ActivePrefixState.RESOURCE_LIMIT: OpenGenerationState.RESOURCE_LIMIT,
    ActivePrefixState.INCOMPATIBLE: OpenGenerationState.INCOMPATIBLE,
}
_REDUCE_STATE_MAP = {
    REDUCE_COVERAGE: OpenGenerationState.COVERAGE_ERROR,
    REDUCE_LIMIT: OpenGenerationState.RESOURCE_LIMIT,
}


def open_generation(manifest_blob: bytes | None, object_store, wal_store,
                    keyring: Keyring, limits: ArtifactLimits | None = None,
                    parent_manifest_blob: bytes | None = None,
                    reducer_limits: ReducerLimits | None = None,
                    gen_limits: GenerationLimits | None = None) -> OpenGenerationResult:
    """Abre UMA geração a partir do manifesto. NÃO abre o pai automaticamente (linhagem só é
    conferida se `parent_manifest_blob` for fornecido — senão UNVERIFIED)."""
    limits = limits or ArtifactLimits()
    gen_limits = gen_limits or DEFAULT_GENERATION_LIMITS
    # o orçamento da geração deriva os limites do reducer e do WalStore, salvo override explícito
    reducer_limits = reducer_limits or ReducerLimits(
        gen_limits.max_segments, gen_limits.max_total_frames, gen_limits.max_total_wal_bytes,
        gen_limits.max_frames_per_segment, gen_limits.max_segment_bytes)
    wal_store = wal_store or object_store

    state, man, reason = parse_manifest(manifest_blob, keyring)
    if state != OpenGenerationState.VALID:
        return _fail(state, reason)

    ok, cov = check_coverage(man)
    if not ok:
        return _fail(OpenGenerationState.COVERAGE_ERROR, cov)

    # preflight de recursos SÓ sobre o manifesto (declaração autenticada) ANTES de abrir qualquer
    # objeto; a realização física reconfere cada limite.
    okr, whyr = _generation_preflight(man, len(manifest_blob), gen_limits)
    if not okr:
        return _fail(OpenGenerationState.RESOURCE_LIMIT, whyr)

    # todos os descriptors da base têm que ter a versão de formato correta e ser obrigatórios
    for kind in BASE_KINDS:
        d = man.base_descriptors[kind]
        if not d.required:
            return _fail(OpenGenerationState.COVERAGE_ERROR, f"{kind.name} não obrigatório")
        if d.format_version != DESCRIPTOR_VERSION[kind]:
            return _fail(OpenGenerationState.INCOMPATIBLE, f"{kind.name} versão de formato")

    # base (erros do store propagados distintos)
    opened = {}
    for kind in BASE_KINDS:
        st, comp, why = _open_base_artifact(man.base_descriptors[kind], object_store, limits, keyring)
        if st != OpenGenerationState.VALID:
            return _fail(st, why)
        opened[kind] = comp

    registry = opened[ArtifactKind.REGISTRY]
    bulk = opened[ArtifactKind.BULK]
    residual = opened[ArtifactKind.RESIDUAL]
    tombstone_layer = opened[ArtifactKind.TOMBSTONE]
    dedup = opened[ArtifactKind.DEDUP]

    bv = validate_base_artifacts(registry, bulk, residual, tombstone_layer,
                                 man.scope_id, man.generation_id, man.fact_count)
    if not bv.valid:
        return _fail(OpenGenerationState.CORRUPT, f"base incoerente: {bv.reason}")

    # link do dedup com a geração e a fronteira da base (H)
    if dedup.scope_id != man.scope_id or dedup.generation_id != man.generation_id:
        return _fail(OpenGenerationState.COVERAGE_ERROR, "dedup: escopo/geração")
    if dedup.dedup_through_seq != man.base_seq:
        return _fail(OpenGenerationState.COVERAGE_ERROR,
                     f"dedup_through={dedup.dedup_through_seq} != H={man.base_seq}")

    # bundle da base + view base-aware p/ o reducer
    tomb_key = keyring.get(man.base_descriptors[ArtifactKind.TOMBSTONE].key_id)
    tomb_open = open_tombstone(tombstone_layer.serialize(), tomb_key, required=True)
    bundle = GenerationBundle(man.scope_id, man.generation_id, residual, registry,
                              tomb_open, bulk)
    base_view = BundleBaseView(ReadView(man.generation_id, man.scope_id, man.base_seq, man.read_seq),
                               bundle, base_dedup=dedup)   # B4.2: replay consulta a dedup da base (colisão fail-closed)

    # redução INCREMENTAL por UMA autoridade (ReducerState): cada segmento é lido do WalStore como um
    # token Validated* SELADO, tem o header ligado ao descriptor, é alimentado ao reducer e a
    # referência liberada. `all_frames` deixou de existir — o pico de memória é do maior segmento.
    reducer = ReducerState.begin(base=base_view, initial_seq=man.base_seq, target_seq=man.read_seq,
                                 limits=reducer_limits)
    # a realização física reconfere o limite por segmento (o manifesto é só uma declaração)
    wlim = WalStoreLimits(max_active_bytes=gen_limits.max_segment_bytes,
                          max_sealed_bytes=gen_limits.max_segment_bytes)
    for s in man.segments:
        tag = f"segmento ord={s.ordinal} sid={s.segment_id}"
        if keyring.get(s.key_id) is None:
            return _fail(OpenGenerationState.INCOMPATIBLE, f"{tag}: key_id")
        vs = wal_store.read_validated_segment(s, man.scope_id, wlim)
        if vs.state != ActivePrefixState.VALID:
            return _fail(_SEGMENT_STATE_MAP.get(vs.state, OpenGenerationState.CORRUPT),
                         f"{tag}: {vs.reason}")
        seg = vs.segment
        h = seg.header
        # BINDING FÍSICO (B3-0): o header autenticado do WAL casa TODOS os campos do descriptor.
        if h is None or h.format_version != WAL_FORMAT_VERSION:
            return _fail(OpenGenerationState.INCOMPATIBLE, f"{tag}: WAL versão")
        if (h.scope_id != man.scope_id or h.segment_id != s.segment_id or h.first_seq != s.first_seq
                or h.key_id != s.key_id
                or not hmac.compare_digest(h.previous_segment_digest, s.prev_segment_digest)):
            return _fail(OpenGenerationState.COVERAGE_ERROR, f"{tag}: header diverge do descriptor")
        step = reducer.feed_segment(seg, s)
        if not step.ok:
            return _fail(_REDUCE_STATE_MAP.get(step.classification, OpenGenerationState.CORRUPT),
                         f"{tag}: {step.reason}")
        del seg, vs                       # libera a referência ao segmento antes do próximo

    rp = reducer.finish()
    if rp.classification != REDUCE_OK:
        return _fail(_REDUCE_STATE_MAP.get(rp.classification, OpenGenerationState.CORRUPT),
                     f"replay: {rp.classification}")
    if rp.applied_seq != man.read_seq:    # garantido por finish (target=R); defensivo
        return _fail(OpenGenerationState.COVERAGE_ERROR,
                     f"applied_seq={rp.applied_seq} != R={man.read_seq}")

    # linhagem v2: genesis, ou CONFERIDA contra o pai fornecido (parseado+autenticado+SHA-256 cheio+
    # mesmo scope e generation correspondente), ou não conferida. Nunca abre o pai por conta própria.
    if man.parent_generation_id < 0:
        lineage = Lineage.GENESIS
    elif parent_manifest_blob is not None:
        p_state, p_man, _ = parse_manifest(parent_manifest_blob, keyring)
        if p_state != OpenGenerationState.VALID:
            return _fail(OpenGenerationState.CORRUPT, "manifesto pai ilegível/não autenticado")
        if not hmac.compare_digest(hashlib.sha256(parent_manifest_blob).digest(),
                                   man.parent_manifest_digest):
            return _fail(OpenGenerationState.CORRUPT, "SHA-256 do pai não confere")
        if p_man.scope_id != man.scope_id or p_man.generation_id != man.parent_generation_id:
            return _fail(OpenGenerationState.CORRUPT, "pai com scope/generation incompatível")
        lineage = Lineage.VERIFIED
    else:
        lineage = Lineage.UNVERIFIED

    handle = GenerationHandle(man.scope_id, man.generation_id, man.fact_count, man.base_seq,
                              man.read_seq, lineage, bundle, rp.index, rp.txindex, dedup, _GEN_SEAL)
    return OpenGenerationResult(OpenGenerationState.VALID, handle, "ok")


# ---------------- política da geração CORRENTE ----------------
@dataclass(frozen=True)
class CurrentDecision:
    opened: bool
    handle: GenerationHandle | None
    state: OpenGenerationState
    abstained: bool
    reason: str


def resolve_current(manifest_blob, object_store, wal_store, keyring, limits=None,
                    parent_manifest_blob=None) -> CurrentDecision:
    """Abre a geração CORRENTE. Se o manifesto corrente for inválido por QUALQUER motivo, ABSTAIN —
    NUNCA abre o pai automaticamente (isso resolveria uma versão antiga como se fosse a atual)."""
    r = open_generation(manifest_blob, object_store, wal_store, keyring, limits, parent_manifest_blob)
    if r.state == OpenGenerationState.VALID:
        return CurrentDecision(True, r.handle, r.state, False, "ok")
    return CurrentDecision(False, None, r.state, True, f"ABSTAIN: {r.reason}")
