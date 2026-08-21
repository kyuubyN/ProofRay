# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""WalStore — armazenamento do WAL separado do ObjectStore (V23-B3-1).

Dois namespaces distintos, com políticas de abertura distintas:

  ACTIVE  — arquivo MUTÁVEL que cresce, localizado por identidade `(scope_id, segment_id)`:
                wal/active/<scope-u32-hex>/<segment-u32-hex>.hwal
            NUNCA é endereçado por conteúdo; o caminho é derivado internamente da identidade
            (jamais recebido do manifesto) — path traversal é irrepresentável.
  SEALED  — objeto IMUTÁVEL endereçado por conteúdo (reusa o layout do ObjectStore):
                objects/<sha256>.hobj

Propriedades exigidas (todas cobertas pelos gates do B3-1):
  * `create_active` usa `O_CREAT|O_EXCL` — nunca sobrescreve um ACTIVE (→ ALREADY_EXISTS);
  * abertura por FD + `pread` (não `Path.read_bytes`), `O_NOFOLLOW` (recusa symlink), `S_ISREG`;
  * limite de tamanho conferido por `fstat` ANTES de ler/alocar;
  * ACTIVE publicado nunca é removido nem substituído; SEALED divergente nunca é sobrescrito;
  * erros SEMPRE tipados (`WalStoreState`), jamais `None`;
  * falha parcial na criação não deixa um ACTIVE utilizável (o arquivo meio-escrito é removido).

