# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""FH-04 / V23-F — ledger de custo total honesto.

Contabiliza os BYTES DURÁVEIS reais no disco, classificados pelas descriptors da geração viva
(registry, bulk, residual, tombstone, dedup, WAL ativo/selado, manifestos, publication records,
quarantine e órfãos). Publica dois ledgers:

- `total`: tudo necessário para o sistema operar (inclui registry + dedup + WAL + publicação);
- `marginal`: remove as estruturas COMPARTILHADAS/fixas (scaffolding da base: registry + dedup),
  para comparações entre braços onde essas estruturas são iguais.

Não mede microlatência (isso é o benchmark COW dedicado); mede armazenamento, que é reproduzível.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from horizon_memory._engine.horizon_artifacts import ArtifactKind

# Categorias "compartilhadas/fixas" removidas no ledger marginal.
_MARGINAL_EXCLUDE = ("registry_bytes", "dedup_bytes")


@dataclass(frozen=True)
class LedgerReport:
    scope_id: int
    generation_id: int | None
    read_seq: int | None
    # bytes por categoria durável
    registry_bytes: int = 0
    bulk_bytes: int = 0
    residual_bytes: int = 0
    tombstone_bytes: int = 0
    dedup_bytes: int = 0
    manifest_bytes: int = 0
    record_bytes: int = 0
    wal_active_bytes: int = 0
    wal_sealed_bytes: int = 0
    quarantine_bytes: int = 0
    orphan_object_bytes: int = 0
    current_pointer_bytes: int = 0
    # contagens de fatos
    live_facts: int = 0
    deleted_facts: int = 0
    # objetos
    object_count: int = 0
    orphan_object_count: int = 0

    @property
    def base_bytes(self) -> int:
        return (self.registry_bytes + self.bulk_bytes + self.residual_bytes
                + self.tombstone_bytes + self.dedup_bytes)

    @property
    def total_bytes(self) -> int:
        return (self.base_bytes + self.manifest_bytes + self.record_bytes
                + self.wal_active_bytes + self.wal_sealed_bytes + self.quarantine_bytes
                + self.orphan_object_bytes + self.current_pointer_bytes)

    @property
    def marginal_bytes(self) -> int:
        return self.total_bytes - self.registry_bytes - self.dedup_bytes

    @property
    def bytes_per_live_fact(self) -> float:
        return self.total_bytes / self.live_facts if self.live_facts else float("nan")

    @property
    def bytes_per_deleted_fact(self) -> float:
        return self.tombstone_bytes / self.deleted_facts if self.deleted_facts else float("nan")

    def accounts(self) -> bool:
        """O ledger fecha contabilmente: soma das categorias == total_bytes (por construção)."""
        parts = (self.base_bytes + self.manifest_bytes + self.record_bytes
                 + self.wal_active_bytes + self.wal_sealed_bytes + self.quarantine_bytes
                 + self.orphan_object_bytes + self.current_pointer_bytes)
        return parts == self.total_bytes

    def as_dict(self) -> dict:
        return {
            "scope_id": self.scope_id,
            "generation_id": self.generation_id,
            "read_seq": self.read_seq,
            "categories": {
                "registry_bytes": self.registry_bytes,
                "bulk_bytes": self.bulk_bytes,
                "residual_bytes": self.residual_bytes,
                "tombstone_bytes": self.tombstone_bytes,
                "dedup_bytes": self.dedup_bytes,
                "manifest_bytes": self.manifest_bytes,
                "record_bytes": self.record_bytes,
                "wal_active_bytes": self.wal_active_bytes,
                "wal_sealed_bytes": self.wal_sealed_bytes,
                "quarantine_bytes": self.quarantine_bytes,
                "orphan_object_bytes": self.orphan_object_bytes,
                "current_pointer_bytes": self.current_pointer_bytes,
            },
            "base_bytes": self.base_bytes,
            "total_bytes": self.total_bytes,
            "marginal_bytes": self.marginal_bytes,
            "live_facts": self.live_facts,
            "deleted_facts": self.deleted_facts,
            "bytes_per_live_fact": self.bytes_per_live_fact,
            "bytes_per_deleted_fact": self.bytes_per_deleted_fact,
            "object_count": self.object_count,
            "orphan_object_count": self.orphan_object_count,
            "accounts": self.accounts(),
        }


def _dir_file_bytes(path: Path) -> dict:
    """{filename_stem_or_name: size} para todos os arquivos regulares sob `path` (recursivo)."""
    out = {}
    if not path.exists():
        return out
    for p in path.rglob("*"):
        if p.is_file():
            out[str(p)] = p.stat().st_size
    return out


