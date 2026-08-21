# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""horizon_wal.py — WAL append-only durável (V23-A1 + V23-A1.1).

L0 durável keyed por **FactId**. Single writer, uma operação por frame, `write_all → fsync →
publica L0 → ACK`. A escrita só fica visível após o sync (group commit é V23-A2).

V23-A1.1 (contrato de durabilidade e idempotência) fecha lacunas da auditoria:
1. **`write_all` real** — laço que trata escrita parcial, `EINTR` e retorno 0; qualquer erro
   **envenena** (`POISONED`) o writer, que só volta após `close()`+recovery.
2. **Validar ANTES de escrever** — versão menor → `STALE_REJECTED`; versão igual + mesma
   (op,payload) → idempotente (sem novo frame); versão igual + conteúdo diferente →
   `VERSION_CONFLICT`. Só a operação realmente aplicável ganha `wal_seq`. Conflito nunca é
   persistido nem recebe ACK.
3. **Idempotência de retry** — `operation_id` de 128 bits com semântica: `TxIdIndex`
   (reconstruído do WAL) devolve o resultado original em replay de mesmo id+digest e recusa
   id igual com digest diferente (`TXID_CONFLICT`). Resolve `fsync feito → crash antes do ACK →
   cliente repete`.
4. **Parser canônico** — PUT tem payload de exatamente 4 bytes, DELETE de 0; `flags==0`;
   `fact_version≥1`; ids sob limite. Frame autêntico porém semanticamente inválido →
   `CORRUPT_COMMITTED_PREFIX`, nunca exceção.
5. **scan físico ≠ apply lógico** — o `scan` valida **todo** o intervalo declarado durável
   (corrupção após o `read_seq` também é detectada); o `apply` materializa só até `read_seq`.
   Índice parcial nunca é entregue quando a classificação final é CORRUPT/CONFLICT/INCOMPATIBLE.

Nuance selado vs ativo: um segmento **ACTIVE** usa o maior prefixo válido (um limite exato pode
ser ops nunca reconhecidas); um segmento **SEALED** (footer autenticado, ou declarado pelo
manifesto via `expected_seal`) exige cobertura total — qualquer prefixo menor é
`MISSING_COMMITTED_PREFIX`, mesmo terminando exatamente entre frames.

Limitações declaradas: single segment; `zlib.crc32` (**não** CRC32C); durabilidade provada
contra *process crash* — matar um subprocesso não prova perda de energia nem controladora mentindo
sobre `fsync` (ver PostgreSQL WAL reliability). Encadeamento multi-segmento e horizonte de dedup
através de compaction são V23-B.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

from horizon_memory._engine.horizon_store import (
    BASE_ABSENT,
    BASE_ABSTAIN,
    OP_DELETE,
    OP_PUT,
    EmptyBaseView,
    TxIdIndex,
    WalIndex,
    preflight,
)
from horizon_memory._engine.residual_field import TAG_BYTES

WAL_MAGIC = b"HWL1"
WAL_FORMAT_VERSION = 2                 # V23-A1.1: operation_id 128b + segment_state
RECORD_VERSION = 2
SEAL_MAGIC = b"WSEAL"
OP_CODE = {OP_PUT: 1, OP_DELETE: 2}
CODE_OP = {v: k for k, v in OP_CODE.items()}

STATE_ACTIVE = 0
STATE_SEALED = 1

# limites (validados ANTES de qualquer alocação/uso)
MAX_PAYLOAD = 1 << 20
PUT_PAYLOAD = 4                        # valor u32
MIN_FRAME = 8 + 44 + 4 + TAG_BYTES + 4  # head + fixed + crc + mac + trailer (sem payload)
MAX_FRAME = MIN_FRAME + MAX_PAYLOAD
MAX_RECORDS = 1 << 30
MAX_SEQ = 1 << 62
MAX_FACT_ID = 1 << 62

# segment header: magic, ver, scope, segment_id, first_seq, key_id, max_payload, max_records,
#                 segment_state, previous_segment_digest(16) ; + header_mac(16)
_SEG_HEADER = struct.Struct("<4sHIIQIIIB16s")
_FRAME_FIXED = struct.Struct("<HBBQ16sQII")  # rec_ver, op, flags, seq, op_id, fact_id, fact_version, payload_len
_SEAL = struct.Struct("<5sQQ16s")            # magic, last_seq, byte_length, digest ; + mac(16)
# tamanho FIXO do footer de selagem (fields + MAC) — o objeto SEALED é o prefixo ACTIVE + este footer.
# Exposto para a validação de ROTATION do C4 (sem literal espalhado): SEALED_len == prefix + FOOTER.
SEAL_FOOTER_BYTES = _SEAL.size + TAG_BYTES   # 37 + 16 = 53


class WalError(Exception):
    pass


def make_operation_id(client_id: int, txid: int) -> bytes:
    """operation_id de 128 bits: client_id||txid (evita colisão entre clientes)."""
    return struct.pack("<QQ", client_id, txid)


