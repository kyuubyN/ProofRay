# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""ResidualField — blob residual REAL, serializado e consultável (V20.1).

V20 provou o modelo de custo com um `set` Python e overhead estimado. Aqui a estrutura existe
como bytes de verdade e o `lookup` navega SOMENTE o blob: `fact_id → ordinal → bitmap[ordinal]
→ rank1 → payload[index]`. Sem `set`, sem lista de correções, sem valores verdadeiros externos.

Layout (little-endian):

    header:
        magic b"HRF1" (4) | format_version u16 | scope_id u32 | bulk_epoch u32
        fact_count u32 | page_size_bytes u32 | n_corrections u32 | payload_kind u8 (0=raw_u8)
    support_bitmap: ceil(fact_count/8) bytes  (bit i = fato i tem resíduo)
    rank_directory: u32 por bloco de RANK_BLOCK bits (popcount acumulado)
    payload: n_corrections bytes (raw u8), particionados em páginas de page_size_bytes
    page_tags: uma tag HMAC-SHA-256 (128 bits) por página de payload
    manifest_tag: HMAC-SHA-256 (128 bits) sobre header + bitmap + rank_directory + page_tags

Integridade (HMAC-SHA-256, chave por sessão, `hmac.compare_digest`):
- cada page_tag cobre {format_version, scope, epoch, page_index, bytes da página};
- o manifest_tag cobre {header, bitmap, rank_directory, todas as page_tags} — assim corromper
  o bitmap ou o rank_directory é detectado (senão um payload íntegro seria ligado ao fato errado).

