# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""horizon_sharded.py — copy-on-write por shards para L0 (V23-A3).

O sweep de V23-A2 mostrou que, acima de ~24k entradas, o `clone()` integral do `WalIndex`/
`TxIdIndex` (O(|L0|) por batch) passa a dominar o custo — não mais o `fsync`. Aqui o índice vira
uma **tupla imutável de shards**; um batch só copia os shards que **tocou** (`begin_mutation` →
mutações → `freeze`), e o snapshot novo reutiliza por referência todos os shards intocados. O custo
passa a depender da **superfície modificada do batch**, não do tamanho total da memória.

- `FrozenShard` expõe só `get`/iteração controlada/`len` (nunca o dict interno).
- Shards vazios apontam para um **singleton compartilhado** (`_EMPTY`) — sem milhares de dicts vazios.
- Função de shard: `splitmix64` (não `hash()` nem `id & mask`), para espalhar FactIds sequenciais e
  os operation_ids do mesmo cliente. Seed fixo nos testes, aleatório por store em produção.

`ShardedWalIndex`/`ShardedTxIdIndex` são **drop-in**: expõem a mesma API dos índices simples
(`get`/`apply`; `check`/`record`/`get_seq`) mais `begin_mutation()`/`freeze()`, então `prepare_batch`
os usa sem saber se é clone integral ou COW.
"""

from __future__ import annotations

import hmac
import struct
from dataclasses import dataclass

from horizon_memory._engine.horizon_store import apply_versioned

_M64 = 0xFFFFFFFFFFFFFFFF


def _splitmix64(x: int) -> int:
    x = (x + 0x9E3779B97F4A7C15) & _M64
    z = x
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _M64
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _M64
    return (z ^ (z >> 31)) & _M64


def shard_of_fact(fact_id: int, seed: int, mask: int) -> int:
    return _splitmix64((fact_id ^ seed) & _M64) & mask


def shard_of_opid(operation_id: bytes, seed: int, mask: int) -> int:
    client, txid = struct.unpack_from("<QQ", operation_id, 0)
    return _splitmix64(_splitmix64((client ^ seed) & _M64) ^ txid) & mask


class FrozenShard:
    """Fatia imutável do índice. Expõe só leitura controlada — nunca o dict interno."""
    __slots__ = ("_d", "__weakref__")

    def __init__(self, d: dict):
        self._d = d

    def get(self, key):
        return self._d.get(key)

    def items(self):
        return self._d.items()

    def copy_dict(self) -> dict:
        """Cópia rápida (nível C) do conteúdo para o builder — sem expor o dict vivo."""
        return dict(self._d)

    def __len__(self):
        return len(self._d)


_EMPTY = FrozenShard({})     # singleton compartilhado por todos os shards vazios


def _freeze_shards(base_shards, dirty):
    """Nova tupla reutilizando por referência os shards intocados; dirty vira FrozenShard/_EMPTY."""
    shards = list(base_shards)
    for idx, d in dirty.items():
        shards[idx] = FrozenShard(d) if d else _EMPTY
    return tuple(shards)


# ------------------------------ WalIndex sharded ------------------------------
class ShardedWalIndex:
    __slots__ = ("shard_bits", "shard_seed", "shards", "_mask")

    def __init__(self, shard_bits: int, shard_seed: int, shards: tuple):
        self.shard_bits = shard_bits
        self.shard_seed = shard_seed
        self.shards = shards
        self._mask = (1 << shard_bits) - 1

    @classmethod
    def empty(cls, shard_bits: int = 10, shard_seed: int = 0) -> "ShardedWalIndex":
        return cls(shard_bits, shard_seed, tuple([_EMPTY] * (1 << shard_bits)))

    def _shard(self, fact_id: int) -> int:
        return shard_of_fact(fact_id, self.shard_seed, self._mask)

    def get(self, fact_id: int):
        return self.shards[self._shard(fact_id)].get(fact_id)

    def begin_mutation(self) -> "WalIndexBuilder":
        return WalIndexBuilder(self)

    def items(self):
        for s in self.shards:
            yield from s.items()


class WalIndexBuilder:
    """Única estrutura mutável: copia um shard só no primeiro write nele (copy-on-write)."""

    def __init__(self, base: ShardedWalIndex):
        self.base = base
        self.dirty: dict[int, dict] = {}
        self.conflicts = 0

    def _mut(self, idx: int) -> dict:
        d = self.dirty.get(idx)
        if d is None:
            d = self.base.shards[idx].copy_dict()      # copy-on-first-write (nível C)
            self.dirty[idx] = d
        return d

    def get(self, fact_id: int):
        idx = shard_of_fact(fact_id, self.base.shard_seed, self.base._mask)
        d = self.dirty.get(idx)
        return (d.get(fact_id) if d is not None else self.base.shards[idx].get(fact_id))

    def apply(self, fact_id: int, version: int, op: str, value) -> str:
        idx = shard_of_fact(fact_id, self.base.shard_seed, self.base._mask)
        r = apply_versioned(self._mut(idx), fact_id, version, op, value)
        if r == "conflict":
            self.conflicts += 1
        return r

    def freeze(self) -> ShardedWalIndex:
        return ShardedWalIndex(self.base.shard_bits, self.base.shard_seed,
                               _freeze_shards(self.base.shards, self.dirty))


# ------------------------------ TxIdIndex sharded ------------------------------
class ShardedTxIdIndex:
    __slots__ = ("shard_bits", "shard_seed", "shards", "_mask")

    def __init__(self, shard_bits: int, shard_seed: int, shards: tuple):
        self.shard_bits = shard_bits
        self.shard_seed = shard_seed
        self.shards = shards
        self._mask = (1 << shard_bits) - 1

    @classmethod
    def empty(cls, shard_bits: int = 12, shard_seed: int = 0) -> "ShardedTxIdIndex":
        return cls(shard_bits, shard_seed, tuple([_EMPTY] * (1 << shard_bits)))

    def _shard(self, opid: bytes) -> int:
        return shard_of_opid(opid, self.shard_seed, self._mask)

    def check(self, operation_id: bytes, digest: bytes) -> str:
        e = self.shards[self._shard(operation_id)].get(operation_id)
        if e is None:
            return "new"
        return "dedup_replay" if hmac.compare_digest(e[0], digest) else "txid_conflict"

    def get_seq(self, operation_id: bytes):
        e = self.shards[self._shard(operation_id)].get(operation_id)
        return None if e is None else e[1]

    def begin_mutation(self) -> "TxIdIndexBuilder":
        return TxIdIndexBuilder(self)

    def items(self):
        for s in self.shards:
            yield from s.items()


class TxIdIndexBuilder:
    def __init__(self, base: ShardedTxIdIndex):
        self.base = base
        self.dirty: dict[int, dict] = {}

    def _mut(self, idx: int) -> dict:
        d = self.dirty.get(idx)
        if d is None:
            d = self.base.shards[idx].copy_dict()      # copy-on-first-write (nível C)
            self.dirty[idx] = d
        return d

    def check(self, operation_id: bytes, digest: bytes) -> str:
        idx = shard_of_opid(operation_id, self.base.shard_seed, self.base._mask)
        d = self.dirty.get(idx)
        e = (d.get(operation_id) if d is not None else self.base.shards[idx].get(operation_id))
        if e is None:
            return "new"
        return "dedup_replay" if hmac.compare_digest(e[0], digest) else "txid_conflict"

    def get_seq(self, operation_id: bytes):
        idx = shard_of_opid(operation_id, self.base.shard_seed, self.base._mask)
        d = self.dirty.get(idx)
        e = (d.get(operation_id) if d is not None else self.base.shards[idx].get(operation_id))
        return None if e is None else e[1]

    def record(self, operation_id: bytes, digest: bytes, seq: int) -> None:
        idx = shard_of_opid(operation_id, self.base.shard_seed, self.base._mask)
        self._mut(idx)[operation_id] = (digest, seq)

    def freeze(self) -> ShardedTxIdIndex:
        return ShardedTxIdIndex(self.base.shard_bits, self.base.shard_seed,
                                _freeze_shards(self.base.shards, self.dirty))


@dataclass(frozen=True)
class CopyStats:
    shards_touched: int
    entries_copied: int


def copy_stats_wal(builder: WalIndexBuilder) -> CopyStats:
    return CopyStats(len(builder.dirty), sum(len(d) for d in builder.dirty.values()))