def content_digest(op: str, fact_id: int, fact_version: int, value: int | None) -> bytes:
    payload = b"" if value is None else struct.pack("<I", int(value))
    h = hashlib.sha256()
    h.update(b"OPDIGEST")
    h.update(struct.pack("<BQI", OP_CODE[op], fact_id, fact_version))
    h.update(payload)
    return h.digest()[:16]


# ------------------------------ codec ------------------------------
def _frame_mac(key: bytes, scope_id: int, segment_id: int, body_crc: bytes) -> bytes:
    m = hmac.new(key, digestmod=hashlib.sha256)
    m.update(b"WALFRAME")
    m.update(struct.pack("<II", scope_id, segment_id))
    m.update(body_crc)
    return m.digest()[:TAG_BYTES]


def encode_frame(key: bytes, scope_id: int, segment_id: int, op: str, wal_seq: int,
                 operation_id: bytes, fact_id: int, fact_version: int, payload: bytes = b"") -> bytes:
    if op not in OP_CODE:
        raise WalError(f"operação inválida: {op}")
    if len(operation_id) != 16:
        raise WalError("operation_id precisa ter 16 bytes")
    if len(payload) > MAX_PAYLOAD:
        raise WalError("payload acima do limite")
    fixed = _FRAME_FIXED.pack(RECORD_VERSION, OP_CODE[op], 0, wal_seq, operation_id, fact_id,
                              fact_version, len(payload))
    total_length = 8 + len(fixed) + len(payload) + 4 + TAG_BYTES + 4
    head = struct.pack("<II", total_length, total_length ^ 0xFFFFFFFF)
    body = head + fixed + payload
    crc = zlib.crc32(body) & 0xFFFFFFFF
    body_crc = body + struct.pack("<I", crc)
    mac = _frame_mac(key, scope_id, segment_id, body_crc)
    return body_crc + mac + struct.pack("<I", total_length)


def encode_segment_header(key: bytes, scope_id: int, segment_id: int, first_seq: int,
                          key_id: int = 0, segment_state: int = STATE_ACTIVE,
                          previous_segment_digest: bytes = b"\x00" * 16) -> bytes:
    fields = _SEG_HEADER.pack(WAL_MAGIC, WAL_FORMAT_VERSION, scope_id, segment_id, first_seq,
                              key_id, MAX_PAYLOAD, MAX_RECORDS, segment_state, previous_segment_digest)
    mac = hmac.new(key, b"WALSEGMENT" + fields, hashlib.sha256).digest()[:TAG_BYTES]
    return fields + mac


def _seal_footer(key: bytes, scope_id: int, segment_id: int, last_seq: int,
                 byte_length: int, prefix_digest: bytes) -> bytes:
    fields = _SEAL.pack(SEAL_MAGIC, last_seq, byte_length, prefix_digest)
    mac = hmac.new(key, b"WALSEAL" + struct.pack("<II", scope_id, segment_id) + fields,
                   hashlib.sha256).digest()[:TAG_BYTES]
    return fields + mac


@dataclass(frozen=True)
class SegmentSeal:
    """Declaração de selagem (do footer ou do manifesto): cobertura obrigatória do segmento."""
    last_seq: int
    byte_length: int
    prefix_digest: bytes


def _write_all(fd: int, data: bytes, write_fn=os.write, max_chunk: int | None = None) -> None:
    """write_all real: trata escrita parcial e EINTR; retorno ≤0 é falha."""
    mv = memoryview(data)
    total = 0
    n = len(data)
    while total < n:
        chunk = mv[total:] if max_chunk is None else mv[total:total + max_chunk]
        try:
            written = write_fn(fd, chunk)
        except InterruptedError:      # EINTR — reintenta
            continue
        if written is None or written <= 0:
            raise WalError("os.write retornou 0/negativo (disco cheio?)")
        total += written


# ------------------------------ append result ------------------------------
APPLIED = "APPLIED"
IDEMPOTENT = "IDEMPOTENT"
STALE_REJECTED = "STALE_REJECTED"
VERSION_CONFLICT = "VERSION_CONFLICT"
DEDUP_REPLAY = "DEDUP_REPLAY"
TXID_CONFLICT = "TXID_CONFLICT"
IDEMPOTENCY_EXPIRED = "IDEMPOTENCY_EXPIRED"   # B4.2: txid abaixo do retained_floor / conhecido-mas-removido
POISONED = "POISONED"
INVALID_ARGUMENT = "INVALID_ARGUMENT"   # erro de cliente: comando não canônico (não envenena)

MAX_U8_VALUE = 255                      # domínio FROZEN do valor (bulk/residual armazenam u8)


def is_u8_value(value) -> bool:
    """Domínio congelado do valor da Horizon Memory: u8 [0,255]. O WAL físico ainda empacota u32, mas o
    ingresso do store compaction-aware/standalone recusa qualquer valor fora de u8 — nunca ACKa um write
    impossível de compactar (a base é u8)."""
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_U8_VALUE