O truncamento de tag para 128 bits é um parâmetro de segurança explícito (NIST SP 800-224);
o risco criptográfico é publicado como limite teórico, não "zero".
"""

from __future__ import annotations

import hashlib
import hmac
import struct
import warnings
from dataclasses import dataclass
from typing import NewType

import numpy as np

MAGIC = b"HRF1"
FORMAT_VERSION = 1
RANK_BLOCK = 512            # bits por amostra do rank_directory
TAG_BYTES = 16             # 128 bits
_HEADER = struct.Struct("<4sHIIIIIB")  # magic, ver, scope, epoch, fact_count, page_size, n_corr, kind
PAYLOAD_RAW_U8 = 0

# --- identidades distintas (V23.0) ---------------------------------------------------------
# fact_id == ordinal é aceitável no substrato, mas NÃO pode atravessar V23: uma compaction que
# reorganize ordinais ligaria um resíduo íntegro ao fato errado. Estes NewType separam os papéis
# em tempo de tipagem (custo zero em runtime); o registro FactId→ordinal é autenticado (ver
# horizon_store.FactRegistry) e seu custo entra no ledger bruto.
FactId = NewType("FactId", int)          # identidade estável entre gerações
FactVersion = NewType("FactVersion", int)  # versão lógica do conteúdo do fato
Ordinal = NewType("Ordinal", int)        # posição local, válida só naquela geração
WalSeq = NewType("WalSeq", int)          # ordem global das mutações
GenerationId = NewType("GenerationId", int)  # snapshot físico imutável

# --- vocabulário de status (V23.0) ---------------------------------------------------------
# separar "sem resíduo" de "apagado" é obrigatório: se ambos virarem clean_miss, o consumidor
# consulta o bulk num fato apagado e o valor ressuscita.
CORRECT = "correct"            # valor residual verificado (autoritativo)
NO_RESIDUAL = "no_residual"    # não há correção; PODE consultar o bulk
DELETED = "deleted"            # TERMINAL: nunca consultar bulk/L1/cold store
FALLBACK = "fallback"          # candidato não autoritativo (campo ausente/ilegível → bulk-base)
ABSTAIN_INTEGRITY = "abstain_integrity"
ABSTAIN_SCOPE = "abstain_scope"
ABSTAIN_EPOCH = "abstain_epoch"
OUT_OF_RANGE = "out_of_range"


class IntegrityError(Exception):
    pass


class IncompatibleError(Exception):
    """Versão/formato não suportado — distinto de corrupção (CORRUPT vs INCOMPATIBLE)."""


def _popcount_bytes(b: bytes) -> int:
    return int(np.unpackbits(np.frombuffer(b, dtype=np.uint8)).sum())


class ResidualField:
    """Construção em memória; `serialize()` produz o blob; `deserialize()` reabre só dos bytes."""

    def __init__(self, blob: bytes, key: bytes):
        self._blob = blob
        self._key = key
        self._open_valid = False
        self._parse()               # estrutura canônica (antes de qualquer divisão/MAC)
        self._verify_and_validate() # autenticidade (manifesto) + invariantes semânticas
        self._open_valid = True     # OPEN_VALID: lookups só verificam a página consultada

    # ---------------- construção ----------------
    @classmethod
    def build(cls, fact_count: int, corrections: dict[int, int], page_size_bytes: int,
              scope_id: int, bulk_epoch: int, key: bytes) -> "ResidualField":
        ordinals = sorted(corrections)
        n = len(ordinals)
        # valida ordinais ANTES de indexar (índice negativo do NumPy criaria alias no fim do vetor)
        for o in ordinals:
            if not (0 <= o < fact_count):
                raise IntegrityError(f"ordinal fora de [0, fact_count): {o}")
        # bitmap
        bits = np.zeros(fact_count, dtype=np.uint8)
        for o in ordinals:
            bits[o] = 1
        bitmap = np.packbits(bits).tobytes()
        # rank directory: popcount acumulado a cada RANK_BLOCK bits
        n_blocks = (fact_count + RANK_BLOCK - 1) // RANK_BLOCK
        rank_dir = np.zeros(n_blocks, dtype=np.uint32)
        acc = 0
        for blk in range(n_blocks):
            rank_dir[blk] = acc
            lo = blk * RANK_BLOCK
            hi = min((blk + 1) * RANK_BLOCK, fact_count)
            acc += int(bits[lo:hi].sum())
        # payload raw u8, na ordem dos ordinais (index = rank)
        payload = np.array([corrections[o] & 0xFF for o in ordinals], dtype=np.uint8).tobytes()
        # páginas + tags
        header = _HEADER.pack(MAGIC, FORMAT_VERSION, scope_id, bulk_epoch, fact_count,
                              page_size_bytes, n, PAYLOAD_RAW_U8)
        page_tags = []
        for pidx, off in enumerate(range(0, len(payload), page_size_bytes)):
            page = payload[off:off + page_size_bytes]
            page_tags.append(cls._page_mac(key, FORMAT_VERSION, scope_id, bulk_epoch, pidx, page))
        page_tags_blob = b"".join(page_tags)
        manifest = cls._manifest_mac(key, header, bitmap, rank_dir.tobytes(), page_tags_blob)
        blob = header + bitmap + rank_dir.tobytes() + payload + page_tags_blob + manifest
        return cls(blob, key)

    # ---------------- MACs ----------------
    @staticmethod
    def _page_mac(key: bytes, ver: int, scope: int, epoch: int, page_index: int, page: bytes) -> bytes:
        m = hmac.new(key, digestmod=hashlib.sha256)
        m.update(struct.pack("<HIII", ver, scope, epoch, page_index))
        m.update(page)
        return m.digest()[:TAG_BYTES]

    @staticmethod
    def _manifest_mac(key: bytes, header: bytes, bitmap: bytes, rank_dir: bytes, page_tags: bytes) -> bytes:
        m = hmac.new(key, digestmod=hashlib.sha256)
        m.update(b"MANIFEST")
        m.update(header); m.update(bitmap); m.update(rank_dir); m.update(page_tags)
        return m.digest()[:TAG_BYTES]

    # ---------------- parse + validação canônica ----------------
    def _parse(self) -> None:
        b = self._blob
        if len(b) < _HEADER.size:
            raise IntegrityError("blob menor que o header")
        (magic, ver, scope, epoch, fact_count, page_size, n_corr, kind) = _HEADER.unpack_from(b, 0)
        if magic != MAGIC:
            raise IntegrityError("magic inválido")
        # --- pré-checagens estruturais: ANTES de qualquer divisão ou MAC ---
        # (um MAC válido prova autenticidade dos bytes, não que a estrutura seja semanticamente
        #  correta; e page_size=0 causaria ZeroDivisionError, que não é capturado por try_open)
        if ver != FORMAT_VERSION:
            raise IncompatibleError(f"format_version desconhecida: {ver}")
        if kind != PAYLOAD_RAW_U8:
            raise IncompatibleError(f"payload_kind desconhecido: {kind}")
        if page_size <= 0:
            raise IntegrityError("page_size inválido (<=0)")
        if n_corr > fact_count:
            raise IntegrityError("n_corrections > fact_count")
        self.format_version = ver; self.scope_id = scope; self.bulk_epoch = epoch
        self.fact_count = fact_count; self.page_size_bytes = page_size; self.n_corrections = n_corr
        self.payload_kind = kind
        off = _HEADER.size
        self._bitmap_off = off
        self._bitmap_len = (fact_count + 7) // 8
        off += self._bitmap_len
        self._rank_off = off
        n_blocks = (fact_count + RANK_BLOCK - 1) // RANK_BLOCK
        self._rank_len = n_blocks * 4
        off += self._rank_len
        self._payload_off = off
        off += n_corr  # raw u8
        self._payload_len = n_corr
        self._n_pages = (n_corr + page_size - 1) // page_size if n_corr else 0
        self._page_tags_off = off
        off += self._n_pages * TAG_BYTES
        self._manifest_off = off
        self._expected_len = off + TAG_BYTES
        # --- comprimento canônico EXATO: rejeita cauda não autenticada E truncamento ---
        if len(b) != self._expected_len:
            raise IntegrityError(f"comprimento não canônico: {len(b)} != {self._expected_len}")

    def _verify_and_validate(self) -> None:
        """Roda UMA vez no open. 1) autenticidade via manifesto; 2) invariantes semânticas
        (que o MAC não garante). Qualquer violação → IntegrityError → try_open devolve None."""
        # 1) autenticidade: o manifesto cobre header+bitmap+rank_directory+page_tags
        if not self.verify_manifest():
            raise IntegrityError("MAC de manifesto inválido")
        # 2) invariantes semânticas sobre bytes já autênticos
        bitmap = self._blob[self._bitmap_off:self._bitmap_off + self._bitmap_len]
        bits = np.unpackbits(np.frombuffer(bitmap, dtype=np.uint8))
        if int(bits.sum()) != self.n_corrections:
            raise IntegrityError("popcount do bitmap != n_corrections")
        if bits.size > self.fact_count and int(bits[self.fact_count:].sum()) != 0:
            raise IntegrityError("bits de padding do bitmap não nulos")
        # rank_directory: consistente com o bitmap e monotônico (acc é não-decrescente)
        rank = np.frombuffer(self._blob[self._rank_off:self._rank_off + self._rank_len], dtype="<u4")
        acc = 0
        n_blocks = self._rank_len // 4
        for blk in range(n_blocks):
            if int(rank[blk]) != acc:
                raise IntegrityError("rank_directory inconsistente com o bitmap")
            lo = blk * RANK_BLOCK
            hi = min((blk + 1) * RANK_BLOCK, self.fact_count)
            acc += int(bits[lo:hi].sum())

    # ---------------- verificação ----------------
    def verify_manifest(self) -> bool:
        header = self._blob[:_HEADER.size]
        bitmap = self._blob[self._bitmap_off:self._bitmap_off + self._bitmap_len]
        rank_dir = self._blob[self._rank_off:self._rank_off + self._rank_len]
        page_tags = self._blob[self._page_tags_off:self._page_tags_off + self._n_pages * TAG_BYTES]
        expected = self._manifest_mac(self._key, header, bitmap, rank_dir, page_tags)
        got = self._blob[self._manifest_off:self._manifest_off + TAG_BYTES]
        return hmac.compare_digest(expected, got)

    def verify_page(self, page_index: int) -> bool:
        off = self._payload_off + page_index * self.page_size_bytes
        end = min(off + self.page_size_bytes, self._payload_off + self._payload_len)
        page = self._blob[off:end]
        expected = self._page_mac(self._key, self.format_version, self.scope_id, self.bulk_epoch, page_index, page)
        got = self._blob[self._page_tags_off + page_index * TAG_BYTES:
                         self._page_tags_off + (page_index + 1) * TAG_BYTES]
        return hmac.compare_digest(expected, got)

    # ---------------- rank/bit ----------------
    def _get_bit(self, ordinal: int) -> int:
        byte = self._blob[self._bitmap_off + (ordinal >> 3)]
        return (byte >> (7 - (ordinal & 7))) & 1

    def _rank1(self, ordinal: int) -> int:
        """Número de 1s em [0, ordinal) — usa o rank_directory e popcount do bloco parcial."""
        blk = ordinal // RANK_BLOCK
        base = struct.unpack_from("<I", self._blob, self._rank_off + blk * 4)[0]
        lo_bit = blk * RANK_BLOCK
        # conta bits de lo_bit até ordinal (exclusivo)
        cnt = 0
        i = lo_bit
        # bytes inteiros
        while i + 8 <= ordinal:
            byte = self._blob[self._bitmap_off + (i >> 3)]
            cnt += int(bin(byte).count("1"))
            i += 8
        while i < ordinal:
            cnt += self._get_bit(i); i += 1
        return int(base) + cnt

    # ---------------- lookup (SÓ do blob) ----------------
    def lookup(self, fact_id: int, scope_id: int, bulk_epoch: int) -> tuple[str, int | None]:
        """Retorna (status, valor). Status: correct | no_residual | abstain_scope |
        abstain_epoch | abstain_integrity | out_of_range. (deleção é decidida em `resolve`,
        que sombreia o campo com a camada L0; o campo em si só conhece "tem/não tem resíduo".)

        O manifesto e as invariantes já foram validados no open (OPEN_VALID); o header é
        autêntico, então scope/época vêm de bytes confiáveis. Por lookup, verifica-se apenas
        a **página consultada** — custo independente do tamanho dos metadados."""
        if scope_id != self.scope_id:
            return (ABSTAIN_SCOPE, None)
        if bulk_epoch != self.bulk_epoch:
            return (ABSTAIN_EPOCH, None)
        ordinal = fact_id  # fact_id == ordinal no substrato (registro do Horizon Core)
        if ordinal < 0 or ordinal >= self.fact_count:
            return (OUT_OF_RANGE, None)
        if self._get_bit(ordinal) == 0:
            return (NO_RESIDUAL, None)  # sem correção aqui → o consumidor pode ir ao bulk
        index = self._rank1(ordinal)
        if index >= self.n_corrections:
            return (ABSTAIN_INTEGRITY, None)  # rank inconsistente com o payload
        page_index = index // self.page_size_bytes
        if not self.verify_page(page_index):
            return (ABSTAIN_INTEGRITY, None)
        value = self._blob[self._payload_off + index]
        return (CORRECT, int(value))

    def safe_lookup(self, fact_id: int, scope_id: int, bulk_epoch: int) -> tuple[str, int | None]:
        """Como `lookup`, mas qualquer corrupção estrutural (truncamento, offset inválido)
        vira `abstain_integrity` — nunca uma exceção. Fail-closed (regra do V21: nenhuma
        falha vira crash, nenhuma vira correção silenciosa)."""
        try:
            return self.lookup(fact_id, scope_id, bulk_epoch)
        except (struct.error, IndexError, ValueError):
            return (ABSTAIN_INTEGRITY, None)

    # ---------------- serialização ----------------
    def serialize(self) -> bytes:
        return self._blob

    @classmethod
    def deserialize(cls, blob: bytes, key: bytes) -> "ResidualField":
        return cls(blob, key)

    @classmethod
    def try_open(cls, blob: bytes, key: bytes) -> "ResidualField | None":
        """Abre o blob sem lançar: header truncado/inválido/incompatível → None (o leitor cai
        no bulk). Para o CAMPO isso é seguro: um campo ausente/ilegível só deixa de adicionar
        correções — cai-se na verdade-base. (A ambiguidade None só é perigosa no TOMBSTONE, por
        isso a camada L0 usa `open_tombstone` com estado tipado, não `try_open`.)"""
        try:
            return cls(blob, key)
        except (struct.error, IndexError, ValueError, IntegrityError, IncompatibleError):
            return None

    def support_ordinals(self) -> set[int]:
        bitmap = self._blob[self._bitmap_off:self._bitmap_off + self._bitmap_len]
        bits = np.unpackbits(np.frombuffer(bitmap, dtype=np.uint8)) if bitmap else np.zeros(0, np.uint8)
        return {int(o) for o in np.flatnonzero(bits[:self.fact_count])}

    @property
    def n_bits(self) -> int:
        return len(self._blob) * 8


_TOMB_HEADER = struct.Struct("<4sHIII")  # magic, ver, scope, epoch, fact_count
TOMB_MAGIC = b"HTB1"


# estados de abertura de um componente (V23.0) — a ausência SÓ é segura quando autorizada pelo
# manifesto; `Path.exists()==false` nunca prova ausência legítima (o arquivo pode ter sido apagado):
#   ABSENT_ALLOWED   → manifesto não exige a camada + ausente → pode consultar L1
#   VALID            → existe, autêntico e canônico
#   REQUIRED_MISSING → manifesto exige a camada + ausente → abstain, JAMAIS consultar L1
#   CORRUPT          → existe mas não abre / MAC / não canônico → abstain
#   INCOMPATIBLE     → versão/formato não suportado → abstain
ABSENT_ALLOWED = "absent_allowed"
VALID = "valid"
REQUIRED_MISSING = "required_missing"
CORRUPT = "corrupt"
INCOMPATIBLE = "incompatible"


@dataclass(frozen=True)
class OpenResult:
    """Resultado tipado de abrir um componente (substitui a tupla ambígua `(status, layer)`).

    `state` distingue ausência-autorizada de ausência-obrigatória-faltando e de corrupção — o
    consumidor nunca mais confunde `None` com "seguro consultar a camada de baixo".
    """
    state: str
    component: str
    reason: str
    layer: "TombstoneLayer | None" = None


class TombstoneLayer:
    """Camada L0: conjunto autenticado de fatos apagados para um (scope, época).

    Validada por inteiro no open (MAC + canônica). Sombreia o campo (L1) antes de qualquer
    payload. O ponto crítico: uma camada **ilegível** não pode ser confundida com **ausente** —
    senão a corrupção reverte um apagamento (o fato ressuscita). Use `open_tombstone`, que
    devolve um `OpenResult` tipado; nunca dependa de `None` para decidir consultar L1.
    """

    def __init__(self, blob: bytes, key: bytes):
        self._blob = blob
        self._key = key
        if len(blob) < _TOMB_HEADER.size:
            raise IntegrityError("blob de tombstone menor que o header")
        (magic, ver, scope, epoch, fact_count) = _TOMB_HEADER.unpack_from(blob, 0)
        if magic != TOMB_MAGIC:
            raise IntegrityError("magic de tombstone inválido")
        if ver != FORMAT_VERSION:
            raise IncompatibleError(f"format_version de tombstone desconhecida: {ver}")
        self.format_version = ver
        self.scope_id = scope
        self.bulk_epoch = epoch
        self.fact_count = fact_count
        self._bitmap_off = _TOMB_HEADER.size
        self._bitmap_len = (fact_count + 7) // 8
        self._tag_off = self._bitmap_off + self._bitmap_len
        self._expected_len = self._tag_off + TAG_BYTES
        # comprimento canônico exato: rejeita cauda não autenticada e truncamento
        if len(blob) != self._expected_len:
            raise IntegrityError(f"comprimento de tombstone não canônico: {len(blob)} != {self._expected_len}")
        # autenticidade (MAC) verificada no open
        if not self.verify():
            raise IntegrityError("MAC de tombstone inválido")
        # invariante: bits de padding além de fact_count devem ser zero
        bitmap = blob[self._bitmap_off:self._bitmap_off + self._bitmap_len]
        bits = np.unpackbits(np.frombuffer(bitmap, dtype=np.uint8))
        if bits.size > fact_count and int(bits[fact_count:].sum()) != 0:
            raise IntegrityError("bits de padding do tombstone não nulos")

    @classmethod
    def build(cls, fact_count: int, deleted: set[int], scope_id: int, bulk_epoch: int,
              key: bytes) -> "TombstoneLayer":
        for o in deleted:
            if not (0 <= o < fact_count):
                raise IntegrityError(f"ordinal de tombstone fora de [0, fact_count): {o}")
        bits = np.zeros(fact_count, dtype=np.uint8)
        for o in deleted:
            bits[o] = 1
        bitmap = np.packbits(bits).tobytes()
        header = _TOMB_HEADER.pack(TOMB_MAGIC, FORMAT_VERSION, scope_id, bulk_epoch, fact_count)
        tag = cls._mac(key, header, bitmap)
        return cls(header + bitmap + tag, key)

    @staticmethod
    def _mac(key: bytes, header: bytes, bitmap: bytes) -> bytes:
        m = hmac.new(key, digestmod=hashlib.sha256)
        m.update(b"TOMBSTONE")
        m.update(header)
        m.update(bitmap)
        return m.digest()[:TAG_BYTES]

    def verify(self) -> bool:
        header = self._blob[:_TOMB_HEADER.size]
        bitmap = self._blob[self._bitmap_off:self._bitmap_off + self._bitmap_len]
        expected = self._mac(self._key, header, bitmap)
        got = self._blob[self._tag_off:self._tag_off + TAG_BYTES]
        return hmac.compare_digest(expected, got)

    def is_deleted(self, ordinal: int) -> bool:
        if ordinal < 0 or ordinal >= self.fact_count:
            return False
        byte = self._blob[self._bitmap_off + (ordinal >> 3)]
        return bool((byte >> (7 - (ordinal & 7))) & 1)

    def support_ordinals(self) -> set[int]:
        bitmap = self._blob[self._bitmap_off:self._bitmap_off + self._bitmap_len]
        bits = np.unpackbits(np.frombuffer(bitmap, dtype=np.uint8)) if bitmap else np.zeros(0, np.uint8)
        return {int(o) for o in np.flatnonzero(bits[:self.fact_count])}

    def serialize(self) -> bytes:
        return self._blob

    @classmethod
    def _open(cls, blob: bytes, key: bytes) -> "TombstoneLayer":
        """Abre ou lança — uso interno por `open_tombstone` (que classifica o erro)."""
        return cls(blob, key)

    @classmethod
    def try_open(cls, blob: bytes, key: bytes) -> "TombstoneLayer | None":
        """DEPRECIADO — devolve `None` tanto para ausente quanto para corrompido, o que já causou
        um bug de ressurreição. Use `open_tombstone`, que devolve um `OpenResult` tipado."""
        warnings.warn(
            "TombstoneLayer.try_open confunde ausência com corrupção (risco de ressurreição); "
            "use open_tombstone(blob, key, required=...)",
            DeprecationWarning, stacklevel=2)
        try:
            return cls(blob, key)
        except (struct.error, IndexError, ValueError, IntegrityError, IncompatibleError):
            return None

    @property
    def n_bits(self) -> int:
        return len(self._blob) * 8


def open_tombstone(blob: bytes | None, key: bytes, *, required: bool) -> OpenResult:
    """Abre a camada L0 devolvendo um `OpenResult` tipado.

    `required` vem da declaração do manifesto (ausência só é segura quando o manifesto a
    autoriza). `Path.exists()==false` NUNCA prova ausência legítima — o arquivo pode ter sido
    apagado; por isso um componente exigido e ausente é `REQUIRED_MISSING`, não `ABSENT_ALLOWED`.

    - `blob is None`, não exigido → ABSENT_ALLOWED (pode consultar L1);
    - `blob is None`, exigido     → REQUIRED_MISSING (abstain; JAMAIS consultar L1);
    - versão/formato não suportado → INCOMPATIBLE;
    - ilegível/adulterado/não canônico → CORRUPT;
    - autêntico e canônico → VALID.
    """
    if blob is None:
        if required:
            return OpenResult(REQUIRED_MISSING, "tombstone",
                              "manifesto exige tombstone, arquivo ausente")
        return OpenResult(ABSENT_ALLOWED, "tombstone", "manifesto não exige tombstone")
    try:
        layer = TombstoneLayer._open(blob, key)
    except IncompatibleError as exc:
        return OpenResult(INCOMPATIBLE, "tombstone", str(exc))
    except (struct.error, IndexError, ValueError, IntegrityError) as exc:
        return OpenResult(CORRUPT, "tombstone", str(exc))
    return OpenResult(VALID, "tombstone", "ok", layer=layer)


def resolve(field: "ResidualField | None", tombstone: OpenResult,
            fact_id: int, scope_id: int, bulk_epoch: int) -> tuple[str, int | None]:
    """Resolução por camadas: L0 (tombstone) sombreia L1 (campo) antes de qualquer payload.

    `tombstone` é o `OpenResult` de `open_tombstone`. Fail-closed em cada porta:
    - **CORRUPT / INCOMPATIBLE / REQUIRED_MISSING** → `abstain_integrity`, JAMAIS consulta L1
      (ausência não autorizada ou camada ilegível não pode reverter um apagamento);
    - VALID de outro escopo/época → abstain (não se confia numa deleção que não casa);
    - VALID e fato apagado → **`deleted`** (TERMINAL — o consumidor jamais consulta bulk/L1);
    - ABSENT_ALLOWED, ou VALID não-apagado → consulta L1;
    - campo ausente/ilegível → `fallback` (o campo só adiciona correções; sem ele cai-se na
      verdade-base — não há risco de ressurreição, ao contrário do tombstone).
    """
    st = tombstone.state
    if st in (CORRUPT, INCOMPATIBLE, REQUIRED_MISSING):
        return (ABSTAIN_INTEGRITY, None)  # tombstone ilegível/ausência-não-autorizada NUNCA libera L1
    if st == VALID:
        layer = tombstone.layer
        if layer.scope_id != scope_id:
            return (ABSTAIN_SCOPE, None)
        if layer.bulk_epoch != bulk_epoch:
            return (ABSTAIN_EPOCH, None)
        if layer.is_deleted(fact_id):
            return (DELETED, None)  # TERMINAL: separado de no_residual de propósito
    if field is None:
        return (FALLBACK, None)
    return field.safe_lookup(fact_id, scope_id, bulk_epoch)


def estimate_blob_bits(fact_count: int, n_corrections: int, page_size_bytes: int) -> int:
    """Tamanho do blob raw em bits, para orçar a construção sem serializar de fato."""
    header = _HEADER.size
    bitmap = (fact_count + 7) // 8
    n_blocks = (fact_count + RANK_BLOCK - 1) // RANK_BLOCK
    rank_dir = n_blocks * 4
    payload = n_corrections
    n_pages = (n_corrections + page_size_bytes - 1) // page_size_bytes if n_corrections else 0
    page_tags = n_pages * TAG_BYTES
    manifest = TAG_BYTES
    return (header + bitmap + rank_dir + payload + page_tags + manifest) * 8