def compute_ledger(cfg, ws, pub, cursor, *, live_facts: int = 0, deleted_facts: int = 0) -> LedgerReport:
    """Constrói o ledger a partir do disco + da geração viva apontada por `cursor`.

    `cursor` é o `PublishedCursor` (de open_published_cursor). `live_facts`/`deleted_facts` vêm do
    export/audit da fachada (contagem lógica). Objetos content-addressed não referenciados pela geração
    viva contam como órfãos (candidatos a GC), nunca somem do total honesto.
    """
    manifest = cursor.manifest if cursor is not None else None

    # mapa sha_hex -> categoria, derivado da geração viva
    kind_by_sha: dict[str, str] = {}
    if manifest is not None:
        base = getattr(manifest, "base_descriptors", {}) or {}
        cat_of = {
            ArtifactKind.REGISTRY: "registry_bytes",
            ArtifactKind.BULK: "bulk_bytes",
            ArtifactKind.RESIDUAL: "residual_bytes",
            ArtifactKind.TOMBSTONE: "tombstone_bytes",
            ArtifactKind.DEDUP: "dedup_bytes",
        }
        for kind, desc in base.items():
            kind_by_sha[desc.sha256.hex()] = cat_of.get(kind, "orphan_object_bytes")
        # manifesto vivo (o objeto content-addressed apontado pelo record)
        kind_by_sha[cursor.proof.manifest_sha256.hex()] = "manifest_bytes"
        # segmentos selados
        for seg in getattr(manifest, "segments", ()):  # ACTIVE final não tem objeto
            sha = getattr(seg, "sealed_object_sha256", None)
            if sha and sha != b"\x00" * 32:
                kind_by_sha[sha.hex()] = "wal_sealed_bytes"

    # publication record vivo (apontado pelo CURRENT)
    rec_sha_hex = None
    if cursor is not None:
        rec_sha_hex = cursor.proof.publication_sha256.hex()
        kind_by_sha.setdefault(rec_sha_hex, "record_bytes")

    totals = {
        "registry_bytes": 0, "bulk_bytes": 0, "residual_bytes": 0, "tombstone_bytes": 0,
        "dedup_bytes": 0, "manifest_bytes": 0, "record_bytes": 0, "wal_sealed_bytes": 0,
        "orphan_object_bytes": 0,
    }
    orphan_count = 0
    object_count = 0

    objects_dir = Path(ws.root) / "objects"
    for p, size in _dir_file_bytes(objects_dir).items():
        object_count += 1
        sha = Path(p).stem  # <sha>.hobj -> <sha>
        cat = kind_by_sha.get(sha)
        if cat is None:
            totals["orphan_object_bytes"] += size
            orphan_count += 1
        else:
            totals[cat] += size

    wal_active_bytes = sum(_dir_file_bytes(Path(ws.root) / "wal" / "active").values())

    # pub scope dir: CURRENT + quarantine
    scope_dir = Path(pub.directory)
    quarantine = scope_dir / "gc-quarantine"
    quarantine_bytes = sum(_dir_file_bytes(quarantine).values())
    current_pointer_bytes = 0
    for p, size in _dir_file_bytes(scope_dir).items():
        name = Path(p).name
        if "gc-quarantine" in p:
            continue
        if name == "CURRENT" or name.startswith("CURRENT"):
            current_pointer_bytes += size

    return LedgerReport(
        scope_id=cfg.scope_id,
        generation_id=getattr(manifest, "generation_id", None),
        read_seq=getattr(manifest, "read_seq", None),
        registry_bytes=totals["registry_bytes"],
        bulk_bytes=totals["bulk_bytes"],
        residual_bytes=totals["residual_bytes"],
        tombstone_bytes=totals["tombstone_bytes"],
        dedup_bytes=totals["dedup_bytes"],
        manifest_bytes=totals["manifest_bytes"],
        record_bytes=totals["record_bytes"],
        wal_active_bytes=wal_active_bytes,
        wal_sealed_bytes=totals["wal_sealed_bytes"],
        quarantine_bytes=quarantine_bytes,
        orphan_object_bytes=totals["orphan_object_bytes"],
        current_pointer_bytes=current_pointer_bytes,
        live_facts=live_facts,
        deleted_facts=deleted_facts,
        object_count=object_count,
        orphan_object_count=orphan_count,
    )