def _command_is_canonical(op: str, fact_id: int, fact_version: int, value: int | None,
                          operation_id: bytes) -> bool:
    """Contrato de ingresso: PUT exige value (u32); DELETE exige value None; ids e versão válidos."""
    if op not in OP_CODE:
        return False
    if len(operation_id) != 16:
        return False
    if fact_version < 1:
        return False
    if not (0 <= fact_id < MAX_FACT_ID):
        return False
    if op == OP_PUT and (value is None or not (0 <= int(value) <= 0xFFFFFFFF)):
        return False
    if op == OP_DELETE and value is not None:
        return False
    return True


@dataclass(frozen=True)
class AppendResult:
    status: str
    wal_seq: int | None


# ------------------------------ writer ------------------------------
class WalWriter:
    """Single writer, com validação ANTES de escrever e estado POISONED após erro."""

    def __init__(self, fd: int, path: str, key: bytes, scope_id: int, segment_id: int,
                 first_seq: int, next_seq: int, index: WalIndex, txindex: TxIdIndex,
                 max_write_chunk: int | None, key_id: int = 0,
                 previous_segment_digest: bytes = b"\x00" * 16):
        """Construtor interno — use `create_new` ou `resume_existing`."""
        self._fd = fd
        self.path = path
        self.key = key
        self.scope_id = scope_id
        self.segment_id = segment_id
        self.first_seq = first_seq
        self.key_id = key_id                        # identidade multissegmento (B3-0/B3-1)
        self.previous_segment_digest = previous_segment_digest
        self._next_seq = next_seq
        self._max_chunk = max_write_chunk
        self.index = index
        self.txindex = txindex
        self._poisoned = False
        self._sealed = False

    @classmethod
    def create_new(cls, path: str, key: bytes, scope_id: int, segment_id: int = 1,
                   first_seq: int = 1, max_write_chunk: int | None = None, *, key_id: int = 0,
                   previous_segment_digest: bytes = b"\x00" * 16) -> "WalWriter":
        """Cria um segmento NOVO. `O_CREAT|O_EXCL` — nunca `O_TRUNC`, jamais clobbera um WAL. O header
        carrega a identidade multissegmento completa (`key_id`, `previous_segment_digest`) que o
        binding físico do B3-0 exige — sem defaults escondidos numa camada acima."""
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        w = cls(fd, path, key, scope_id, segment_id, first_seq, first_seq, WalIndex(), TxIdIndex(),
                max_write_chunk, key_id=key_id, previous_segment_digest=previous_segment_digest)
        try:
            _write_all(fd, encode_segment_header(key, scope_id, segment_id, first_seq, key_id=key_id,
                                                 previous_segment_digest=previous_segment_digest),
                       max_chunk=max_write_chunk)
            os.fsync(fd)
        except Exception:
            w._poisoned = True
            raise
        return w

    @classmethod
    def resume_existing(cls, path: str, key: bytes, scope_id: int, segment_id: int = 1,
                        max_write_chunk: int | None = None, *, expected_key_id: int | None = None,
                        expected_previous_segment_digest: bytes | None = None) -> "WalWriter":
        """Retoma um segmento ACTIVE: valida fisicamente, trunca a cauda incompleta, restaura L0,
        TxIdIndex e `_next_seq = last_seq+1`. Segmento SEALED nunca é retomável.

        A identidade (`first_seq`, `key_id`, `previous_segment_digest`) é lida do header AUTENTICADO
        (`sr.header`) — nunca reconstruída com defaults; se o chamador passar valores esperados, eles
        são conferidos e uma divergência é fail-closed."""
        blob = Path(path).read_bytes()
        sr = scan(blob, key, required=True, scope_id=scope_id, segment_id=segment_id)
        if sr.sealed:
            raise WalError("segmento SEALED não pode ser retomado")
        if sr.classification not in (CLEAN, TAIL_DROPPED):
            raise WalError(f"não é possível retomar: {sr.classification}")
        h = sr.header
        if h is None:
            raise WalError("header não autenticado ao retomar")
        if expected_key_id is not None and h.key_id != expected_key_id:
            raise WalError("key_id do header diverge do esperado")
        if (expected_previous_segment_digest is not None
                and not hmac.compare_digest(h.previous_segment_digest,
                                            expected_previous_segment_digest)):
            raise WalError("previous_segment_digest do header diverge do esperado")
        # reconstrói L0 + TxIdIndex pelo MESMO reducer que o recover usa (autoridade única)
        rr = reduce_frames(sr.frames)
        if rr.classification != "OK":
            raise WalError(f"história não canônica ao retomar: {rr.classification}")
        index, txindex = rr.index, rr.txindex
        fd = os.open(path, os.O_WRONLY)           # sem O_TRUNC
        if sr.classification == TAIL_DROPPED:      # descarta a cauda incompleta e persiste
            os.ftruncate(fd, sr.consumed_bytes)
            os.fsync(fd)
        os.lseek(fd, sr.consumed_bytes, os.SEEK_SET)
        return cls(fd, path, key, scope_id, segment_id, h.first_seq, sr.last_seq + 1,
                   index, txindex, max_write_chunk, key_id=h.key_id,
                   previous_segment_digest=h.previous_segment_digest)

    @property
    def poisoned(self) -> bool:
        return self._poisoned

    def append(self, op: str, fact_id: int, fact_version: int, value: int | None,
               operation_id: bytes) -> AppendResult:
        if self._poisoned:
            return AppendResult(POISONED, None)
        # 1) ingresso canônico ANTES do preflight/reserva de seq: erro de cliente não toca o WAL,
        #    _next_seq, L0 nem TxIdIndex, e não envenena o writer.
        if not _command_is_canonical(op, fact_id, fact_version, value, operation_id):
            return AppendResult(INVALID_ARGUMENT, None)
        if self._sealed:
            raise WalError("segmento selado; não aceita novas escritas")
        digest = content_digest(op, fact_id, fact_version, value)

        # 3) idempotência de retry por operation_id
        tx = self.txindex.check(operation_id, digest)
        if tx == "dedup_replay":
            return AppendResult(DEDUP_REPLAY, self.txindex.get_seq(operation_id))
        if tx == "txid_conflict":
            return AppendResult(TXID_CONFLICT, None)

        # 2) validação de versão ANTES de escrever qualquer byte
        cur = self.index.get(fact_id)
        if cur is not None:
            if fact_version < cur[0]:
                return AppendResult(STALE_REJECTED, None)
            if fact_version == cur[0]:
                if (op, value) == (cur[1], cur[2]):
                    return AppendResult(IDEMPOTENT, None)
                return AppendResult(VERSION_CONFLICT, None)

        # aplicável: reserva seq, serializa, escreve, sincroniza, publica, ACK
        seq = self._next_seq
        payload = b"" if value is None else struct.pack("<I", int(value))
        frame = encode_frame(self.key, self.scope_id, self.segment_id, op, seq, operation_id,
                             fact_id, fact_version, payload)
        try:
            _write_all(self._fd, frame, max_chunk=self._max_chunk)
            os.fsync(self._fd)                        # ponto de durabilidade
        except Exception:
            self._poisoned = True                     # não continua no mesmo descritor
            raise
        self.index.apply(fact_id, fact_version, op, value)   # publica L0
        self.txindex.record(operation_id, digest, seq)
        self._next_seq += 1
        return AppendResult(APPLIED, seq)             # ACK

    def seal(self) -> None:
        """Sela o segmento: footer autenticado com last_seq, byte_length e digest do prefixo."""
        if self._poisoned:
            raise WalError("writer POISONED")
        os.fsync(self._fd)
        byte_length = os.lseek(self._fd, 0, os.SEEK_CUR)
        prefix = Pathlike_read(self.path, byte_length)
        digest = hashlib.sha256(prefix).digest()[:16]
        footer = _seal_footer(self.key, self.scope_id, self.segment_id, self._next_seq - 1,
                              byte_length, digest)
        try:
            _write_all(self._fd, footer, max_chunk=self._max_chunk)
            os.fsync(self._fd)
        except Exception:
            self._poisoned = True
            raise
        self._sealed = True

    def close(self):
        os.close(self._fd)


