# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""horizon_bulk.py — BulkSnapshot indexado por Ordinal (V23-B1.1).

O bulk deixa de ser `dict[FactId, value]`: como Registry, residual e tombstones falam em **Ordinal**,
o bulk também. Isso elimina a ambiguidade em que "Registry conhece o fato mas o bulk não tem seu
valor" era confundido com "fato novo". Agora `lookup(ordinal)` distingue `VALUE`/`ABSENT`/
`OUT_OF_RANGE`, e a `BaseStateView` trata ABSENT-após-Registry como **corrupção** (BASE_ABSTAIN).

Layout canônico (little-endian):

    header: magic b"HBK1" | format_version u16 | value_kind u16 | scope_id u32
            generation_id u64 | fact_count u32 | support_count u32 | rank_block u32
    support_bitmap : ceil(fact_count/8) bytes  (bit ordinal = tem valor)
    rank_directory : u32 por bloco de rank_block bits (popcount acumulado)
    payload        : support_count bytes (raw u8, em ordem de rank)
    mac            : HMAC-SHA-256 (128 bits) sobre header+bitmap+rank+payload

Corrupção é detectada **no open** (MAC + invariantes canônicas); um snapshot já aberto nunca
produz `CORRUPT` por lookup. `build()` rejeita ordinal negativo/fora de faixa, valor fora de u8,
duplicação e limites — ANTES de acessar arrays (índice negativo do NumPy criaria alias silencioso).
"""

from __future__ import annotations

import hashlib
import hmac
import struct

import numpy as np

from horizon_memory._engine.residual_field import TAG_BYTES, IncompatibleError, IntegrityError

BULK_MAGIC = b"HBK1"
BULK_FORMAT_VERSION = 1
VALUE_KIND_U8 = 0
RANK_BLOCK = 512
MAX_FACT_COUNT = 1 << 30
_BH = struct.Struct("<4sHHIQIII")  # magic, ver, value_kind, scope, generation, fact_count, support, rank_block

# estados de lookup
BULK_VALUE = "value"
BULK_ABSENT = "absent"
BULK_OUT_OF_RANGE = "out_of_range"


class BulkSnapshot:
    def __init__(self, blob: bytes, key: bytes):
        self._blob = blob
        self._key = key
        self._parse()
        self._verify_and_validate()

    # ---------------- construção ----------------
    @classmethod
    def build(cls, fact_count: int, values: dict[int, int], scope_id: int, generation_id: int,
              key: bytes) -> "BulkSnapshot":
        if fact_count < 0 or fact_count > MAX_FACT_COUNT:
            raise IntegrityError("fact_count inválido")
        ordinals = sorted(values)
        seen = set()
        for o in ordinals:
            if not (0 <= o < fact_count):
                raise IntegrityError(f"ordinal fora de [0, fact_count): {o}")
            if o in seen:
                raise IntegrityError(f"ordinal duplicado: {o}")
            seen.add(o)
            v = values[o]
            if not (0 <= int(v) <= 0xFF):
                raise IntegrityError(f"valor fora de u8: {v}")
        bits = np.zeros(fact_count, dtype=np.uint8)
        for o in ordinals:
            bits[o] = 1
        bitmap = np.packbits(bits).tobytes() if fact_count else b""
        n_blocks = (fact_count + RANK_BLOCK - 1) // RANK_BLOCK
        rank_dir = np.zeros(n_blocks, dtype=np.uint32)
        acc = 0
        for blk in range(n_blocks):
            rank_dir[blk] = acc
            lo, hi = blk * RANK_BLOCK, min((blk + 1) * RANK_BLOCK, fact_count)
            acc += int(bits[lo:hi].sum())
        payload = np.array([values[o] & 0xFF for o in ordinals], dtype=np.uint8).tobytes()
        header = _BH.pack(BULK_MAGIC, BULK_FORMAT_VERSION, VALUE_KIND_U8, scope_id, generation_id,
                          fact_count, len(ordinals), RANK_BLOCK)
        mac = cls._mac(key, header, bitmap, rank_dir.tobytes(), payload)
        return cls(header + bitmap + rank_dir.tobytes() + payload + mac, key)

    @staticmethod
    def _mac(key, header, bitmap, rank_dir, payload) -> bytes:
        m = hmac.new(key, digestmod=hashlib.sha256)
        m.update(b"BULKSNAP")
        m.update(header)
        m.update(bitmap)
        m.update(rank_dir)
        m.update(payload)
        return m.digest()[:TAG_BYTES]

    # ---------------- parse + validação ----------------
    def _parse(self):
        b = self._blob
        if len(b) < _BH.size:
            raise IntegrityError("blob de bulk menor que o header")
        (magic, ver, kind, scope, gen, fact_count, support, rank_block) = _BH.unpack_from(b, 0)
        if magic != BULK_MAGIC:
            raise IntegrityError("magic de bulk inválido")
        if ver != BULK_FORMAT_VERSION:
            raise IncompatibleError(f"format_version de bulk não suportada: {ver}")
        if kind != VALUE_KIND_U8:
            raise IncompatibleError(f"value_kind não suportado: {kind}")
        if rank_block != RANK_BLOCK:
            raise IntegrityError("rank_block inesperado")
        if fact_count > MAX_FACT_COUNT or support > fact_count:
            raise IntegrityError("limites de bulk violados")
        self.scope_id, self.generation_id = scope, gen
        self.fact_count, self.support_count = fact_count, support
        off = _BH.size
        self._bitmap_off = off
        self._bitmap_len = (fact_count + 7) // 8
        off += self._bitmap_len
        self._rank_off = off
        self._rank_len = ((fact_count + RANK_BLOCK - 1) // RANK_BLOCK) * 4
        off += self._rank_len
        self._payload_off = off
        off += support
        self._mac_off = off
        if len(b) != off + TAG_BYTES:
            raise IntegrityError(f"comprimento de bulk não canônico: {len(b)} != {off + TAG_BYTES}")

    def _verify_and_validate(self):
        b = self._blob
        bitmap = b[self._bitmap_off:self._bitmap_off + self._bitmap_len]
        rank_dir = b[self._rank_off:self._rank_off + self._rank_len]
        payload = b[self._payload_off:self._payload_off + self.support_count]
        expected = self._mac(self._key, b[:_BH.size], bitmap, rank_dir, payload)
        if not hmac.compare_digest(expected, b[self._mac_off:self._mac_off + TAG_BYTES]):
            raise IntegrityError("MAC de bulk inválido")
        bits = np.unpackbits(np.frombuffer(bitmap, dtype=np.uint8)) if bitmap else np.zeros(0, np.uint8)
        if int(bits.sum()) != self.support_count:
            raise IntegrityError("popcount do bitmap != support_count")
        if bits.size > self.fact_count and int(bits[self.fact_count:].sum()) != 0:
            raise IntegrityError("bits de padding do bulk não nulos")
        rank = np.frombuffer(rank_dir, dtype="<u4")
        acc = 0
        for blk in range(self._rank_len // 4):
            if int(rank[blk]) != acc:
                raise IntegrityError("rank_directory do bulk inconsistente")
            lo, hi = blk * RANK_BLOCK, min((blk + 1) * RANK_BLOCK, self.fact_count)
            acc += int(bits[lo:hi].sum())

    # ---------------- consulta ----------------
    def _get_bit(self, ordinal: int) -> int:
        return (self._blob[self._bitmap_off + (ordinal >> 3)] >> (7 - (ordinal & 7))) & 1

    def _rank1(self, ordinal: int) -> int:
        blk = ordinal // RANK_BLOCK
        base = struct.unpack_from("<I", self._blob, self._rank_off + blk * 4)[0]
        cnt, i = 0, blk * RANK_BLOCK
        while i + 8 <= ordinal:
            cnt += int(bin(self._blob[self._bitmap_off + (i >> 3)]).count("1"))
            i += 8
        while i < ordinal:
            cnt += self._get_bit(i)
            i += 1
        return int(base) + cnt

    def lookup(self, ordinal: int):
        """Retorna (status, valor). status ∈ {value, absent, out_of_range}. Sem CORRUPT: a
        corrupção já foi barrada no open."""
        if ordinal < 0 or ordinal >= self.fact_count:
            return (BULK_OUT_OF_RANGE, None)
        if self._get_bit(ordinal) == 0:
            return (BULK_ABSENT, None)
        return (BULK_VALUE, int(self._blob[self._payload_off + self._rank1(ordinal)]))

    def support_ordinals(self) -> set[int]:
        bitmap = self._blob[self._bitmap_off:self._bitmap_off + self._bitmap_len]
        bits = np.unpackbits(np.frombuffer(bitmap, dtype=np.uint8)) if bitmap else np.zeros(0, np.uint8)
        return {int(o) for o in np.flatnonzero(bits[:self.fact_count])}

    def serialize(self) -> bytes:
        return self._blob

    @classmethod
    def try_open(cls, blob: bytes | None, key: bytes) -> "BulkSnapshot | None":
        if blob is None:
            return None
        try:
            return cls(blob, key)
        except (struct.error, IndexError, ValueError, IntegrityError, IncompatibleError):
            return None

    @property
    def n_bits(self) -> int:
        return len(self._blob) * 8