FRONTEIRA B3-1/B3-2: este módulo NÃO interpreta a cauda do ACTIVE nem decide `ReadView` — apenas
localiza, valida a segurança do arquivo, autentica o HEADER e confere a identidade, devolvendo os
bytes crus. A leitura/validação do `ActivePrefix` (permitindo cauda posterior) é do B3-2.
"""

from __future__ import annotations

import errno
import hashlib
import hmac
import os
import stat
import struct
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

from horizon_memory._engine.horizon_artifacts import ObjectStore
from horizon_memory._engine.horizon_durability import ensure_durable_directory_chain, fsync_dir
from horizon_memory._engine.horizon_wal import (
    SEAL_MAGIC,
    TAG_BYTES,
    WAL_SEGMENT_SEAL,
    WalHeaderInfo,
    _SEG_HEADER,
    _seal_footer,
    scan,
    CLEAN,
    STATE_ACTIVE,
    STATE_SEALED,
    INCOMPATIBLE as WAL_INCOMPATIBLE,
)

_U32_MAX = 0xFFFFFFFF
_HEADER_SIZE = _SEG_HEADER.size + TAG_BYTES
_KEY_ID_FIELD = 5   # em "<4sHIIQIIIB16s": magic,ver,scope,segment,first_seq,KEY_ID,...


class WalStoreState(IntEnum):
    VALID = 0
    MISSING = 1
    ALREADY_EXISTS = 2
    CORRUPT = 3
    INCOMPATIBLE = 4
    RESOURCE_LIMIT = 5
    IO_ERROR = 6
    POISONED = 7


@dataclass(frozen=True)
class WalIdentity:
    scope_id: int
    segment_id: int


@dataclass(frozen=True)
class WalStoreLimits:
    max_active_bytes: int = 1 << 30
    max_sealed_bytes: int = 1 << 30


@dataclass(frozen=True)
class ActiveWalHandle:
    identity: WalIdentity
    path: str
    header: WalHeaderInfo


@dataclass(frozen=True)
class CreateActiveResult:
    state: WalStoreState
    handle: ActiveWalHandle | None
    reason: str


@dataclass(frozen=True)
class ActiveWalOpenResult:
    state: WalStoreState
    blob: bytes | None            # bytes CRUS do arquivo ACTIVE (B3-2 interpreta prefixo/cauda)
    header: WalHeaderInfo | None
    reason: str


@dataclass(frozen=True)
class SealedWalDescriptor:
    sha256: bytes
    byte_length: int


@dataclass(frozen=True)
class SealedWalOpenResult:
    state: WalStoreState
    blob: bytes | None
    reason: str


@dataclass(frozen=True)
class PublishResult:
    state: WalStoreState
    descriptor: SealedWalDescriptor | None
    reason: str


@dataclass(frozen=True)
class SealActiveResult:
    state: WalStoreState
    reason: str
    repaired: bool = False          # True se um footer rasgado foi reparado (prefixo já provado)
    idempotent: bool = False        # True se o footer correto já existia


_WRITER_FD_SEAL = object()          # selo do FD validado (C3.1) — transferido ao GroupCommitStore


@dataclass(frozen=True)
class ValidatedActiveWriterFd:
    identity: WalIdentity
    fd: int
    header_length: int
    header: WalHeaderInfo
    seal: object


# ---------------- ActivePrefix (B3-2) ----------------
class ActivePrefixState(IntEnum):
    VALID = 0
    MISSING = 1                     # arquivo ACTIVE inexistente
    MISSING_COMMITTED_PREFIX = 2    # arquivo menor que o prefixo declarado
    CORRUPT = 3
    INCOMPATIBLE = 4                # versão do WAL / footer dentro do prefixo ACTIVE
    RESOURCE_LIMIT = 5
    IO_ERROR = 6


@dataclass(frozen=True)
class ActivePrefixExpectation:
    """O que o manifesto/`WalHead` afirma sobre o prefixo DURÁVEL do ACTIVE em `R`."""
    byte_length: int
    sha256: bytes                   # 32 bytes sobre exatamente `byte_length` bytes

    @classmethod
    def from_wal_head(cls, head) -> "ActivePrefixExpectation":
        # `head.prefix_digest` já é SHA-256 completo (32B) capturado atomicamente com R
        return cls(head.byte_length, head.prefix_digest)


@dataclass(frozen=True)
class ValidatedActivePrefix:
    identity: WalIdentity
    header: WalHeaderInfo
    frames: tuple                   # frames do prefixo (para o reducer incremental do B3-3)
    last_seq: int
    byte_length: int
    is_sealed: bool = False
    seal: object = None             # selo privado; só o WalStore carimba WAL_SEGMENT_SEAL


@dataclass(frozen=True)
class ValidatedSealedSegment:
    """Equivalente selado do `ValidatedActivePrefix`: um segmento SEALED lido do objeto imutável."""
    identity: WalIdentity
    header: WalHeaderInfo
    frames: tuple
    last_seq: int
    byte_length: int
    is_sealed: bool = True
    seal: object = None


# um token de segmento validado para o reducer é um dos dois — ambos só são selados pelo WalStore
ValidatedWalSegment = "ValidatedActivePrefix | ValidatedSealedSegment"


@dataclass(frozen=True)
class ActivePrefixResult:
    state: ActivePrefixState
    prefix: ValidatedActivePrefix | None
    reason: str


@dataclass(frozen=True)
class ValidatedSegmentResult:
    state: ActivePrefixState        # reusa o enum (cobre MISSING/MISSING_COMMITTED_PREFIX/etc.)
    segment: object | None          # ValidatedActivePrefix | ValidatedSealedSegment (selado)
    reason: str


def _os_write_all(fd: int, data: bytes) -> None:
    mv, n = memoryview(data), 0
    while n < len(data):
        try:
            w = os.write(fd, mv[n:])
        except InterruptedError:          # EINTR
            continue
        if w <= 0:
            raise OSError("write incompleto")
        n += w


def _pread_exact(fd: int, n: int) -> bytes:
    """Lê exatamente `n` bytes a partir do offset 0 por `pread`. Um append concorrente APÓS o offset
    `n` não afeta esses bytes (o WAL só faz append) — por isso não se exige tamanho/mtime estáveis."""
    data = bytearray()
    off = 0
    while off < n:
        try:
            chunk = os.pread(fd, n - off, off)
        except InterruptedError:
            continue
        if not chunk:
            break
        data += chunk
        off += len(chunk)
    return bytes(data)


def _pread_at(fd: int, offset: int, n: int) -> bytes:
    data = bytearray()
    got = 0
    while got < n:
        try:
            chunk = os.pread(fd, n - got, offset + got)
        except InterruptedError:
            continue
        if not chunk:
            break
        data += chunk
        got += len(chunk)
    return bytes(data)


def _pread_all(fd: int, size: int) -> bytes:
    data = bytearray()
    off = 0
    while off < size:
        try:
            chunk = os.pread(fd, size - off, off)
        except InterruptedError:
            continue
        if not chunk:
            break
        data += chunk
        off += len(chunk)
    return bytes(data)


def _is_hex64(s: str) -> bool:
    return isinstance(s, str) and len(s) == 64 and all(c in "0123456789abcdef" for c in s)


class WalStore:
    def __init__(self, root: str, keyring, limits: WalStoreLimits | None = None):
        self.root = Path(root)
        self.keyring = keyring
        self.limits = limits or WalStoreLimits()
        (self.root / "wal" / "active").mkdir(parents=True, exist_ok=True)
        self._objects = ObjectStore(str(self.root))     # objects/<sha>.hobj (imutável)

    # ---------------- caminhos derivados da identidade ----------------
    @staticmethod
    def _identity_ok(identity: WalIdentity) -> bool:
        return (isinstance(identity, WalIdentity)
                and 0 <= identity.scope_id <= _U32_MAX
                and 0 <= identity.segment_id <= _U32_MAX)

    def _active_path(self, identity: WalIdentity) -> Path:
        # nome derivado SÓ da identidade (u32 hex) — nunca vem do manifesto; traversal irrepresentável
        return (self.root / "wal" / "active" / f"{identity.scope_id:08x}"
                / f"{identity.segment_id:08x}.hwal")

    def _authenticate_header(self, header: bytes, identity: WalIdentity):
        """(state, WalHeaderInfo|None, reason). Autentica o header e confere a identidade."""
        if len(header) < _HEADER_SIZE:
            return (WalStoreState.CORRUPT, None, "header curto")
        try:
            key_id = _SEG_HEADER.unpack_from(header, 0)[_KEY_ID_FIELD]
        except struct.error:
            return (WalStoreState.CORRUPT, None, "header ilegível")
        key = self.keyring.get(key_id)
        if key is None:
            return (WalStoreState.INCOMPATIBLE, None, "key_id desconhecido")
        sr = scan(header[:_HEADER_SIZE], key, required=True,
                  scope_id=identity.scope_id, segment_id=identity.segment_id)
        if sr.classification == WAL_INCOMPATIBLE:
            return (WalStoreState.INCOMPATIBLE, None, "versão do WAL")
        if sr.classification != CLEAN or sr.header is None:
            # scope/segment/MAC divergentes do esperado caem aqui → header não bate a identidade
            return (WalStoreState.CORRUPT, None, "header diverge da identidade")
        return (WalStoreState.VALID, sr.header, "ok")

    # ---------------- ACTIVE (mutável, por identidade) ----------------
    def create_active(self, identity: WalIdentity, header: bytes,
                      _write=_os_write_all) -> CreateActiveResult:
        """Cria o arquivo ACTIVE com `O_CREAT|O_EXCL` e escreve o header autenticado. Falha parcial
        remove o arquivo meio-criado (nunca deixa um ACTIVE utilizável)."""
        if not self._identity_ok(identity):
            return CreateActiveResult(WalStoreState.CORRUPT, None, "identidade fora de u32")
        st, hi, why = self._authenticate_header(header, identity)
        if st != WalStoreState.VALID:
            return CreateActiveResult(st, None, why)
        path = self._active_path(identity)
        try:
            # cria E torna DURÁVEL a cadeia de diretórios (o NOME wal/active/<scope>/ precisa
            # sobreviver à perda de energia, não só o conteúdo do arquivo) — V23-C0
            ensure_durable_directory_chain(path.parent, self.root)
        except OSError as e:
            return CreateActiveResult(WalStoreState.IO_ERROR, None, f"mkdir errno {e.errno}")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(str(path), flags, 0o600)
        except FileExistsError:
            return CreateActiveResult(WalStoreState.ALREADY_EXISTS, None, "ACTIVE já existe")
        except OSError as e:
            return CreateActiveResult(WalStoreState.IO_ERROR, None, f"open errno {e.errno}")
        wrote_ok = False
        try:
            _write(fd, header)
            os.fsync(fd)
            wrote_ok = True
        except Exception:  # noqa: BLE001 — falha parcial
            pass
        finally:
            os.close(fd)
        if wrote_ok:
            fsync_dir(path.parent)       # o NOME do ACTIVE novo tem que ser durável
        if not wrote_ok:
            try:
                os.unlink(str(path))     # não deixa um ACTIVE meio-escrito utilizável
            except FileNotFoundError:
                pass
            return CreateActiveResult(WalStoreState.IO_ERROR, None, "falha ao escrever header")
        return CreateActiveResult(WalStoreState.VALID, ActiveWalHandle(identity, str(path), hi), "ok")

    def open_active(self, identity: WalIdentity, limits: WalStoreLimits | None = None
                    ) -> ActiveWalOpenResult:
        """Localiza e lê o ACTIVE com segurança; autentica o HEADER e confere a identidade. NÃO
        interpreta cauda nem decide ReadView (isso é B3-2) — devolve os bytes crus."""
        limits = limits or self.limits
        if not self._identity_ok(identity):
            return ActiveWalOpenResult(WalStoreState.CORRUPT, None, None, "identidade fora de u32")
        st, data, why = self._safe_read(self._active_path(identity), limits.max_active_bytes)
        if st != WalStoreState.VALID:
            return ActiveWalOpenResult(st, None, None, why)
        hst, hi, hwhy = self._authenticate_header(data, identity)
        if hst != WalStoreState.VALID:
            return ActiveWalOpenResult(hst, None, None, hwhy)
        return ActiveWalOpenResult(WalStoreState.VALID, data, hi, "ok")

    # ---------------- SEALED (imutável, por conteúdo) ----------------
    def publish_sealed(self, identity: WalIdentity,
                       limits: WalStoreLimits | None = None) -> PublishResult:
        """Publica um segmento SELADO como objeto imutável — SÓ com prova de selagem (B3-1.1). O
        caminho é derivado EXCLUSIVAMENTE da identidade (não se aceita `str`/`path` nem se confia no
        `path` público de um handle). Não certifica bytes arbitrários: exige header autenticado,
        `scan` CLEAN, `sealed is True` e scope/segment batendo a identidade — só então publica."""
        limits = limits or self.limits
        if not self._identity_ok(identity):
            return PublishResult(WalStoreState.CORRUPT, None, "identidade fora de u32")
        st, data, why = self._safe_read(self._active_path(identity), limits.max_sealed_bytes)
        if st != WalStoreState.VALID:
            return PublishResult(st, None, why)
        hst, hi, hwhy = self._authenticate_header(data, identity)   # header + identidade
        if hst != WalStoreState.VALID:
            return PublishResult(hst, None, hwhy)
        key = self.keyring.get(hi.key_id)
        sr = scan(data, key, required=True, scope_id=identity.scope_id, segment_id=identity.segment_id)
        if sr.classification == WAL_INCOMPATIBLE:
            return PublishResult(WalStoreState.INCOMPATIBLE, None, "versão do WAL")
        if sr.classification != CLEAN:
            return PublishResult(WalStoreState.CORRUPT, None, f"scan {sr.classification}")
        if not sr.sealed:                                # ACTIVE sem footer não é selável
            return PublishResult(WalStoreState.CORRUPT, None, "sem footer válido — não selável")
        try:
            sha_hex = self._objects.put_object(data)     # O_EXCL + hard-link; recusa divergente
        except Exception:  # noqa: BLE001
            return PublishResult(WalStoreState.CORRUPT, None, "objeto divergente já existe")
        return PublishResult(WalStoreState.VALID,
                             SealedWalDescriptor(bytes.fromhex(sha_hex), len(data)), "ok")

    # ---------------- ActivePrefix (B3-2): leitura do prefixo ACKado ----------------
    def read_active_prefix(self, identity: WalIdentity, expected: ActivePrefixExpectation,
                           limits: WalStoreLimits | None = None) -> ActivePrefixResult:
        """Lê e valida SOMENTE o prefixo durável `[0, expected.byte_length)` do ACTIVE. Um append
        concorrente DEPOIS do prefixo é legítimo (não é falso CORRUPT): lê-se exatamente
        `byte_length` bytes por `pread`, sem exigir tamanho/mtime estáveis. Bytes posteriores nunca
        entram na ReadView. Footer dentro do prefixo → INCOMPATIBLE."""
        limits = limits or self.limits
        if not self._identity_ok(identity):
            return ActivePrefixResult(ActivePrefixState.CORRUPT, None, "identidade fora de u32")
        if len(expected.sha256) != 32 or expected.byte_length <= 0:
            return ActivePrefixResult(ActivePrefixState.CORRUPT, None, "expectation inválida")
        if expected.byte_length > limits.max_active_bytes:
            return ActivePrefixResult(ActivePrefixState.RESOURCE_LIMIT, None, "prefixo excede o limite")
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(str(self._active_path(identity)), flags)
        except FileNotFoundError:
            return ActivePrefixResult(ActivePrefixState.MISSING, None, "ACTIVE ausente")
        except OSError as e:
            if e.errno in (errno.ELOOP, errno.EMLINK):
                return ActivePrefixResult(ActivePrefixState.CORRUPT, None, "symlink recusado")
            return ActivePrefixResult(ActivePrefixState.IO_ERROR, None, f"open errno {e.errno}")
        try:
            st_ = os.fstat(fd)
            if not stat.S_ISREG(st_.st_mode):
                return ActivePrefixResult(ActivePrefixState.CORRUPT, None, "não é arquivo regular")
            if st_.st_size < expected.byte_length:       # arquivo menor que o prefixo declarado
                return ActivePrefixResult(ActivePrefixState.MISSING_COMMITTED_PREFIX, None,
                                          "arquivo menor que o prefixo")
            data = _pread_exact(fd, expected.byte_length)  # exatamente byte_length, do offset 0
        except OSError as e:
            return ActivePrefixResult(ActivePrefixState.IO_ERROR, None, f"read errno {e.errno}")
        finally:
            os.close(fd)
        if len(data) != expected.byte_length:
            return ActivePrefixResult(ActivePrefixState.MISSING_COMMITTED_PREFIX, None,
                                      "prefixo incompleto")
        if not hmac.compare_digest(hashlib.sha256(data).digest(), expected.sha256):   # mutação em qualquer byte do prefixo
            return ActivePrefixResult(ActivePrefixState.CORRUPT, None, "prefixo diverge do digest")
        hst, hi, hwhy = self._authenticate_header(data, identity)
        if hst == WalStoreState.INCOMPATIBLE:
            return ActivePrefixResult(ActivePrefixState.INCOMPATIBLE, None, hwhy)
        if hst != WalStoreState.VALID:
            return ActivePrefixResult(ActivePrefixState.CORRUPT, None, hwhy)
        key = self.keyring.get(hi.key_id)
        sr = scan(data, key, required=True, scope_id=identity.scope_id, segment_id=identity.segment_id)
        if sr.classification == WAL_INCOMPATIBLE:
            return ActivePrefixResult(ActivePrefixState.INCOMPATIBLE, None, "versão do WAL")
        if sr.classification != CLEAN or sr.dropped_tail_bytes != 0 or sr.consumed_bytes != expected.byte_length:
            # comprimento terminando no meio de frame / bytes não canônicos no prefixo
            return ActivePrefixResult(ActivePrefixState.CORRUPT, None, f"prefixo não canônico ({sr.classification})")
        if sr.sealed:                                    # footer DENTRO do prefixo ACTIVE
            return ActivePrefixResult(ActivePrefixState.INCOMPATIBLE, None, "footer no prefixo ACTIVE")
        return ActivePrefixResult(
            ActivePrefixState.VALID,
            ValidatedActivePrefix(identity, hi, tuple(sr.frames), sr.last_seq, expected.byte_length,
                                  is_sealed=False, seal=WAL_SEGMENT_SEAL),
            "ok")

    def read_validated_segment(self, descriptor, scope_id: int,
                               limits: WalStoreLimits | None = None) -> ValidatedSegmentResult:
        """Lê UM segmento (SEALED ou ACTIVE) do descriptor e devolve um token Validated* SELADO,
        pronto para a autoridade de replay. É o único produtor do selo `WAL_SEGMENT_SEAL`."""
        limits = limits or self.limits
        ident = WalIdentity(scope_id, descriptor.segment_id)
        if descriptor.status == STATE_ACTIVE:
            exp = ActivePrefixExpectation(descriptor.durable_prefix_length,
                                          descriptor.durable_prefix_sha256)
            ap = self.read_active_prefix(ident, exp, limits)
            return ValidatedSegmentResult(ap.state, ap.prefix, ap.reason)
        if descriptor.status != STATE_SEALED:
            return ValidatedSegmentResult(ActivePrefixState.CORRUPT, None, "status inválido")
        rr = self.open_sealed(descriptor.sealed_object_sha256.hex(), limits)
        if rr.state != WalStoreState.VALID:
            _M = {WalStoreState.MISSING: ActivePrefixState.MISSING,
                  WalStoreState.RESOURCE_LIMIT: ActivePrefixState.RESOURCE_LIMIT,
                  WalStoreState.INCOMPATIBLE: ActivePrefixState.INCOMPATIBLE}
            return ValidatedSegmentResult(_M.get(rr.state, ActivePrefixState.CORRUPT), None, rr.reason)
        obj = rr.blob
        if len(obj) != descriptor.sealed_object_length:
            return ValidatedSegmentResult(ActivePrefixState.CORRUPT, None, "comprimento do objeto")
        key = self.keyring.get(descriptor.key_id)
        if key is None:
            return ValidatedSegmentResult(ActivePrefixState.INCOMPATIBLE, None, "key_id desconhecido")
        sr = scan(obj, key, required=True, scope_id=scope_id, segment_id=descriptor.segment_id)
        if sr.classification == WAL_INCOMPATIBLE:
            return ValidatedSegmentResult(ActivePrefixState.INCOMPATIBLE, None, "versão do WAL")
        if sr.classification != CLEAN or not sr.sealed:      # SEALED EXIGE footer válido
            return ValidatedSegmentResult(ActivePrefixState.CORRUPT, None,
                                          f"scan={sr.classification} sealed={sr.sealed}")
        seg = ValidatedSealedSegment(ident, sr.header, tuple(sr.frames), sr.last_seq, len(obj),
                                     is_sealed=True, seal=WAL_SEGMENT_SEAL)
        return ValidatedSegmentResult(ActivePrefixState.VALID, seg, "ok")

    # ---------------- aquisição do FD do ACTIVE p/ escrita (C3.1: sem TOCTOU) ----------------
    def acquire_active_writer(self, identity: WalIdentity, expected_length: int,
                              expected_sha256: bytes, limits: WalStoreLimits | None = None) -> tuple:
        """Abre o ACTIVE UMA vez por FD (`O_RDWR|O_NOFOLLOW`), valida `S_ISREG`, comprimento, digest e
        header NO PRÓPRIO FD, e devolve um token SELADO com o FD — que é transferido diretamente ao
        `GroupCommitStore`, sem reabertura por caminho (elimina a corrida entre validar e reabrir).
        (state, ValidatedActiveWriterFd|None, reason)."""
        limits = limits or self.limits
        if not self._identity_ok(identity):
            return (WalStoreState.CORRUPT, None, "identidade fora de u32")
        if expected_length <= 0 or expected_length > limits.max_active_bytes or len(expected_sha256) != 32:
            return (WalStoreState.CORRUPT, None, "expectation inválida")
        flags = os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(str(self._active_path(identity)), flags)
        except FileNotFoundError:
            return (WalStoreState.MISSING, None, "ACTIVE ausente")
        except OSError as e:
            if e.errno in (errno.ELOOP, errno.EMLINK):
                return (WalStoreState.CORRUPT, None, "symlink recusado")
            return (WalStoreState.IO_ERROR, None, f"open errno {e.errno}")
        ok = False
        try:
            st_ = os.fstat(fd)
            if not stat.S_ISREG(st_.st_mode):
                return (WalStoreState.CORRUPT, None, "não é arquivo regular")
            if st_.st_size != expected_length:                 # header-only exige tamanho EXATO
                return (WalStoreState.CORRUPT, None, "comprimento != esperado")
            data = _pread_exact(fd, expected_length)
            if (len(data) != expected_length
                    or not hmac.compare_digest(hashlib.sha256(data).digest(), expected_sha256)):
                return (WalStoreState.CORRUPT, None, "digest do ACTIVE diverge")
            hst, hi, hwhy = self._authenticate_header(data, identity)
            if hst != WalStoreState.VALID:
                return (hst, None, hwhy)
            token = ValidatedActiveWriterFd(identity, fd, expected_length, hi, _WRITER_FD_SEAL)
            ok = True
            return (WalStoreState.VALID, token, "ok")
        except OSError as e:
            return (WalStoreState.IO_ERROR, None, f"io errno {e.errno}")
        finally:
            if not ok:
                os.close(fd)

    # ---------------- selagem do ACTIVE (B3-4) ----------------
    def seal_active(self, identity: WalIdentity, expected: ActivePrefixExpectation,
                    expected_last_seq: int, limits: WalStoreLimits | None = None) -> SealActiveResult:
        """Sela o ACTIVE acrescentando o footer autenticado — SÓ depois de PROVAR o prefixo exato
        (comprimento + SHA-256), que `last_seq == R`, e que NÃO há frame posterior a `R`. Idempotente
        se o footer correto já existir. Um footer RASGADO (prefixo do footer correto) é reparado sem
        truncar operação alguma; um footer divergente, ou qualquer FRAME após o prefixo, é recusado —
        nunca se trunca uma possível operação ACKada."""
        limits = limits or self.limits
        if not self._identity_ok(identity):
            return SealActiveResult(WalStoreState.CORRUPT, "identidade fora de u32")
        if len(expected.sha256) != 32 or expected.byte_length <= 0:
            return SealActiveResult(WalStoreState.CORRUPT, "expectation inválida")
        if expected.byte_length > limits.max_active_bytes:
            return SealActiveResult(WalStoreState.RESOURCE_LIMIT, "prefixo excede o limite")
        flags = os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(str(self._active_path(identity)), flags)
        except FileNotFoundError:
            return SealActiveResult(WalStoreState.MISSING, "ACTIVE ausente")
        except OSError as e:
            if e.errno in (errno.ELOOP, errno.EMLINK):
                return SealActiveResult(WalStoreState.CORRUPT, "symlink recusado")
            return SealActiveResult(WalStoreState.IO_ERROR, f"open errno {e.errno}")
        try:
            st_ = os.fstat(fd)
            if not stat.S_ISREG(st_.st_mode):
                return SealActiveResult(WalStoreState.CORRUPT, "não é arquivo regular")
            if st_.st_size < expected.byte_length:
                return SealActiveResult(WalStoreState.CORRUPT, "arquivo menor que o prefixo declarado")
            prefix = _pread_exact(fd, expected.byte_length)
            if (len(prefix) != expected.byte_length
                    or not hmac.compare_digest(hashlib.sha256(prefix).digest(), expected.sha256)):
                return SealActiveResult(WalStoreState.CORRUPT, "prefixo diverge do digest")
            hst, hi, hwhy = self._authenticate_header(prefix, identity)
            if hst != WalStoreState.VALID:
                return SealActiveResult(hst, hwhy)
            key = self.keyring.get(hi.key_id)
            sr = scan(prefix, key, required=True, scope_id=identity.scope_id,
                      segment_id=identity.segment_id)
            if sr.classification != CLEAN or sr.consumed_bytes != expected.byte_length or sr.sealed:
                return SealActiveResult(WalStoreState.CORRUPT, "prefixo não canônico")
            if sr.last_seq != expected_last_seq:
                return SealActiveResult(WalStoreState.CORRUPT, "last_seq != R")
            footer = _seal_footer(key, identity.scope_id, identity.segment_id, expected_last_seq,
                                  expected.byte_length, hashlib.sha256(prefix).digest()[:16])
            tail_len = st_.st_size - expected.byte_length
            if tail_len == 0:                                   # sem cauda: acrescenta o footer
                self._pwrite_all(fd, footer, expected.byte_length)
                os.fsync(fd)
                return SealActiveResult(WalStoreState.VALID, "selado")
            # a ÚNICA cauda permitida na selagem é um footer: recusa ANTES de alocar uma cauda enorme
            # (um `_pread_at` de gigabytes seria um vetor de esgotamento de memória).
            if tail_len > len(footer):
                return SealActiveResult(WalStoreState.CORRUPT, "cauda maior que um footer — recusado")
            existing = _pread_at(fd, expected.byte_length, tail_len)
            if existing == footer:                              # footer correto já presente
                return SealActiveResult(WalStoreState.VALID, "idempotente", idempotent=True)
            if footer.startswith(existing):                     # footer RASGADO (prefixo do correto)
                os.ftruncate(fd, expected.byte_length)          # descarta só bytes de footer, nunca frame
                self._pwrite_all(fd, footer, expected.byte_length)
                os.fsync(fd)
                return SealActiveResult(WalStoreState.VALID, "footer reparado", repaired=True)
            if existing[:len(SEAL_MAGIC)] == SEAL_MAGIC:        # footer DIVERGENTE → recusa
                return SealActiveResult(WalStoreState.CORRUPT, "footer divergente")
            return SealActiveResult(WalStoreState.CORRUPT, "frame posterior ao prefixo — não trunca")
        except OSError as e:
            return SealActiveResult(WalStoreState.IO_ERROR, f"io errno {e.errno}")
        finally:
            os.close(fd)

    @staticmethod
    def _pwrite_all(fd: int, data: bytes, offset: int) -> None:
        mv, n = memoryview(data), 0
        while n < len(data):
            try:
                w = os.pwrite(fd, mv[n:], offset + n)
            except InterruptedError:
                continue
            if w <= 0:
                raise OSError("pwrite incompleto")
            n += w

    # ---------------- delegação ao ObjectStore (base + SEALED partilham objects/) ----------------
    def put_object(self, blob: bytes) -> str:
        return self._objects.put_object(blob)

    def get_limited(self, digest_hex: str, max_bytes: int):
        return self._objects.get_limited(digest_hex, max_bytes)

    def get(self, digest_hex: str, max_bytes: int = 1 << 30):
        return self._objects.get(digest_hex, max_bytes)

    def _path(self, digest_hex: str) -> Path:
        return self._objects._path(digest_hex)

    def open_sealed(self, sha256_hex: str, limits: WalStoreLimits | None = None
                    ) -> SealedWalOpenResult:
        limits = limits or self.limits
        if not _is_hex64(sha256_hex):
            return SealedWalOpenResult(WalStoreState.CORRUPT, None, "digest inválido")
        path = self.root / "objects" / f"{sha256_hex}.hobj"
        st, data, why = self._safe_read(path, limits.max_sealed_bytes)
        if st != WalStoreState.VALID:
            return SealedWalOpenResult(st, None, why)
        if not hmac.compare_digest(hashlib.sha256(data).hexdigest(), sha256_hex):
            return SealedWalOpenResult(WalStoreState.CORRUPT, None, "digest não confere")
        return SealedWalOpenResult(WalStoreState.VALID, data, "ok")

    # ---------------- leitura segura comum ----------------
    def _safe_read(self, path: Path, max_bytes: int):
        """(state, data|None, reason). FD + O_NOFOLLOW + S_ISREG + limite por fstat ANTES de ler +
        detecção de alteração concorrente. Nunca lança; nunca devolve None sem estado."""
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(str(path), flags)
        except FileNotFoundError:
            return (WalStoreState.MISSING, None, "ausente")
        except OSError as e:
            if e.errno in (errno.ELOOP, errno.EMLINK):
                return (WalStoreState.CORRUPT, None, "symlink recusado")
            return (WalStoreState.IO_ERROR, None, f"open errno {e.errno}")
        try:
            st_ = os.fstat(fd)
            if not stat.S_ISREG(st_.st_mode):
                return (WalStoreState.CORRUPT, None, "não é arquivo regular")
            if st_.st_size > max_bytes:                 # limite ANTES de alocar
                return (WalStoreState.RESOURCE_LIMIT, None, "excede o limite de tamanho")
            data = _pread_all(fd, st_.st_size)
            st2 = os.fstat(fd)
            if (st2.st_size != st_.st_size or st2.st_mtime_ns != st_.st_mtime_ns
                    or len(data) != st_.st_size):
                return (WalStoreState.CORRUPT, None, "alteração concorrente")
        except OSError as e:
            return (WalStoreState.IO_ERROR, None, f"read errno {e.errno}")
        finally:
            os.close(fd)
        return (WalStoreState.VALID, data, "ok")