def Pathlike_read(path: str, n: int) -> bytes:
    with open(path, "rb") as fh:
        return fh.read(n)


# ------------------------------ scan (físico) ------------------------------
CLEAN = "CLEAN"
TAIL_DROPPED = "TAIL_DROPPED"
CORRUPT_COMMITTED_PREFIX = "CORRUPT_COMMITTED_PREFIX"
MISSING_REQUIRED = "MISSING_REQUIRED"
MISSING_COMMITTED_PREFIX = "MISSING_COMMITTED_PREFIX"
INCOMPATIBLE = "INCOMPATIBLE"
CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class WalHeaderInfo:
    """Metadados AUTENTICADOS do header do segmento (V23-B3-0). O `scan` deixou de descartá-los: o
    manifesto tem que casar TODOS com o `WalSegmentDescriptor` (binding físico ↔ declarado)."""
    scope_id: int
    segment_id: int                 # identidade PERSISTENTE do arquivo (não a posição no manifesto)
    first_seq: int
    key_id: int
    previous_segment_digest: bytes  # 16 bytes: cadeia embutida no próprio WAL
    format_version: int


@dataclass
class ScanResult:
    classification: str
    frames: list          # [(op, seq, operation_id, fact_id, fact_version, value)]
    last_seq: int
    consumed_bytes: int
    dropped_tail_bytes: int
    sealed: bool
    header: WalHeaderInfo | None = None   # presente sempre que o header físico foi autenticado


def _value_of(payload: bytes) -> int:
    return int(struct.unpack_from("<I", payload, 0)[0])


