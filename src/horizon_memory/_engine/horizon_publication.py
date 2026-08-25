# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""CURRENT + PublicationRecord (V23-C1): a camada de nomeação da geração publicada.

Não se guarda o manifesto diretamente no `CURRENT`. Três níveis, todos autenticados e (os dois
primeiros) endereçados por conteúdo:

    Manifest object      imutável, por SHA-256 (o próprio blob do EpochManifest no ObjectStore)
    PublicationRecord    imutável, por SHA-256 — liga geração/revisão/R ao manifesto e ao anterior
    CURRENT              ponteiro FIXO e pequeno para o PublicationRecord (comprimento fixo + MAC)

`revision` (u64) distingue vários manifestos da MESMA `generation_id` e impede ABA acidental entre
publishers concorrentes: uma publicação só troca o CURRENT via CAS sobre o digest esperado (C2).

`CURRENT` inválido = ABSTAIN (nunca fallback para pai nem "maior generation encontrada"). Sem uma
âncora monotônica externa, isto não resiste a replay adversarial de um CURRENT antigo VÁLIDO — limite
honesto já congelado.
"""

from __future__ import annotations

import errno
import hashlib
import hmac
import os
import stat
import struct
from dataclasses import dataclass, replace
from enum import IntEnum
from pathlib import Path

from horizon_memory._engine.horizon_artifacts import ArtifactLimits
from horizon_memory._engine import file_lock as fcntl
from horizon_memory._engine.horizon_durability import (
    durable_replace,
    ensure_durable_directory_chain,
    fsync_dir,
    open_hardened_lock,
)
from horizon_memory._engine.horizon_manifest import (
    DEFAULT_GENERATION_LIMITS,
    GenerationLimits,
    OpenGenerationState,
    _generation_preflight,
    check_coverage,
    open_generation,
    parse_manifest,
)
from horizon_memory._engine.horizon_wal import (
    APPLIED, MAX_SEQ, SEAL_FOOTER_BYTES, STATE_ACTIVE, STATE_SEALED)

_PR_MAGIC = b"HPR1"
_PR_VERSION = 1
# scope(I), gen(I), revision(Q), read_seq(Q), manifest_sha256(32s), prev_pub_sha256(32s), key_id(I)
_PR = struct.Struct("<4sHIIQQ32s32sI")
_PR_TAG = 16

_CUR_MAGIC = b"HCUR"
_CUR_VERSION = 1
_CUR = struct.Struct("<4sH32sI")     # magic, ver, publication_sha256(32s), key_id(I)
_CUR_TAG = 16
CURRENT_LEN = _CUR.size + _CUR_TAG   # comprimento FIXO do ponteiro CURRENT
_U32_MAX = 0xFFFFFFFF
_ZERO32 = b"\x00" * 32

CURRENT_NAME = "CURRENT"
_MAX_CURRENT_BYTES = 4096            # o CURRENT é minúsculo; recusa qualquer coisa maior


class PublicationState(IntEnum):
    VALID = 0
    MISSING = 1
    CORRUPT = 2
    INCOMPATIBLE = 3
    RESOURCE_LIMIT = 4


@dataclass(frozen=True)
class PublicationRecord:
    scope_id: int
    generation_id: int
    revision: int
    read_seq: int
    manifest_sha256: bytes            # 32 bytes
    previous_publication_sha256: bytes  # 32 bytes (zero na primeira publicação)
    key_id: int

    def serialize(self, key: bytes) -> bytes:
        body = _PR.pack(_PR_MAGIC, _PR_VERSION, self.scope_id, self.generation_id, self.revision,
                        self.read_seq, self.manifest_sha256, self.previous_publication_sha256,
                        self.key_id)
        mac = hmac.new(key, b"HORIZONPUB" + body, hashlib.sha256).digest()[:_PR_TAG]
        return body + mac


def parse_publication_record(blob: bytes | None, keyring) -> tuple:
    """(state, record|None, reason). Comprimento EXATO + MAC obrigatório. Nunca lança."""
    if blob is None:
        return (PublicationState.MISSING, None, "ausente")
    if len(blob) != _PR.size + _PR_TAG:
        return (PublicationState.CORRUPT, None, "comprimento não canônico")
    magic, ver, scope, gen, rev, R, msha, psha, key_id = _PR.unpack_from(blob, 0)
    if magic != _PR_MAGIC:
        return (PublicationState.CORRUPT, None, "magic")
    if ver != _PR_VERSION:
        return (PublicationState.INCOMPATIBLE, None, "versão")
    key = keyring.get(key_id)
    if key is None:
        return (PublicationState.INCOMPATIBLE, None, "key_id desconhecido")
    exp = hmac.new(key, b"HORIZONPUB" + blob[:-_PR_TAG], hashlib.sha256).digest()[:_PR_TAG]
    if not hmac.compare_digest(exp, blob[-_PR_TAG:]):
        return (PublicationState.CORRUPT, None, "MAC")
    if scope > _U32_MAX or gen > _U32_MAX:
        return (PublicationState.CORRUPT, None, "scope/generation fora de u32")
    # invariantes canônicas (o parser é a autoridade — nunca aceita um record incoerente)
    if msha == _ZERO32:
        return (PublicationState.CORRUPT, None, "manifest_sha256 zero")
    if (rev == 0) != (psha == _ZERO32):
        return (PublicationState.CORRUPT, None, "revision==0 ⇔ previous==zero32 violado")
    if not (0 <= R < MAX_SEQ):
        return (PublicationState.CORRUPT, None, "read_seq fora de faixa")
    rec = PublicationRecord(scope, gen, rev, R, bytes(msha), bytes(psha), key_id)
    return (PublicationState.VALID, rec, "ok")


def serialize_current(publication_sha256: bytes, key_id: int, key: bytes) -> bytes:
    if len(publication_sha256) != 32:
        raise ValueError("publication_sha256 precisa de 32 bytes")
    body = _CUR.pack(_CUR_MAGIC, _CUR_VERSION, publication_sha256, key_id)
    mac = hmac.new(key, b"HORIZONCURRENT" + body, hashlib.sha256).digest()[:_CUR_TAG]
    return body + mac


@dataclass(frozen=True)
class CurrentPointer:
    """Identidade COMPLETA do CURRENT (C4.0.1): além do `publication_sha256`, o `key_id` autenticado.
    O CAS compara `publication_sha256`; o cursor/publicação também conferem `key_id == record.key_id`,
    provando que a chave que assinou o ponteiro é a mesma que assinou o PublicationRecord apontado."""
    publication_sha256: bytes
    key_id: int


def parse_current(blob: bytes | None, keyring) -> tuple:
    """(state, CurrentPointer|None, reason). Comprimento FIXO + MAC. Nunca lança."""
    if blob is None:
        return (PublicationState.MISSING, None, "ausente")
    if len(blob) != CURRENT_LEN:
        return (PublicationState.CORRUPT, None, "comprimento fixo violado")
    magic, ver, psha, key_id = _CUR.unpack_from(blob, 0)
    if magic != _CUR_MAGIC:
        return (PublicationState.CORRUPT, None, "magic")
    if ver != _CUR_VERSION:
        return (PublicationState.INCOMPATIBLE, None, "versão")
    key = keyring.get(key_id)
    if key is None:
        return (PublicationState.INCOMPATIBLE, None, "key_id desconhecido")
    exp = hmac.new(key, b"HORIZONCURRENT" + blob[:-_CUR_TAG], hashlib.sha256).digest()[:_CUR_TAG]
    if not hmac.compare_digest(exp, blob[-_CUR_TAG:]):
        return (PublicationState.CORRUPT, None, "MAC")
    return (PublicationState.VALID, CurrentPointer(bytes(psha), key_id), "ok")


# ---------------- IO do ponteiro CURRENT (durável) ----------------
def _pread_exact(fd: int, n: int) -> bytes:
    data = bytearray()
    off = 0
    while off < n:
        try:
            chunk = os.pread(fd, n - off, off)
        except InterruptedError:          # EINTR — reintenta
            continue
        if not chunk:
            break
        data += chunk
        off += len(chunk)
    return bytes(data)


def _write_all(fd: int, data: bytes) -> None:
    mv, n = memoryview(data), 0
    while n < len(data):
        try:
            w = os.write(fd, mv[n:])
        except InterruptedError:          # EINTR
            continue
        if w <= 0:
            raise OSError("write incompleto do CURRENT")
        n += w


def read_current(directory, keyring, max_bytes: int = _MAX_CURRENT_BYTES) -> tuple:
    """Lê `CURRENT` com LIMITE antes de alocar; `S_ISREG`, `O_NOFOLLOW`, `pread` exato (EINTR/leitura
    curta não viram falso CORRUPT). (state, CurrentPointer|None, reason) — C4.0.1."""
    path = Path(directory) / CURRENT_NAME
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(str(path), flags)
    except FileNotFoundError:
        return (PublicationState.MISSING, None, "CURRENT ausente")
    except OSError as e:
        if e.errno == errno.ELOOP:
            return (PublicationState.CORRUPT, None, "symlink recusado")
        return (PublicationState.CORRUPT, None, f"open errno {e.errno}")
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return (PublicationState.CORRUPT, None, "não é arquivo regular")
        if st.st_size > max_bytes:                       # limite ANTES de ler/alocar
            return (PublicationState.RESOURCE_LIMIT, None, "CURRENT grande demais")
        data = _pread_exact(fd, st.st_size)
        if len(data) != st.st_size:
            return (PublicationState.CORRUPT, None, "leitura curta")
    finally:
        os.close(fd)
    return parse_current(data, keyring)


def write_current(directory, current_blob: bytes, *, best_effort: bool = False) -> None:
    """Escreve o ponteiro CURRENT de forma DURÁVEL e endurecida (C1.1): valida o blob; temp com nome
    ALEATÓRIO e `O_CREAT|O_EXCL|O_NOFOLLOW` modo 0600 (nunca reusa/segue um preexistente); `write_all`
    com EINTR; `fsync`; rename atômico; `fsync` do diretório; remove o temp em qualquer falha."""
    if len(current_blob) != CURRENT_LEN:                 # valida ANTES de gravar
        raise ValueError("CURRENT blob com comprimento inválido")
    directory = Path(directory)
    final = directory / CURRENT_NAME
    tmp = directory / f"{CURRENT_NAME}.tmp.{os.urandom(8).hex()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(tmp), flags, 0o600)
    try:
        _write_all(fd, current_blob)
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        _silent_unlink(tmp)
        raise
    else:
        os.close(fd)
    try:
        durable_replace(tmp, final, best_effort=best_effort)   # rename atômico + fsync do diretório
    except BaseException:
        _silent_unlink(tmp)          # se o rename não aconteceu, não deixa temp órfão
        raise


def _silent_unlink(path) -> None:
    try:
        os.unlink(str(path))
    except FileNotFoundError:
        pass


# ================= C2: publicação por CAS =================
_MAX_RECORD_BYTES = 4096
_PROOF_SEAL = object()             # selo privado da prova (consumida em-processo pela ativação do C3)
PROOF_SEAL = _PROOF_SEAL           # a ativação (C3) confere este selo — não é serializável/forjável


@dataclass(frozen=True)
class PublicationLimits:
    artifact: ArtifactLimits
    generation: GenerationLimits
    max_current_bytes: int = _MAX_CURRENT_BYTES
    max_record_bytes: int = _MAX_RECORD_BYTES


DEFAULT_PUBLICATION_LIMITS = PublicationLimits(ArtifactLimits(), DEFAULT_GENERATION_LIMITS)


class PublicationStore:
    """Diretório de publicação por scope, inicializado de forma DURÁVEL (o nome do diretório precisa
    sobreviver à perda de energia — `mkdir` sozinho não basta). `publish_generation` opera sobre um
    store inicializado."""

    def __init__(self, root, scope_id: int):
        self.root = Path(root)
        self.scope_id = scope_id
        self.directory = self.root / "scopes" / f"{scope_id:08x}"

    def initialize(self, *, best_effort: bool = False) -> "PublicationStore":
        ensure_durable_directory_chain(self.directory, self.root, best_effort=best_effort)
        return self


def _resolve_directory(store_or_dir) -> Path:
    if isinstance(store_or_dir, PublicationStore):
        return store_or_dir.directory
    return Path(store_or_dir)


class PublishState(IntEnum):
    PUBLISHED = 0
    ALREADY_PUBLISHED = 1          # CURRENT já aponta para exatamente esta publicação (retry idempotente)
    STALE_CURRENT = 2              # o CURRENT observado != expected → outro publisher venceu
    INVALID_CANDIDATE = 3          # o manifesto candidato não abre (open_generation != VALID)
    INVALID_SEMANTICS = 4          # read_seq/scope/generation/manifest incoerentes
    ABSTAIN = 5                    # CURRENT ilegível — jamais sobrescrever como "reparo"
    IO_ERROR = 6
    INVALID_AUTHORITY = 7          # lease ausente/errado para o purpose (C4.1)
    INVALID_TRANSITION = 8         # a transição prev→candidato viola o purpose (C4.1)
    PURPOSE_NOT_ENABLED = 9        # purpose ainda não habilitado (MAINTENANCE até o B4) (C4.1)
    INVALID_PURPOSE = 10           # API pública chamada sem purpose (fail-closed, C4.1.1)
    NO_PUBLICATION_REQUIRED = 11   # batch sem APPLIED: nada a publicar (C4.2)
    INVALID_INTENT = 12            # BatchPublicationIntent inválido/não-selado ou ≠ cursor (C4.2)
    INVALID_DURABILITY = 13        # DurableBatchProof ausente/não-selado ou ≠ intent/arquivo (C4.2)
    RESOURCE_LIMIT = 14            # overflow de seq / limite tipado (C4.2)


class PublicationPurpose(IntEnum):
    """Intenção de uma publicação (C4.1): define a AUTORIDADE exigida e a TRANSIÇÃO permitida sobre o
    manifesto anterior. Não é serializável — é um parâmetro do ato de publicar."""
    GENESIS = 0                    # 1ª publicação do scope: CURRENT ausente, sem lease
    BATCH = 1                      # avanço do MESMO ACTIVE: lease OWNED_BY_STORE|OWNED_BY_NEW_STORE
    ROTATION = 2                   # ACTIVE→SEALED + próximo ACTIVE: lease OWNED_BY_PREPARED
    MAINTENANCE = 3                # compaction/base (B4.1): lease OWNED_BY_COMPACTION_PREPARED


class TransitionState(IntEnum):
    OK = 0
    INVALID = 1                    # a forma prev→candidato não corresponde ao purpose
    NOT_ENABLED = 2                # purpose ainda não habilitado (MAINTENANCE)


@dataclass(frozen=True)
class TransitionResult:
    state: TransitionState
    reason: str


_ZERO16 = b"\x00" * 16

# identidade IMUTÁVEL da geração — nenhuma publicação de BATCH/ROTATION pode mudá-la
_GENERATION_IDENTITY = ("scope_id", "generation_id", "fact_count", "base_seq",
                        "parent_generation_id", "parent_manifest_digest", "base_descriptors", "key_id")


def _same_generation_identity(prev, cand) -> bool:
    return all(getattr(prev, f) == getattr(cand, f) for f in _GENERATION_IDENTITY)


def _validate_batch(prev, cand) -> TransitionResult:
    old_active, cand_active = prev.segments[-1], cand.segments[-1]
    if old_active.status != STATE_ACTIVE or cand_active.status != STATE_ACTIVE:
        return TransitionResult(TransitionState.INVALID, "BATCH exige ACTIVE final em ambos")
    if cand.read_seq <= prev.read_seq:
        return TransitionResult(TransitionState.INVALID, "BATCH não avançou R")
    # forma esperada por `replace`: SÓ R e (record_count, prefix_length, prefix_sha256) do ACTIVE mudam;
    # qualquer outro campo (base, parent, SEALED anteriores, identidade do ACTIVE, campos futuros) que
    # divergir faz `cand != expected` — recusado por padrão, nunca ignorado em silêncio.
    expected_active = replace(old_active, record_count=cand_active.record_count,
                              durable_prefix_length=cand_active.durable_prefix_length,
                              durable_prefix_sha256=cand_active.durable_prefix_sha256)
    expected = replace(prev, read_seq=cand.read_seq,
                       segments=prev.segments[:-1] + (expected_active,))
    if cand != expected:
        return TransitionResult(TransitionState.INVALID, "candidato != avanço de BATCH esperado")
    # semântica não capturada pela igualdade estrutural
    if cand_active.durable_prefix_length < old_active.durable_prefix_length:
        return TransitionResult(TransitionState.INVALID, "comprimento durável diminuiu")
    if cand_active.record_count < old_active.record_count:
        return TransitionResult(TransitionState.INVALID, "record_count diminuiu")
    if cand_active.record_count != cand.read_seq - cand_active.first_seq + 1:
        return TransitionResult(TransitionState.INVALID, "record_count != R - first_seq + 1")
    if cand_active.sealed_object_length != 0 or cand_active.sealed_object_sha256 != _ZERO32:
        return TransitionResult(TransitionState.INVALID, "ACTIVE com sealed_object não vazio")
    return TransitionResult(TransitionState.OK, "batch")


def _validate_rotation(prev, cand) -> TransitionResult:
    """Forma ESTRUTURAL de ROTATION derivável puramente. O objeto SEALED é o prefixo ACTIVE anterior
    MAIS o footer autenticado (`SEAL_FOOTER_BYTES`), então `SHA(SEALED) != SHA(prefixo)` e a comparação
    pura NÃO consegue conferir o `sealed_object_sha256` real — só a SHAPE (identidade/comprimento/cadeia
    canônica). O binding físico do SHA (prefixo real + footer, `last_seq == R`) é do `publish_rotation`,
    que exige o `RotationPrepared` selado cujo `old_sealed_descriptor` já carrega o SHA provado."""
    old_active = prev.segments[-1]
    if old_active.status != STATE_ACTIVE:
        return TransitionResult(TransitionState.INVALID, "anterior sem ACTIVE final")
    if cand.read_seq != prev.read_seq:
        return TransitionResult(TransitionState.INVALID, "ROTATION mudou R")
    if len(cand.segments) != len(prev.segments) + 1:
        return TransitionResult(TransitionState.INVALID, "rotação não adicionou exatamente 1 segmento")
    n = len(prev.segments)
    # todos os SEALED anteriores ao ACTIVE antigo permanecem byte-idênticos
    if cand.segments[:n - 1] != prev.segments[:n - 1]:
        return TransitionResult(TransitionState.INVALID, "SEALED anteriores mudaram")
    # o ACTIVE antigo vira SEALED: MESMA identidade/ordinal/first_seq/count/cadeia/key, canônico
    # (durable == sealed, sha != zero) e comprimento == prefixo do ACTIVE + footer de selagem
    new_sealed = cand.segments[n - 1]
    if new_sealed.status != STATE_SEALED:
        return TransitionResult(TransitionState.INVALID, "posição do ACTIVE antigo não é SEALED")
    if (new_sealed.ordinal != old_active.ordinal or new_sealed.segment_id != old_active.segment_id
            or new_sealed.first_seq != old_active.first_seq
            or new_sealed.record_count != old_active.record_count
            or not hmac.compare_digest(new_sealed.prev_segment_digest, old_active.prev_segment_digest)
            or new_sealed.key_id != old_active.key_id):
        return TransitionResult(TransitionState.INVALID, "SEALED não conserva a identidade do ACTIVE")
    if (new_sealed.sealed_object_length != new_sealed.durable_prefix_length
            or not hmac.compare_digest(new_sealed.sealed_object_sha256, new_sealed.durable_prefix_sha256)
            or new_sealed.sealed_object_sha256 == _ZERO32):
        return TransitionResult(TransitionState.INVALID, "SEALED não canônico (durable != sealed)")
    if new_sealed.durable_prefix_length != old_active.durable_prefix_length + SEAL_FOOTER_BYTES:
        return TransitionResult(TransitionState.INVALID, "SEALED length != prefixo do ACTIVE + footer")
    # novo ACTIVE: último, vazio, canônico, encadeado, ordinal/segment_id+1, first_seq=R+1
    na = cand.segments[-1]
    if na.status != STATE_ACTIVE:
        return TransitionResult(TransitionState.INVALID, "novo segmento não é ACTIVE")
    if na.ordinal != old_active.ordinal + 1 or na.segment_id != old_active.segment_id + 1:
        return TransitionResult(TransitionState.INVALID, "ordinal/segment_id do novo ACTIVE incorretos")
    if na.first_seq != cand.read_seq + 1:
        return TransitionResult(TransitionState.INVALID, "first_seq do novo ACTIVE != R+1")
    if na.record_count != 0 or na.sealed_object_length != 0 or na.sealed_object_sha256 != _ZERO32:
        return TransitionResult(TransitionState.INVALID, "novo ACTIVE não está vazio/canônico")
    if na.durable_prefix_length <= 0:
        return TransitionResult(TransitionState.INVALID, "novo ACTIVE sem header durável")
    if not hmac.compare_digest(na.prev_segment_digest, new_sealed.sealed_object_sha256[:16]):
        return TransitionResult(TransitionState.INVALID, "cadeia do novo ACTIVE quebrada")
    if na.key_id != old_active.key_id:
        return TransitionResult(TransitionState.INVALID, "key_id do novo ACTIVE mudou")
    return TransitionResult(TransitionState.OK, "rotation")


def _validate_maintenance(prev, cand) -> TransitionResult:
    """Forma de MAINTENANCE/compaction (B4.1). AO CONTRÁRIO de BATCH/ROTATION, a identidade da geração
    MUDA: a compaction cria uma geração NOVA `G+1` cuja base absorve todo o estado até `R` (nova base
    inteira, novo `generation_id`) — então `_same_generation_identity` NÃO se aplica. Estrutura pura:
    scope preservado; `generation_id == prev+1`; `parent_generation_id == prev.generation_id`; `R`
    preservado (`base_seq == read_seq == prev.read_seq`, a base absorveu até `R`); exatamente 1 segmento
    ACTIVE vazio começando em `R+1`. O binding do `parent_manifest_digest` ao manifesto de ORIGEM é do
    `publish_maintenance` (contra o cursor); a abertura física da base é do `open_generation`."""
    if cand.scope_id != prev.scope_id:
        return TransitionResult(TransitionState.INVALID, "MAINTENANCE mudou o scope")
    if cand.generation_id != prev.generation_id + 1:
        return TransitionResult(TransitionState.INVALID, "generation_id != prev+1")
    if cand.parent_generation_id != prev.generation_id:
        return TransitionResult(TransitionState.INVALID, "parent_generation_id != prev.generation_id")
    if cand.read_seq != prev.read_seq:
        return TransitionResult(TransitionState.INVALID, "MAINTENANCE mudou R")
    if cand.base_seq != cand.read_seq:
        return TransitionResult(TransitionState.INVALID, "base_seq != read_seq (base não absorveu até R)")
    if len(cand.segments) != 1:
        return TransitionResult(TransitionState.INVALID, "G+1 deve ter exatamente 1 segmento")
    na = cand.segments[0]
    if na.status != STATE_ACTIVE:
        return TransitionResult(TransitionState.INVALID, "o segmento único de G+1 não é ACTIVE")
    if na.first_seq != cand.read_seq + 1:
        return TransitionResult(TransitionState.INVALID, "novo ACTIVE não começa em R+1")
    if na.record_count != 0 or na.sealed_object_length != 0 or na.sealed_object_sha256 != _ZERO32:
        return TransitionResult(TransitionState.INVALID, "novo ACTIVE de G+1 não está vazio/canônico")
    if na.durable_prefix_length <= 0:
        return TransitionResult(TransitionState.INVALID, "novo ACTIVE sem header durável")
    return TransitionResult(TransitionState.OK, "maintenance")


def validate_publication_transition(previous, candidate, purpose: PublicationPurpose) -> TransitionResult:
    """PURA (C4.1 + B4.1): a transição `previous → candidate` é permitida pelo `purpose`? Nunca lança.
    GENESIS exige `previous is None`. MAINTENANCE cria uma geração NOVA (identidade MUDA — roteada antes
    de `_same_generation_identity`). BATCH (avanço do mesmo ACTIVE) e ROTATION (ACTIVE→SEALED + novo
    ACTIVE) exigem a IDENTIDADE imutável da geração preservada. Não confere abertura física — isso é do
    `open_generation`."""
    if purpose == PublicationPurpose.GENESIS:
        if previous is not None:
            return TransitionResult(TransitionState.INVALID, "GENESIS exige ausência de manifesto anterior")
        return TransitionResult(TransitionState.OK, "genesis")
    if previous is None:
        return TransitionResult(TransitionState.INVALID, f"{purpose.name} exige manifesto anterior")
    if not previous.segments or not candidate.segments:
        return TransitionResult(TransitionState.INVALID, "manifesto sem segmentos")
    if purpose == PublicationPurpose.MAINTENANCE:
        return _validate_maintenance(previous, candidate)   # identidade MUDA — nova geração
    if not _same_generation_identity(previous, candidate):
        return TransitionResult(TransitionState.INVALID, "identidade imutável da geração mudou")
    if purpose == PublicationPurpose.BATCH:
        return _validate_batch(previous, candidate)
    if purpose == PublicationPurpose.ROTATION:
        return _validate_rotation(previous, candidate)
    return TransitionResult(TransitionState.INVALID, "purpose desconhecido")


# ================= C4.2: derivação pura do manifesto de batch =================
class AdvanceState(IntEnum):
    OK = 0
    INVALID = 1
    RESOURCE_LIMIT = 2             # overflow de seq (limite tipado, distinto de INVALID)


@dataclass(frozen=True)
class AdvanceResult:
    state: AdvanceState
    manifest_blob: bytes | None
    manifest: object | None        # EpochManifest
    candidate_read_seq: int
    reason: str


def advance_batch_manifest(cursor, candidate_head, applied_count: int, keyring) -> AdvanceResult:
    """PURA (C4.2), nunca lança. Deriva o manifesto do próximo BATCH SÓ do `cursor` (autoridade
    publicada) e do `candidate_head` (= `plan.candidate_snapshot.wal_head`): altera EXCLUSIVAMENTE
    `read_seq` e os campos do ACTIVE final (`record_count`, `durable_prefix_length`,
    `durable_prefix_sha256`); todo o resto (base, H, parent, SEALED, cadeia) fica byte-idêntico. O
    `key_id` é HERDADO do cursor (nunca escolhido pelo caller). Lei central:
    `ΔR = candidate_R − source_R = applied_count = Δrecord_count`."""
    if applied_count <= 0:
        return AdvanceResult(AdvanceState.INVALID, None, None, 0, "applied_count deve ser > 0")
    man = cursor.manifest
    old_active = cursor.active_descriptor
    candidate_R = candidate_head.durable_through_seq
    if candidate_R != cursor.read_seq + applied_count:
        return AdvanceResult(AdvanceState.INVALID, None, None, candidate_R, "ΔR != applied_count")
    if candidate_head.scope_id != man.scope_id:
        return AdvanceResult(AdvanceState.INVALID, None, None, candidate_R, "scope do head != manifesto")
    if (candidate_head.segment_id != old_active.segment_id
            or candidate_head.first_seq != old_active.first_seq):
        return AdvanceResult(AdvanceState.INVALID, None, None, candidate_R, "head não casa o ACTIVE")
    if candidate_head.byte_length < old_active.durable_prefix_length:
        return AdvanceResult(AdvanceState.INVALID, None, None, candidate_R, "prefixo durável diminuiu")
    if len(candidate_head.prefix_digest) != 32:
        return AdvanceResult(AdvanceState.INVALID, None, None, candidate_R, "prefix_digest != 32 bytes")
    new_record_count = old_active.record_count + applied_count
    if new_record_count != candidate_R - old_active.first_seq + 1:
        return AdvanceResult(AdvanceState.INVALID, None, None, candidate_R,
                             "record_count anterior + aplicados != R - first_seq + 1")
    if candidate_R >= MAX_SEQ:
        return AdvanceResult(AdvanceState.RESOURCE_LIMIT, None, None, candidate_R, "overflow de seq")
    key = keyring.get(man.key_id)                 # key_id HERDADO do cursor
    if key is None:
        return AdvanceResult(AdvanceState.INVALID, None, None, candidate_R, "key_id do cursor desconhecido")
    new_active = replace(old_active, record_count=new_record_count,
                         durable_prefix_length=candidate_head.byte_length,
                         durable_prefix_sha256=candidate_head.prefix_digest)
    new_man = replace(man, read_seq=candidate_R, segments=man.segments[:-1] + (new_active,))
    return AdvanceResult(AdvanceState.OK, new_man.serialize(key), new_man, candidate_R, "ok")


def _check_authority(authority, purpose: PublicationPurpose, store_scope) -> tuple:
    """(ok, reason). Valida a AUTORIDADE de uma publicação por purpose (C4.1 + B4.1): GENESIS não aceita
    lease; BATCH exige lease OWNED_BY_STORE|OWNED_BY_NEW_STORE; ROTATION exige OWNED_BY_PREPARED;
    MAINTENANCE (compaction) exige OWNED_BY_COMPACTION_PREPARED. Do lease confere: selo interno, `held`,
    `os.fstat(fd)` (FD ainda válido), scope == PublicationStore e estado permitido pelo purpose."""
    from horizon_memory._engine.horizon_batch import LeaseState
    if purpose == PublicationPurpose.GENESIS:
        return (True, "ok") if authority is None else (False, "GENESIS não aceita lease")
    allowed = {PublicationPurpose.BATCH: (LeaseState.OWNED_BY_STORE, LeaseState.OWNED_BY_NEW_STORE),
               PublicationPurpose.ROTATION: (LeaseState.OWNED_BY_PREPARED,),
               PublicationPurpose.MAINTENANCE: (LeaseState.OWNED_BY_COMPACTION_PREPARED,)}
    if authority is None:
        return (False, f"{purpose.name} exige lease")
    if not getattr(authority, "is_sealed", lambda: False)() or not getattr(authority, "held", False):
        return (False, "lease inválido (selo/held)")
    try:
        os.fstat(authority._fd)                       # FD ainda aberto/válido
    except OSError:
        return (False, "FD do lease inválido")
    if store_scope is not None and authority.scope_id != store_scope:
        return (False, "lease de outro scope")
    if authority.state not in allowed[purpose]:
        return (False, f"estado {authority.state.name} não permitido para {purpose.name}")
    return (True, "ok")


@dataclass(frozen=True)
class PublicationProof:
    scope_id: int
    generation_id: int
    revision: int
    read_seq: int
    manifest_sha256: bytes
    publication_sha256: bytes
    next_active_segment_id: int
    next_active_first_seq: int
    next_active_prefix_length: int
    next_active_prefix_sha256: bytes
    seal: object


@dataclass(frozen=True)
class PublishResult:
    state: PublishState
    proof: PublicationProof | None
    reason: str


def open_publication_record(sha256_hex: str, object_store, keyring,
                            max_bytes: int = _MAX_RECORD_BYTES) -> tuple:
    rr = object_store.get_limited(sha256_hex, max_bytes)
    if not rr.ok:
        return (PublicationState.MISSING if rr.reason == "MISSING" else PublicationState.CORRUPT,
                None, rr.reason)
    return parse_publication_record(rr.blob, keyring)


def _proof_from(record: PublicationRecord, pub_sha: bytes, active) -> PublicationProof:
    return PublicationProof(record.scope_id, record.generation_id, record.revision, record.read_seq,
                            record.manifest_sha256, pub_sha, active.segment_id, active.first_seq,
                            active.durable_prefix_length, active.durable_prefix_sha256, _PROOF_SEAL)


def _noop_failpoint(_stage: str) -> None:
    return None


def publish_genesis(store, object_store, wal_store, keyring, candidate_manifest_blob: bytes,
                    key_id: int, *, best_effort: bool = False, failpoint=None,
                    limits: "PublicationLimits | None" = None) -> PublishResult:
    """API PÚBLICA tipada (C4.1.2) — publicação de GENESIS: só com CURRENT ausente, sem lease. Não há
    entrada pública genérica por `purpose`: cada operação tem sua API tipada (`publish_genesis`,
    `publish_rotation`, e `publish_batch` no C4.2) para que a autoridade e a transição CORRETAS de cada
    operação sejam sempre exigidas — nada publica passando apenas um `purpose`."""
    return _publish_impl(store, object_store, wal_store, keyring, candidate_manifest_blob,
                         _ZERO32, key_id, best_effort=best_effort, failpoint=failpoint, limits=limits,
                         authority=None, purpose=PublicationPurpose.GENESIS)


def _publish_cas_primitive(store, object_store, wal_store, keyring, candidate_manifest_blob: bytes,
                           expected_current_digest: bytes, key_id: int, *, best_effort: bool = False,
                           failpoint=None, limits: "PublicationLimits | None" = None) -> PublishResult:
    """Primitivo CAS PRIVADO (C4.1.1): a mecânica pura de CAS/idempotência do CURRENT, SEM purpose nem
    autoridade. NÃO é API operacional — existe só para a camada de testes do C2 (que publica gerações
    deliberadamente não relacionadas para exercitar o CAS). Nenhum caminho publicado deve chamá-lo."""
    return _publish_impl(store, object_store, wal_store, keyring, candidate_manifest_blob,
                         expected_current_digest, key_id, best_effort=best_effort, failpoint=failpoint,
                         limits=limits, authority=None, purpose=None)


def _publish_impl(store, object_store, wal_store, keyring, candidate_manifest_blob: bytes,
                  expected_current_digest: bytes, key_id: int, *, best_effort: bool = False,
                  failpoint=None, limits: "PublicationLimits | None" = None,
                  authority=None, purpose: "PublicationPurpose | None" = None,
                  require_physical_open: bool = True) -> PublishResult:
    """Núcleo compartilhado da publicação por CAS sobre o CURRENT esperado. Semântica congelada:
    `revision` global e monotônica por scope; genesis é `revision=0`/`previous=zero32` SÓ com CURRENT
    ausente; senão `revision=anterior+1` e `previous == CURRENT observado`; `manifest_sha256` nunca zero;
    `record.read_seq == manifest.read_seq`; CURRENT ilegível → ABSTAIN. Idempotente: repetir a MESMA
    publicação após o CURRENT já apontar para ela → ALREADY_PUBLISHED.

    Se `purpose` for dado, SOB o mesmo `publish.lock` do CAS, valida AUTORIDADE (`_check_authority`) e a
    TRANSIÇÃO (`validate_publication_transition`: prev do CURRENT → candidato). `ALREADY_PUBLISHED`
    retorna antes, sem autoridade (não muta). `require_physical_open=False` (fast path do C4.2/BATCH)
    PULA o `open_generation` (replay físico completo) — a durabilidade já é provada pelo `DurableBatchProof`
    a montante; a validação física completa continua no genesis/rotação/recovery."""
    fp = failpoint or _noop_failpoint
    lim = limits or DEFAULT_PUBLICATION_LIMITS
    key = keyring.get(key_id)
    if key is None:
        return PublishResult(PublishState.INVALID_SEMANTICS, None, "key_id desconhecido")
    directory = _resolve_directory(store)

    # 1) o candidato TEM que abrir (validação física completa antes de publicar qualquer nome), com os
    #    LIMITES tipados. No fast path do BATCH, o `open_generation` (replay) é PULADO — a durabilidade
    #    é provada pelo DurableBatchProof, sem reler base+segmentos a cada commit.
    if require_physical_open:
        r = open_generation(candidate_manifest_blob, object_store, wal_store, keyring,
                            limits=lim.artifact, gen_limits=lim.generation)
        if r.state != OpenGenerationState.VALID:
            return PublishResult(PublishState.INVALID_CANDIDATE, None, f"candidato: {r.reason}")
    mstate, man, _ = parse_manifest(candidate_manifest_blob, keyring)
    if mstate != OpenGenerationState.VALID:
        return PublishResult(PublishState.INVALID_CANDIDATE, None, "manifesto ilegível")
    if not man.segments or man.segments[-1].status != STATE_ACTIVE:
        return PublishResult(PublishState.INVALID_SEMANTICS, None, "geração sem ACTIVE final")
    active = man.segments[-1]
    manifest_sha256 = hashlib.sha256(candidate_manifest_blob).digest()
    if manifest_sha256 == _ZERO32:
        return PublishResult(PublishState.INVALID_SEMANTICS, None, "manifest_sha256 zero")

    directory.mkdir(parents=True, exist_ok=True)
    lock_fd = open_hardened_lock(directory, ".publish.lock")   # O_NOFOLLOW + S_ISREG + 0600 (C3.2)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)              # CAS serializado na mesma máquina
        cst, cur_ptr, _why = read_current(directory, keyring, max_bytes=lim.max_current_bytes)
        if cst in (PublicationState.CORRUPT, PublicationState.INCOMPATIBLE, PublicationState.RESOURCE_LIMIT):
            return PublishResult(PublishState.ABSTAIN, None, "CURRENT ilegível — não sobrescreve")

        prev_record = None
        if cst == PublicationState.MISSING:             # genesis: só aceita com CURRENT ausente
            if expected_current_digest != _ZERO32:
                return PublishResult(PublishState.STALE_CURRENT, None, "esperava CURRENT, mas ausente")
            revision, previous = 0, _ZERO32
        else:
            cur_pub_sha = cur_ptr.publication_sha256     # C4.0.1: identidade completa do CURRENT
            # idempotência: o CURRENT já aponta para exatamente ESTA publicação?
            prst, cur_rec, _ = open_publication_record(cur_pub_sha.hex(), object_store, keyring,
                                                       max_bytes=lim.max_record_bytes)
            if prst != PublicationState.VALID:
                return PublishResult(PublishState.ABSTAIN, None, "PublicationRecord do CURRENT ilegível")
            if cur_ptr.key_id != cur_rec.key_id:         # a chave do ponteiro tem que assinar o record
                return PublishResult(PublishState.ABSTAIN, None, "CURRENT.key_id != record.key_id")
            if (hmac.compare_digest(cur_rec.previous_publication_sha256, expected_current_digest)
                    and hmac.compare_digest(cur_rec.manifest_sha256, manifest_sha256)
                    and cur_rec.read_seq == man.read_seq and cur_rec.scope_id == man.scope_id
                    and cur_rec.generation_id == man.generation_id):
                return PublishResult(PublishState.ALREADY_PUBLISHED,   # não muta → sem autoridade
                                     _proof_from(cur_rec, cur_pub_sha, active), "idempotente")
            if not hmac.compare_digest(cur_pub_sha, expected_current_digest):  # outro publisher já avançou
                return PublishResult(PublishState.STALE_CURRENT, None, "CURRENT != expected")
            revision, previous = cur_rec.revision + 1, cur_pub_sha
            prev_record = cur_rec

        # C4.1: autoridade + transição, SOB o mesmo publish.lock, para toda publicação com purpose
        if purpose is not None:
            ok_auth, why_auth = _check_authority(authority, purpose, _resolve_scope(store))
            if not ok_auth:
                return PublishResult(PublishState.INVALID_AUTHORITY, None, why_auth)
            prev_man = None
            if prev_record is not None:
                rrp = object_store.get_limited(prev_record.manifest_sha256.hex(), lim.artifact.max_blob_bytes)
                if not rrp.ok:
                    return PublishResult(PublishState.ABSTAIN, None, "manifesto anterior ilegível")
                pmst, prev_man, _ = parse_manifest(rrp.blob, keyring)
                if pmst != OpenGenerationState.VALID:
                    return PublishResult(PublishState.ABSTAIN, None, "manifesto anterior não abre")
            tr = validate_publication_transition(prev_man, man, purpose)
            if tr.state == TransitionState.NOT_ENABLED:
                return PublishResult(PublishState.PURPOSE_NOT_ENABLED, None, tr.reason)
            if tr.state != TransitionState.OK:
                return PublishResult(PublishState.INVALID_TRANSITION, None, tr.reason)

        # semântica do record ligada ao manifesto
        record = PublicationRecord(man.scope_id, man.generation_id, revision, man.read_seq,
                                   manifest_sha256, previous, key_id)
        record_blob = record.serialize(key)
        pub_sha = hashlib.sha256(record_blob).digest()

        object_store.put_object(candidate_manifest_blob)   # manifesto imutável, durável (fsync dir)
        fp("after_manifest")
        object_store.put_object(record_blob)               # PublicationRecord imutável, durável
        fp("after_record")

        current_blob = serialize_current(pub_sha, key_id, key)
        _write_current_pointer(directory, current_blob, fp, best_effort)
        fp("after_dirfsync_before_proof")
        return PublishResult(PublishState.PUBLISHED, _proof_from(record, pub_sha, active), "publicado")
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)


def _write_current_pointer(directory, current_blob, failpoint, best_effort) -> None:
    """CURRENT.tmp endurecido → fsync → [failpoint] → rename atômico → [failpoint] → fsync do dir."""
    directory = Path(directory)
    final = directory / CURRENT_NAME
    tmp = directory / f"{CURRENT_NAME}.tmp.{os.urandom(8).hex()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(tmp), flags, 0o600)
    try:
        _write_all(fd, current_blob)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        failpoint("before_current_replace")
        os.replace(str(tmp), str(final))
    except BaseException:
        _silent_unlink(tmp)          # se não renomeou, não deixa temp órfão
        raise
    failpoint("after_replace_before_dirfsync")
    fsync_dir(directory, best_effort=best_effort)


def bind_rotation_prepared(prepared, current_publication_digest, current_publication_record_blob,
                           current_manifest_blob, keyring) -> tuple:
    """Liga um `RotationPrepared` ao CURRENT de ORIGEM (C2.1): prova que o `current_manifest_blob` é
    realmente aquele apontado pelo `current_publication_digest` — não confia num manifesto arbitrário.
    Devolve (ok, next_segments|None, reason).

    Prova: SHA256(record_blob)==current_publication_digest; record.manifest_sha256==SHA256(manifest);
    record.scope/generation/read_seq == manifesto; ACTIVE final do manifesto == origem da rotação;
    prepared.read_seq==record.read_seq; old SEALED substitui exatamente esse ACTIVE; novo ACTIVE em
    R+1, encadeado; e o pacote é SELADO."""
    from horizon_memory._engine.horizon_rotation import (
        is_sealed_prepared, rotation_registry_name,
    )
    if not is_sealed_prepared(prepared):
        return (False, None, "RotationPrepared não selado")
    # 1) o record TEM que ser exatamente o apontado pelo digest do CURRENT
    if not hmac.compare_digest(hashlib.sha256(current_publication_record_blob).digest(),
                               current_publication_digest):
        return (False, None, "record_blob não corresponde ao current_publication_digest")
    rstate, record, _ = parse_publication_record(current_publication_record_blob, keyring)
    if rstate != PublicationState.VALID:
        return (False, None, "PublicationRecord ilegível")
    # 2) o manifesto tem que ser o do record
    if not hmac.compare_digest(record.manifest_sha256,
                               hashlib.sha256(current_manifest_blob).digest()):
        return (False, None, "manifest_sha256 do record != manifesto fornecido")
    mstate, man, _ = parse_manifest(current_manifest_blob, keyring)
    if mstate != OpenGenerationState.VALID:
        return (False, None, "CURRENT manifesto ilegível")
    if (record.scope_id != man.scope_id or record.generation_id != man.generation_id
            or record.read_seq != man.read_seq):
        return (False, None, "record ↔ manifesto incoerentes")
    if not man.segments or man.segments[-1].status != STATE_ACTIVE:
        return (False, None, "CURRENT sem ACTIVE final")
    cur_active = man.segments[-1]
    old = prepared.old_sealed_descriptor
    nxt = prepared.next_active_descriptor
    # 3) a rotação nasceu DESTE ACTIVE, em DESTE R
    if prepared.read_seq != record.read_seq:
        return (False, None, "prepared.read_seq != record.read_seq")
    # 4) o SEALED substituto ocupa o MESMO ordinal/segment_id/first_seq/cadeia do ACTIVE vigente
    if (old.ordinal != cur_active.ordinal or old.segment_id != cur_active.segment_id
            or old.first_seq != cur_active.first_seq
            or not hmac.compare_digest(old.prev_segment_digest, cur_active.prev_segment_digest)):
        return (False, None, "old_sealed não substitui o ACTIVE do CURRENT")
    # 5) novo ACTIVE encadeia o SEALED recém-selado e começa em R+1
    if (not hmac.compare_digest(nxt.prev_segment_digest, old.sealed_object_sha256[:16])
            or nxt.first_seq != prepared.read_seq + 1 or nxt.ordinal != old.ordinal + 1):
        return (False, None, "cadeia/R descontínuos")
    # os SEALED anteriores permanecem byte-idênticos (mesma tupla de descriptors)
    next_segments = tuple(man.segments[:-1]) + (old, nxt)
    return (True, next_segments, "ok")


# ================= C4.0: cursor publicado (autoridade da geração vigente) =================
_CURSOR_SEAL = object()            # selo privado do cursor: só `open_published_cursor` o produz
CURSOR_SEAL = _CURSOR_SEAL         # alias público para conferência (não serializável/forjável)


class CursorState(IntEnum):
    VALID = 0
    NO_CURRENT = 1                 # CURRENT ausente (genesis ainda não publicado) — não é erro
    CORRUPT = 2
    INCOMPATIBLE = 3               # scope do store != manifesto, ou key_id desconhecido
    RESOURCE_LIMIT = 4             # recusa por ORÇAMENTO (limites), distinta de corrupção (C4.1)


@dataclass(frozen=True)
class PublishedCursor:
    """Autoridade publicada do scope, reconstruída INTEIRAMENTE do CURRENT durável (C4.0):
    CURRENT → PublicationRecord → manifest object → EpochManifest → ACTIVE final → PublicationProof.
    É SELADA — só `open_published_cursor` a produz, tendo provado cada elo (digests + identidade). O
    C4 deriva o próximo manifesto de batch SÓ deste cursor; nunca de um manifesto/template do caller."""
    proof: PublicationProof
    manifest_blob: bytes
    manifest: object               # EpochManifest (parseado do manifest_blob)
    active_descriptor: object      # WalSegmentDescriptor — segmento ACTIVE final do manifesto
    revision: int
    read_seq: int
    _seal: object


def is_sealed_cursor(cursor) -> bool:
    """True se `cursor` foi selado por `open_published_cursor` (não uma dataclass forjada à mão)."""
    return getattr(cursor, "_seal", None) is _CURSOR_SEAL


def _resolve_scope(store_or_dir):
    return store_or_dir.scope_id if isinstance(store_or_dir, PublicationStore) else None


def open_published_cursor(store, object_store, keyring, *, limits=None) -> tuple:
    """(state, PublishedCursor|None, reason). Reconstrói a autoridade publicada do scope a partir do
    CURRENT durável, conferindo TODOS os elos: CURRENT→PublicationRecord (digest + `key_id` do ponteiro
    == `key_id` do record, C4.0.1); record→manifesto (`manifest_sha256` == SHA-256 do blob);
    record↔manifesto coerentes (scope/generation/read_seq); scope do store == manifesto. Valida ainda
    (barato, sem reler a base): lei de cobertura (`check_coverage`, que inclui o ACTIVE/SEALED canônico),
    preflight de `GenerationLimits`, e `proof.revision == record.revision` / `proof.read_seq ==
    manifest.read_seq`. NUNCA confia num manifesto/prova do caller — tudo é derivado do CURRENT."""
    lim = limits or DEFAULT_PUBLICATION_LIMITS
    directory = _resolve_directory(store)
    cst, cur_ptr, why = read_current(directory, keyring, max_bytes=lim.max_current_bytes)
    if cst == PublicationState.MISSING:
        return (CursorState.NO_CURRENT, None, "CURRENT ausente")
    if cst == PublicationState.RESOURCE_LIMIT:
        return (CursorState.RESOURCE_LIMIT, None, f"CURRENT: {why}")   # orçamento, não corrupção
    if cst != PublicationState.VALID:
        return (CursorState.CORRUPT, None, f"CURRENT: {why}")
    cur_pub_sha = cur_ptr.publication_sha256
    prst, record, rwhy = open_publication_record(cur_pub_sha.hex(), object_store, keyring,
                                                 max_bytes=lim.max_record_bytes)
    if prst != PublicationState.VALID:
        return (CursorState.CORRUPT, None, f"PublicationRecord: {rwhy}")
    if cur_ptr.key_id != record.key_id:               # C4.0.1: a chave do ponteiro assina o record
        return (CursorState.CORRUPT, None, "CURRENT.key_id != record.key_id")
    rr = object_store.get_limited(record.manifest_sha256.hex(), lim.artifact.max_blob_bytes)
    if not rr.ok:
        return (CursorState.CORRUPT, None, f"manifesto: {rr.reason}")
    if not hmac.compare_digest(hashlib.sha256(rr.blob).digest(), record.manifest_sha256):
        return (CursorState.CORRUPT, None, "manifesto não casa manifest_sha256")   # defesa extra (store já é CA)
    mstate, man, mwhy = parse_manifest(rr.blob, keyring)
    if mstate != OpenGenerationState.VALID:
        return (CursorState.CORRUPT, None, f"manifesto ilegível: {mwhy}")
    if (record.scope_id != man.scope_id or record.generation_id != man.generation_id
            or record.read_seq != man.read_seq):
        return (CursorState.CORRUPT, None, "record ↔ manifesto incoerentes")
    scope = _resolve_scope(store)
    if scope is not None and scope != man.scope_id:
        return (CursorState.INCOMPATIBLE, None, "scope do store != manifesto")
    # cobertura (inclui ACTIVE/SEALED canônico e cadeia) + limites por dimensão — barato, sem reler base
    cov_ok, cov_why = check_coverage(man)
    if not cov_ok:
        return (CursorState.CORRUPT, None, f"cobertura: {cov_why}")
    pf_ok, pf_why = _generation_preflight(man, len(rr.blob), lim.generation)
    if not pf_ok:
        return (CursorState.RESOURCE_LIMIT, None, f"limites: {pf_why}")   # orçamento != corrupção
    if not man.segments or man.segments[-1].status != STATE_ACTIVE:
        return (CursorState.CORRUPT, None, "geração sem ACTIVE final")
    active = man.segments[-1]
    proof = _proof_from(record, cur_pub_sha, active)
    if proof.revision != record.revision or proof.read_seq != man.read_seq:
        return (CursorState.CORRUPT, None, "prova ↔ record/manifesto incoerentes")
    cursor = PublishedCursor(proof, rr.blob, man, active, record.revision, man.read_seq, _CURSOR_SEAL)
    return (CursorState.VALID, cursor, "ok")


def publish_rotation(store, object_store, wal_store, keyring, cursor, prepared,
                     candidate_manifest_blob: bytes, key_id: int, *, authority,
                     best_effort: bool = False, failpoint=None,
                     limits: "PublicationLimits | None" = None) -> PublishResult:
    """API PÚBLICA de rotação (C4.1.1). A comparação pura não deriva o SHA real do SEALED (prefixo +
    footer); então a publicação ROTATION EXIGE o `RotationPrepared` SELADO — cujo `old_sealed_descriptor`
    já carrega o SHA provado por `publish_sealed` — LIGADO ao `cursor` (o CURRENT vigente). Isso impede
    que um lease `OWNED_BY_PREPARED` publique uma história selada DIFERENTE: o candidato tem que ser
    EXATAMENTE `cursor.manifest` com o ACTIVE final trocado por `(prepared.old_sealed_descriptor,
    prepared.next_active_descriptor)`. Delega o CAS ao núcleo com `purpose=ROTATION` e a mesma
    autoridade. `expected` é o próprio `cursor.proof.publication_sha256` (rotaciona sobre o cursor)."""
    from horizon_memory._engine.horizon_rotation import is_sealed_prepared
    if not is_sealed_cursor(cursor):
        return PublishResult(PublishState.INVALID_TRANSITION, None, "cursor não selado")
    if not is_sealed_prepared(prepared):
        return PublishResult(PublishState.INVALID_TRANSITION, None, "RotationPrepared não selado")
    ok_auth, why = _check_authority(authority, PublicationPurpose.ROTATION, _resolve_scope(store))
    if not ok_auth:
        return PublishResult(PublishState.INVALID_AUTHORITY, None, why)
    # binding cursor↔prepared: a rotação nasceu DESTE ACTIVE, NESTE R
    old, nxt, ca = prepared.old_sealed_descriptor, prepared.next_active_descriptor, cursor.active_descriptor
    if (prepared.read_seq != cursor.read_seq or old.ordinal != ca.ordinal
            or old.segment_id != ca.segment_id or old.first_seq != ca.first_seq
            or not hmac.compare_digest(old.prev_segment_digest, ca.prev_segment_digest)
            or old.key_id != ca.key_id):
        return PublishResult(PublishState.INVALID_TRANSITION, None, "prepared não casa o cursor/ACTIVE")
    mstate, cand, _ = parse_manifest(candidate_manifest_blob, keyring)
    if mstate != OpenGenerationState.VALID:
        return PublishResult(PublishState.INVALID_CANDIDATE, None, "candidato ilegível")
    expected = replace(cursor.manifest, segments=cursor.manifest.segments[:-1] + (old, nxt))
    if cand != expected:      # candidato != a rotação EXATA preparada (SEALED real do prepared)
        return PublishResult(PublishState.INVALID_TRANSITION, None, "candidato != rotação preparada")
    res = _publish_impl(store, object_store, wal_store, keyring, candidate_manifest_blob,
                        cursor.proof.publication_sha256, key_id, best_effort=best_effort,
                        failpoint=failpoint, limits=limits, authority=authority,
                        purpose=PublicationPurpose.ROTATION)
    if res.state in (PublishState.PUBLISHED, PublishState.ALREADY_PUBLISHED):
        # FH-03.2: depois que CURRENT torna o pacote alcançável, o root prepared deixa de ser
        # necessário. Falha de limpeza é um vazamento conservador (o GC continuará protegendo),
        # nunca motivo para reverter uma publicação já linearizada.
        try:
            from horizon_memory._engine.horizon_gc import PreparedRegistry
            PreparedRegistry(store).unregister(rotation_registry_name(prepared))
        except Exception:
            pass
    return res


# ================= C4.2: intent lógico + prova de durabilidade + publish_batch =================
_INTENT_SEAL = object()            # selo privado do intent (só prepare_batch_publication o produz)


@dataclass(frozen=True)
class BatchPublicationIntent:
    """Token LÓGICO e imutável (C4.2), produzido SÓ por `prepare_batch_publication`. Vincula, num único
    objeto selado, a publicação/cursor de ORIGEM, `R`/revisão de origem, a identidade do ACTIVE, o
    resumo do `BatchPlan` (contagem de frames + digest do prefixo — nunca o `candidate_hasher` mutável),
    a quantidade EXATA de receipts APPLIED, o intervalo contíguo `[source_R+1, candidate_R]`, o
    `candidate_head` (comprimento + SHA do novo prefixo), e o manifesto candidato + seu digest. O
    `key_id` é herdado do cursor. Prova O QUE se pretende publicar — não que os bytes ficaram duráveis
    (isso é do `DurableBatchProof`)."""
    source_publication_sha256: bytes
    source_revision: int
    source_read_seq: int
    scope_id: int
    segment_id: int
    first_seq: int
    source_byte_length: int              # comprimento do prefixo ACTIVE ANTES do batch (C4.2.1)
    source_prefix_sha256: bytes          # SHA do prefixo ACTIVE ANTES do batch (C4.2.1)
    applied_count: int
    frame_count: int
    candidate_read_seq: int              # source_read_seq + applied_count
    candidate_byte_length: int           # candidate_head.byte_length
    candidate_prefix_sha256: bytes       # candidate_head.prefix_digest (== digest do candidate_hasher)
    candidate_manifest_blob: bytes
    candidate_manifest_sha256: bytes
    key_id: int
    _seal: object


def is_sealed_intent(intent) -> bool:
    return getattr(intent, "_seal", None) is _INTENT_SEAL


_INTENT_DIGEST_DOMAIN = b"HORIZON-BATCH-INTENT-v1"


def intent_canonical_digest(intent) -> bytes:
    """Digest CANÔNICO e íntegro do intent (C4.2.1) — SHA-256 com separação de domínio de TODOS os
    campos (não só o `candidate_manifest_sha256`). É o que o `DurableBatchProof` vincula, provando que a
    durabilidade se refere EXATAMENTE a este intent, sem colisão possível com outro batch."""
    h = hashlib.sha256()
    h.update(_INTENT_DIGEST_DOMAIN)
    h.update(struct.pack("<QIIIQ32sQ32sQQ32s",
                         intent.source_revision, intent.scope_id, intent.segment_id, intent.key_id,
                         intent.source_read_seq, intent.source_publication_sha256,
                         intent.first_seq, intent.source_prefix_sha256,
                         intent.source_byte_length, intent.applied_count, intent.candidate_prefix_sha256))
    h.update(struct.pack("<QQQ32s", intent.frame_count, intent.candidate_read_seq,
                         intent.candidate_byte_length, intent.candidate_manifest_sha256))
    return h.digest()


def prepare_batch_publication(published_state, batch_plan, keyring) -> tuple:
    """(PublishState, BatchPublicationIntent|None, reason). Constrói o intent a partir do ESTADO
    PUBLICADO (cursor) e do `BatchPlan`. Batch sem APPLIED → `NO_PUBLICATION_REQUIRED` (nada a
    publicar; o C4.3 não grava WAL/fsync/CURRENT). O manifesto é derivado INTERNAMENTE por
    `advance_batch_manifest` (nunca recebido do caller). `ΔR = applied = frame_count`."""
    cursor = getattr(published_state, "cursor", None)
    if cursor is None or not is_sealed_cursor(cursor):
        return (PublishState.INVALID_INTENT, None, "estado publicado sem cursor selado")
    applied = sum(1 for rc in batch_plan.receipts if rc.status == APPLIED)
    if applied == 0:
        return (PublishState.NO_PUBLICATION_REQUIRED, None, "batch sem APPLIED")
    if applied != len(batch_plan.frames):
        return (PublishState.INVALID_INTENT, None, "applied_count != número de frames")
    head = batch_plan.candidate_head
    adv = advance_batch_manifest(cursor, head, applied, keyring)
    if adv.state == AdvanceState.RESOURCE_LIMIT:
        return (PublishState.RESOURCE_LIMIT, None, f"advance: {adv.reason}")
    if adv.state != AdvanceState.OK:
        return (PublishState.INVALID_INTENT, None, f"advance: {adv.reason}")
    act = cursor.active_descriptor
    intent = BatchPublicationIntent(
        cursor.proof.publication_sha256, cursor.revision, cursor.read_seq,
        cursor.manifest.scope_id, act.segment_id, act.first_seq,
        act.durable_prefix_length, act.durable_prefix_sha256,     # prefixo de ORIGEM (C4.2.1)
        applied, len(batch_plan.frames), adv.candidate_read_seq, head.byte_length, head.prefix_digest,
        adv.manifest_blob, hashlib.sha256(adv.manifest_blob).digest(), cursor.manifest.key_id, _INTENT_SEAL)
    return (PublishState.PUBLISHED, intent, "ok")


def publish_batch(store, object_store, wal_store, keyring, cursor, intent, durable_proof, *,
                  authority, best_effort: bool = False, failpoint=None,
                  limits: "PublicationLimits | None" = None) -> tuple:
    """API PÚBLICA de BATCH (C4.2) — (PublishState, PublishedCursor|None, reason). EXIGE os dois tokens:
    o `BatchPublicationIntent` selado (o que se pretende) e o `DurableBatchProof` selado (que os bytes
    ficaram duráveis — emitido só pelo caminho de durabilidade após write+fsync+fstat), além da
    autoridade (lease BATCH) e do CAS. NÃO chama `open_generation` (fast path: cursor validado + intent
    + prova durável + CAS substituem o replay completo, que continua no genesis/rotação/recovery).
    Devolve um novo `PublishedCursor` selado em `PUBLISHED` e em `ALREADY_PUBLISHED`."""
    from horizon_memory._engine.horizon_batch import is_sealed_durable_proof
    if not is_sealed_cursor(cursor):
        return (PublishState.INVALID_INTENT, None, "cursor não selado")
    if not is_sealed_intent(intent):
        return (PublishState.INVALID_INTENT, None, "intent não selado")
    if not is_sealed_durable_proof(durable_proof):
        return (PublishState.INVALID_DURABILITY, None, "prova de durabilidade não selada")
    ok_auth, why = _check_authority(authority, PublicationPurpose.BATCH, _resolve_scope(store))
    if not ok_auth:
        return (PublishState.INVALID_AUTHORITY, None, why)
    # intent ↔ cursor (mesma publicação/revisão/R/ACTIVE e PREFIXO de origem)
    if (not hmac.compare_digest(intent.source_publication_sha256, cursor.proof.publication_sha256)
            or intent.source_revision != cursor.revision or intent.source_read_seq != cursor.read_seq
            or intent.scope_id != cursor.manifest.scope_id
            or intent.segment_id != cursor.active_descriptor.segment_id
            or intent.first_seq != cursor.active_descriptor.first_seq
            or intent.source_byte_length != cursor.active_descriptor.durable_prefix_length
            or not hmac.compare_digest(intent.source_prefix_sha256,
                                       cursor.active_descriptor.durable_prefix_sha256)):
        return (PublishState.INVALID_INTENT, None, "intent não casa o cursor")
    if intent.candidate_read_seq != intent.source_read_seq + intent.applied_count:
        return (PublishState.INVALID_INTENT, None, "intervalo [R+1, candidate_R] incoerente")
    # prova durável ↔ intent (comprimento físico, SHA do prefixo, intervalo, DIGEST CANÔNICO do intent)
    if (not hmac.compare_digest(durable_proof.intent_digest,
                                intent_canonical_digest(intent))   # C4.2.1: digest íntegro
            or durable_proof.byte_length != intent.candidate_byte_length
            or not hmac.compare_digest(durable_proof.prefix_sha256, intent.candidate_prefix_sha256)
            or durable_proof.candidate_read_seq != intent.candidate_read_seq
            or durable_proof.interval_lo != intent.source_read_seq + 1
            or durable_proof.scope_id != intent.scope_id
            or durable_proof.segment_id != intent.segment_id):
        return (PublishState.INVALID_DURABILITY, None, "prova durável não casa o intent")
    # prova durável ↔ ARQUIVO físico vigente (mesmo st_dev/st_ino; bytes presentes após o fsync)
    from horizon_memory._engine.horizon_walstore import WalIdentity
    try:
        st = os.stat(str(wal_store._active_path(WalIdentity(intent.scope_id, intent.segment_id))))
    except OSError:
        return (PublishState.INVALID_DURABILITY, None, "ACTIVE físico ausente")
    if (st.st_dev != durable_proof.st_dev or st.st_ino != durable_proof.st_ino
            or st.st_size < durable_proof.byte_length):
        return (PublishState.INVALID_DURABILITY, None, "prova durável de outro arquivo/segmento")
    # CAS sobre o CURRENT do cursor, purpose=BATCH, FAST PATH (sem open_generation)
    res = _publish_impl(store, object_store, wal_store, keyring, intent.candidate_manifest_blob,
                        cursor.proof.publication_sha256, intent.key_id, best_effort=best_effort,
                        failpoint=failpoint, limits=limits, authority=authority,
                        purpose=PublicationPurpose.BATCH, require_physical_open=False)
    if res.state not in (PublishState.PUBLISHED, PublishState.ALREADY_PUBLISHED):
        return (res.state, None, res.reason)
    # cursor novo, reconstruído do CURRENT publicado (autoridade), em PUBLISHED e ALREADY_PUBLISHED
    cst, new_cursor, cwhy = open_published_cursor(store, object_store, keyring, limits=limits)
    if cst != CursorState.VALID:
        return (PublishState.IO_ERROR, None, f"cursor pós-publicação ilegível: {cwhy}")
    return (res.state, new_cursor, res.reason)


# ================= B4.1: publicação da compaction (MAINTENANCE) =================
def publish_maintenance(store, object_store, wal_store, keyring, cursor, prepared, *,
                        best_effort: bool = False, failpoint=None,
                        limits: "PublicationLimits | None" = None) -> PublishResult:
    """API PÚBLICA de MAINTENANCE/compaction (B4.1). Aceita EXCLUSIVAMENTE um `CompactionPrepared` SELADO
    e o LIGA ao `cursor` de origem (o CURRENT vigente): a compaction nasceu DESTE `R` e DESTE manifesto.
    O candidato tem que ser EXATAMENTE o pacote (`candidate_manifest_sha256` == SHA do blob) e seu pai o
    manifesto apontado pelo CURRENT (`cand.parent_manifest_digest == cursor.proof.manifest_sha256`) —
    impede que um lease OWNED_BY_COMPACTION_PREPARED publique uma base DIFERENTE. Full open da geração
    `G+1` (validação física completa da nova base) ANTES de publicar, delegado ao núcleo com
    `purpose=MAINTENANCE` (`require_physical_open=True`). CAS sobre o CURRENT de origem
    (`cursor.proof.publication_sha256`): mesmo pacote após perda de resposta → `ALREADY_PUBLISHED`;
    CURRENT diferente → `STALE_CURRENT`. Autoridade = o lease `OWNED_BY_COMPACTION_PREPARED` embutido no
    pacote. Ponto de linearização = `rename(CURRENT) + fsync_dir` (dentro de `_publish_impl`)."""
    from horizon_memory._engine.horizon_compaction import (
        compaction_registry_name, is_sealed_compaction,
    )
    if not is_sealed_cursor(cursor):
        return PublishResult(PublishState.INVALID_TRANSITION, None, "cursor não selado")
    if not is_sealed_compaction(prepared):
        return PublishResult(PublishState.INVALID_TRANSITION, None, "CompactionPrepared não selado")
    # candidato EXATAMENTE igual ao pacote (integridade do blob vs. digest selado)
    if not hmac.compare_digest(prepared.candidate_manifest_sha256,
                               hashlib.sha256(prepared.candidate_manifest_blob).digest()):
        return PublishResult(PublishState.INVALID_CANDIDATE, None, "candidate_manifest_sha256 != blob")
    # binding cursor↔prepared: mesma publicação/R de origem
    if (not hmac.compare_digest(prepared.source_publication_sha256, cursor.proof.publication_sha256)
            or prepared.read_seq != cursor.read_seq):
        return PublishResult(PublishState.INVALID_TRANSITION, None, "prepared não casa o cursor de origem")
    mstate, cand, _ = parse_manifest(prepared.candidate_manifest_blob, keyring)
    if mstate != OpenGenerationState.VALID:
        return PublishResult(PublishState.INVALID_CANDIDATE, None, "candidato ilegível")
    # FH-00.1: o manifesto PARSEADO tem que casar EXATAMENTE os descriptors do pacote (o digest do
    # pacote já garante isso ponta-a-ponta, mas comparamos os campos explicitamente — descriptors da
    # base e ACTIVE seguinte — para recusar qualquer candidato divergente sem depender só do digest).
    if dict(prepared.base_descriptors) != cand.base_descriptors:
        return PublishResult(PublishState.INVALID_CANDIDATE, None, "base_descriptors != pacote")
    if not cand.segments or prepared.next_active_descriptor != cand.segments[-1]:
        return PublishResult(PublishState.INVALID_CANDIDATE, None, "next_active_descriptor != pacote")
    # o PAI do candidato tem que ser EXATAMENTE o manifesto de origem apontado pelo CURRENT
    if not hmac.compare_digest(cand.parent_manifest_digest, cursor.proof.manifest_sha256):
        return PublishResult(PublishState.INVALID_TRANSITION, None, "parent != manifesto do cursor de origem")
    if cand.generation_id != cursor.manifest.generation_id + 1:
        return PublishResult(PublishState.INVALID_TRANSITION, None, "generation_id != cursor+1")
    authority = getattr(prepared, "lease", None)
    res = _publish_impl(store, object_store, wal_store, keyring, prepared.candidate_manifest_blob,
                        cursor.proof.publication_sha256, cand.key_id, best_effort=best_effort,
                        failpoint=failpoint, limits=limits, authority=authority,
                        purpose=PublicationPurpose.MAINTENANCE, require_physical_open=True)
    # FH-00.1: a prova publicada tem que apontar para EXATAMENTE o manifesto do pacote
    if (res.state in (PublishState.PUBLISHED, PublishState.ALREADY_PUBLISHED)
            and res.proof is not None
            and not hmac.compare_digest(res.proof.manifest_sha256,
                                        prepared.candidate_manifest_sha256)):
        return PublishResult(PublishState.INVALID_CANDIDATE, None,
                             "proof.manifest_sha256 != candidate_manifest_sha256")
    if res.state in (PublishState.PUBLISHED, PublishState.ALREADY_PUBLISHED):
        try:
            from horizon_memory._engine.horizon_gc import PreparedRegistry
            PreparedRegistry(store).unregister(compaction_registry_name(prepared))
        except Exception:
            pass
    return res