def _canonical_frame_ok(op_code, flags, seq, fact_id, fver, plen) -> bool:
    if op_code not in CODE_OP:
        return False
    if flags != 0:
        return False
    op = CODE_OP[op_code]
    if op == OP_PUT and plen != PUT_PAYLOAD:
        return False
    if op == OP_DELETE and plen != 0:
        return False
    if fver < 1:
        return False
    if not (0 <= seq < MAX_SEQ):
        return False
    if not (0 <= fact_id < MAX_FACT_ID):
        return False
    return True


def scan(blob: bytes | None, key: bytes, *, required: bool, scope_id: int, segment_id: int = 1,
         expected_seal: SegmentSeal | None = None) -> ScanResult:
    """Valida FISICAMENTE todo o intervalo declarado durável (independe de read_seq)."""
    if blob is None:
        return ScanResult(MISSING_REQUIRED if required else CLEAN, [], 0, 0, 0, False)
    if len(blob) < _SEG_HEADER.size + TAG_BYTES:
        return ScanResult(CORRUPT_COMMITTED_PREFIX, [], 0, 0, 0, False)
    hdr = blob[:_SEG_HEADER.size]
    magic, ver, scope, seg, first_seq, _kid, maxp, maxr, seg_state, _prev = _SEG_HEADER.unpack(hdr)
    if magic != WAL_MAGIC:
        return ScanResult(CORRUPT_COMMITTED_PREFIX, [], 0, 0, 0, False)
    if ver != WAL_FORMAT_VERSION:
        return ScanResult(INCOMPATIBLE, [], 0, 0, 0, False)
    exp = hmac.new(key, b"WALSEGMENT" + hdr, hashlib.sha256).digest()[:TAG_BYTES]
    if not hmac.compare_digest(exp, blob[_SEG_HEADER.size:_SEG_HEADER.size + TAG_BYTES]):
        return ScanResult(CORRUPT_COMMITTED_PREFIX, [], 0, 0, 0, False)
    if scope != scope_id or seg != segment_id:
        return ScanResult(CORRUPT_COMMITTED_PREFIX, [], 0, 0, 0, False)
    if maxp > MAX_PAYLOAD or maxr > MAX_RECORDS or seg_state not in (STATE_ACTIVE, STATE_SEALED):
        return ScanResult(CORRUPT_COMMITTED_PREFIX, [], 0, 0, 0, False)

    # header autenticado: exponha os metadados físicos para o binding com o descriptor (B3-0)
    hi = WalHeaderInfo(scope, seg, first_seq, _kid, bytes(_prev), ver)

    frames = []
    off = _SEG_HEADER.size + TAG_BYTES
    n = len(blob)
    expected_seq = first_seq
    last_seq = first_seq - 1
    sealed = False
    tail_bytes = 0

    while off < n:
        remaining = n - off
        # footer de selagem?
        if remaining >= 5 and blob[off:off + 5] == SEAL_MAGIC:
            if remaining < _SEAL.size + TAG_BYTES:
                tail_bytes = remaining; break               # seal rasgado → cauda
            sfields = blob[off:off + _SEAL.size]
            s_magic, s_last, s_len, s_dig = _SEAL.unpack(sfields)
            s_mac = blob[off + _SEAL.size:off + _SEAL.size + TAG_BYTES]
            exp_s = hmac.new(key, b"WALSEAL" + struct.pack("<II", scope_id, segment_id) + sfields,
                             hashlib.sha256).digest()[:TAG_BYTES]
            if not hmac.compare_digest(exp_s, s_mac):
                return ScanResult(CORRUPT_COMMITTED_PREFIX, frames, last_seq, off, 0, False)
            if off + _SEAL.size + TAG_BYTES != n:
                return ScanResult(CORRUPT_COMMITTED_PREFIX, frames, last_seq, off, 0, False)  # lixo após o seal
            if (s_len != off or s_last != last_seq
                    or not hmac.compare_digest(s_dig, hashlib.sha256(blob[:off]).digest()[:16])):
                return ScanResult(CORRUPT_COMMITTED_PREFIX, frames, last_seq, off, 0, False)
            # autoridade do manifesto: se `expected_seal` foi declarado, o footer TEM que casar com
            # ele; footer ≠ declaração → fail-closed (não se aceita cobertura divergente).
            if expected_seal is not None and (
                    s_last != expected_seal.last_seq or s_len != expected_seal.byte_length
                    or not hmac.compare_digest(s_dig, expected_seal.prefix_digest)):
                return ScanResult(MISSING_COMMITTED_PREFIX, [], last_seq, off, 0, False)
            sealed = True
            off = n
            break
        if remaining < 8:
            tail_bytes = remaining; break
        total_length, comp = struct.unpack_from("<II", blob, off)
        if (total_length ^ 0xFFFFFFFF) != comp:
            return ScanResult(CORRUPT_COMMITTED_PREFIX, frames, last_seq, off, 0, False)
        if not (MIN_FRAME <= total_length <= MAX_FRAME):
            return ScanResult(CORRUPT_COMMITTED_PREFIX, frames, last_seq, off, 0, False)
        if remaining < total_length:
            tail_bytes = remaining; break
        frame = blob[off:off + total_length]
        if struct.unpack_from("<I", frame, total_length - 4)[0] != total_length:
            return ScanResult(CORRUPT_COMMITTED_PREFIX, frames, last_seq, off, 0, False)
        rec_ver, opc, flags, seq, op_id, fact_id, fver, plen = _FRAME_FIXED.unpack_from(frame, 8)
        if rec_ver != RECORD_VERSION or plen != total_length - MIN_FRAME:
            return ScanResult(CORRUPT_COMMITTED_PREFIX, frames, last_seq, off, 0, False)
        body = frame[:8 + _FRAME_FIXED.size + plen]
        crc = struct.unpack_from("<I", frame, 8 + _FRAME_FIXED.size + plen)[0]
        if (zlib.crc32(body) & 0xFFFFFFFF) != crc:
            return ScanResult(CORRUPT_COMMITTED_PREFIX, frames, last_seq, off, 0, False)
        mac_off = 8 + _FRAME_FIXED.size + plen + 4
        if not hmac.compare_digest(_frame_mac(key, scope_id, segment_id, frame[:mac_off]),
                                   frame[mac_off:mac_off + TAG_BYTES]):
            return ScanResult(CORRUPT_COMMITTED_PREFIX, frames, last_seq, off, 0, False)
        if seq != expected_seq:
            return ScanResult(CORRUPT_COMMITTED_PREFIX, frames, last_seq, off, 0, False)
        # parser canônico: autêntico mas semanticamente inválido → CORRUPT (nunca exceção)
        if not _canonical_frame_ok(opc, flags, seq, fact_id, fver, plen):
            return ScanResult(CORRUPT_COMMITTED_PREFIX, frames, last_seq, off, 0, False)
        payload = frame[8 + _FRAME_FIXED.size:8 + _FRAME_FIXED.size + plen]
        value = _value_of(payload) if CODE_OP[opc] == OP_PUT else None
        frames.append((CODE_OP[opc], seq, bytes(op_id), fact_id, fver, value))
        last_seq = seq
        expected_seq += 1
        off += total_length

    tail = off < n
    classification = TAIL_DROPPED if tail else CLEAN
    # footer de selagem embutido → cobertura total do blob já provada acima
    if sealed:
        return ScanResult(CLEAN, frames, last_seq, off, 0, True, hi)
    # selagem declarada externamente (manifesto): cobertura EXATA. Um segmento selado não aceita
    # frames adicionais nem bytes a mais — comprimento, last_seq e digest têm que bater exatamente.
    if expected_seal is not None:
        covers = (not tail and n == expected_seal.byte_length
                  and last_seq == expected_seal.last_seq
                  and hmac.compare_digest(
                      hashlib.sha256(blob[:expected_seal.byte_length]).digest()[:16],
                      expected_seal.prefix_digest))
        if not covers:
            return ScanResult(MISSING_COMMITTED_PREFIX, [], last_seq, off, 0, False)
    return ScanResult(classification, frames, last_seq, off, tail_bytes, False, hi)


# ------------------------------ apply (lógico) — reducer ÚNICO ------------------------------
@dataclass
class ReplayResult:
    classification: str        # "OK" | CONFLICT | TXID_CONFLICT
    index: WalIndex
    txindex: TxIdIndex
    applied_count: int
    applied_seq: int


# selo privado dos tokens Validated* — só o WalStore o carimba; o reducer recusa tokens sem ele.
WAL_SEGMENT_SEAL = object()

# classificações de replay (as duas primeiras preservam a semântica legada de reduce_frames)
REDUCE_OK = "OK"
# CONFLICT / TXID_CONFLICT já definidos acima
REDUCE_COVERAGE = "COVERAGE"
REDUCE_LIMIT = "LIMIT"
REDUCE_SEAL = "SEAL"
REDUCE_STATE = "STATE"


@dataclass(frozen=True)
class ReducerLimits:
    max_segments: int
    max_total_frames: int
    max_total_wal_bytes: int
    max_frames_per_segment: int
    max_segment_bytes: int


UNBOUNDED_REDUCER_LIMITS = ReducerLimits(1 << 62, 1 << 62, 1 << 62, 1 << 62, 1 << 62)


@dataclass(frozen=True)
class ReducerStepResult:
    ok: bool
    classification: str
    reason: str


class ReducerState:
    """A ÚNICA autoridade de replay lógico — agora incremental e streaming. `recover`,
    `resume_existing` e `open_generation` usam a MESMA máquina.

    Garantia central: os builders internos são INACESSÍVEIS antes de `finish()`. Um conflito no
    último frame, depois de milhares válidos, leva o reducer a um estado TERMINAL, descarta os
    builders, e nenhuma chamada posterior devolve índices — sem rollback nem clonagem por segmento
    (que recriariam o custo do A2). O pico de memória depende do MAIOR segmento (o chamador libera a
    referência após cada `feed_segment`), não da soma."""

    def __init__(self, base, initial_seq, target_seq, limits, wal_factory, tx_factory):
        self._base = base or EmptyBaseView()
        self._wal_factory = wal_factory
        self._tx_factory = tx_factory
        self._wal_b = wal_factory().begin_mutation()
        self._tx_b = tx_factory().begin_mutation()
        self._initial_seq = initial_seq
        self._target_seq = target_seq
        self._expected_next = initial_seq + 1
        self._applied = 0
        self._last_applied = initial_seq
        self._limits = limits
        self._seg_count = 0
        self._prev_segment_id = -1
        self._total_frames = 0
        self._total_bytes = 0
        self._terminal = False
        self._classification = REDUCE_OK
        self._reason = ""
        self._finished = False
        self._legacy = False

    @classmethod
    def begin(cls, *, base, initial_seq: int, target_seq: int, limits: ReducerLimits,
              wal_index_factory=WalIndex, tx_index_factory=TxIdIndex) -> "ReducerState":
        return cls(base, initial_seq, target_seq, limits, wal_index_factory, tx_index_factory)

    def _to_terminal(self, classification, reason) -> ReducerStepResult:
        self._terminal = True
        self._classification = classification
        self._reason = reason
        self._wal_b = None          # descarta os builders: inacessíveis após falha
        self._tx_b = None
        return ReducerStepResult(False, classification, reason)

    def _apply_one(self, op, seq, op_id, fact_id, fver, value):
        """Aplica um frame contra base+L0. (classification, reason) em falha; None em sucesso."""
        digest = content_digest(op, fact_id, fver, value)
        if self._tx_b.check(op_id, digest) != "new":         # operation_id repetido DENTRO do WAL
            return (TXID_CONFLICT, "operation_id repetido")
        # B4.2: MESMA autoridade do ingresso — um frame DURÁVEL nunca pode ter um operation_id que a base
        # já retém (o ingresso o teria deduplicado). Se acontece, é colisão base×WAL → fail-closed, jamais
        # overwrite silencioso. `dedup_probe` da base vazia/EmptyBaseView devolve "new" (sem custo).
        dprobe = getattr(self._base, "dedup_probe", None)
        if dprobe is not None and dprobe(op_id, digest)[0] != "new":
            return (TXID_CONFLICT, "operation_id já retido na base (colisão base×WAL)")
        cur = self._wal_b.get(fact_id)                        # L0 primeiro, senão a base
        if cur is None:
            bf = self._base.lookup(fact_id)
            if bf is BASE_ABSTAIN:                            # base ilegível → fail-closed
                return (CONFLICT, "base ilegível")
            if bf is not BASE_ABSENT:
                cur = (bf.version, bf.op, bf.value)
        if preflight(cur, op, fver, value) != "applied":      # stale/idempotente/conflito
            return (CONFLICT, "stale/idempotente/conflito")
        self._wal_b.apply(fact_id, fver, op, value)
        self._tx_b.record(op_id, digest, seq)
        self._applied += 1
        self._last_applied = seq
        return None

    def feed_segment(self, segment, descriptor) -> ReducerStepResult:
        if self._terminal:
            return ReducerStepResult(False, self._classification, self._reason)   # idempotente
        if self._finished:
            return self._to_terminal(REDUCE_STATE, "feed após finish")
        if getattr(segment, "seal", None) is not WAL_SEGMENT_SEAL:
            return self._to_terminal(REDUCE_SEAL, "token não selado pelo WalStore")
        frames = segment.frames
        # invariantes de segmento vs descriptor
        if descriptor.ordinal != self._seg_count:
            return self._to_terminal(REDUCE_COVERAGE, "ordinal fora de ordem")
        if descriptor.segment_id <= self._prev_segment_id:
            return self._to_terminal(REDUCE_COVERAGE, "segment_id não-monotônico")
        if descriptor.first_seq != self._expected_next:
            return self._to_terminal(REDUCE_COVERAGE, "first_seq != expected (gap/overlap/regressão)")
        if len(frames) != descriptor.record_count:
            return self._to_terminal(REDUCE_COVERAGE, "record_count divergente")
        # limites (aritmética segura ANTES de tocar os builders)
        if self._seg_count + 1 > self._limits.max_segments:
            return self._to_terminal(REDUCE_LIMIT, "max_segments")
        if len(frames) > self._limits.max_frames_per_segment:
            return self._to_terminal(REDUCE_LIMIT, "max_frames_per_segment")
        if segment.byte_length > self._limits.max_segment_bytes:
            return self._to_terminal(REDUCE_LIMIT, "max_segment_bytes")
        if self._total_frames + len(frames) > self._limits.max_total_frames:
            return self._to_terminal(REDUCE_LIMIT, "max_total_frames")
        if self._total_bytes + segment.byte_length > self._limits.max_total_wal_bytes:
            return self._to_terminal(REDUCE_LIMIT, "max_total_wal_bytes")
        for fr in frames:
            op, seq, op_id, fact_id, fver, value = fr
            if seq != self._expected_next:
                return self._to_terminal(REDUCE_COVERAGE, "seq fora de ordem")
            if seq > self._target_seq:
                return self._to_terminal(REDUCE_COVERAGE, "frame excede target_seq")
            err = self._apply_one(op, seq, op_id, fact_id, fver, value)
            if err is not None:
                return self._to_terminal(err[0], err[1])
            self._expected_next += 1
        self._seg_count += 1
        self._prev_segment_id = descriptor.segment_id
        self._total_frames += len(frames)
        self._total_bytes += segment.byte_length
        return ReducerStepResult(True, REDUCE_OK, "ok")

    def feed_legacy_frames(self, frames: list, upto_seq: int | None = None) -> ReducerStepResult:
        """Compatibilidade (recover/resume): frames planos, sem invariantes de segmento/cobertura;
        aplica até `upto_seq` com a MESMA lógica base-aware + txid."""
        if self._terminal:
            return ReducerStepResult(False, self._classification, self._reason)
        self._legacy = True
        for fr in frames:
            op, seq, op_id, fact_id, fver, value = fr
            if upto_seq is not None and seq > upto_seq:
                break
            err = self._apply_one(op, seq, op_id, fact_id, fver, value)
            if err is not None:
                return self._to_terminal(err[0], err[1])
        return ReducerStepResult(True, REDUCE_OK, "ok")

    def finish(self) -> ReplayResult:
        if self._terminal:
            return ReplayResult(self._classification, self._wal_factory(), self._tx_factory(), 0, 0)
        if self._finished:
            self._to_terminal(REDUCE_STATE, "finish duplicado")
            return ReplayResult(REDUCE_STATE, self._wal_factory(), self._tx_factory(), 0, 0)
        if not self._legacy and self._expected_next != self._target_seq + 1:
            self._to_terminal(REDUCE_COVERAGE, "cobertura incompleta")
            return ReplayResult(REDUCE_COVERAGE, self._wal_factory(), self._tx_factory(), 0, 0)
        index = self._wal_b.freeze()
        txindex = self._tx_b.freeze()
        self._finished = True
        self._wal_b = None
        self._tx_b = None
        return ReplayResult(REDUCE_OK, index, txindex, self._applied, self._last_applied)


def reduce_frames(frames: list, upto_seq: int | None = None,
                  wal_index_factory=WalIndex, tx_index_factory=TxIdIndex,
                  base=None, initial_seq: int = 0) -> ReplayResult:
    """Wrapper de compatibilidade sobre `ReducerState` — a MESMA autoridade usada por
    `open_generation`. Preserva a semântica legada: aplica frames planos até `upto_seq`, e qualquer
    frame stale/idempotente/conflitante ou `operation_id` repetido é corrupção semântica
    (fail-closed, sem estado parcial)."""
    reducer = ReducerState.begin(base=base, initial_seq=initial_seq, target_seq=1 << 62,
                                 limits=UNBOUNDED_REDUCER_LIMITS,
                                 wal_index_factory=wal_index_factory, tx_index_factory=tx_index_factory)
    reducer.feed_legacy_frames(frames, upto_seq)
    return reducer.finish()


@dataclass
class RecoveryResult:
    classification: str
    index: WalIndex
    txindex: TxIdIndex
    applied_count: int
    applied_seq: int
    dropped_tail_bytes: int


def recover(blob: bytes | None, key: bytes, *, required: bool, scope_id: int, segment_id: int = 1,
            upto_seq: int | None = None, expected_seal: SegmentSeal | None = None) -> RecoveryResult:
    """Compõe scan (físico, todo o range) + `reduce_frames` (lógico, até read_seq). Índice parcial
    NUNCA é entregue quando a classificação é CORRUPT/CONFLICT/INCOMPATIBLE/MISSING_*."""
    empty = WalIndex()
    sr = scan(blob, key, required=required, scope_id=scope_id, segment_id=segment_id,
              expected_seal=expected_seal)
    if sr.classification in (CORRUPT_COMMITTED_PREFIX, INCOMPATIBLE, MISSING_REQUIRED,
                             MISSING_COMMITTED_PREFIX):
        return RecoveryResult(sr.classification, empty, TxIdIndex(), 0, 0, sr.dropped_tail_bytes)
    rr = reduce_frames(sr.frames, upto_seq)
    if rr.classification != "OK":
        return RecoveryResult(rr.classification, empty, TxIdIndex(), 0, 0, 0)
    return RecoveryResult(sr.classification, rr.index, rr.txindex, rr.applied_count,
                          rr.applied_seq, sr.dropped_tail_bytes)
