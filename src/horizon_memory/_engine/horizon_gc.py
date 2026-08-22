# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Garbage collection SEGURO da Horizon Memory (V23-E / FH-03).

Regra de ouro: **nunca apagar nada alcançável** por `CURRENT`, por uma ReadView presa (snapshot) ou por
uma operação preparada que ainda detenha autoridade. A incerteza JAMAIS vira `unreachable` — qualquer
descriptor não autenticado, objeto ausente ou limite excedido ABORTA o plano.

Três universos DISTINTOS (E0 §1):
  1. objetos content-addressed (`objects/<sha256>.hobj`): manifestos, PublicationRecords, os 5 artefatos
     da base e os segmentos WAL SEALED (todos endereçados por conteúdo);
  2. arquivos ACTIVE (`wal/active/<scope>/<segment>.hwal`): identificados por scope/segment, NÃO por
     conteúdo (o ACTIVE muta por append até selar);
  3. arquivos operacionais temporários / quarantine (`.tmp.*`, `gc-quarantine/`): nunca são raiz.

E0 é PURO: só calcula alcançabilidade e um `GcPlan`; não apaga nem move nada. As fases destrutivas
(quarantine → delete) e o protocolo conservador (lock/stale-plan/journal) são E1–E5.
"""

from __future__ import annotations

import errno
import fcntl
import hashlib
import hmac
import os
import stat
import struct
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from horizon_memory._engine.horizon_durability import fsync_dir, open_hardened_lock
from horizon_memory._engine.horizon_manifest import (
    BASE_KINDS, OpenGenerationState, parse_manifest)
from horizon_memory._engine.horizon_publication import (
    PublicationState, parse_publication_record, read_current)
from horizon_memory._engine.horizon_wal import STATE_ACTIVE, STATE_SEALED

_PLAN_DOMAIN = b"HORIZON-GC-PLAN-v1"
_ZERO32 = b"\x00" * 32


# ---------------- identidade dos itens coletáveis ----------------
OBJECT, ACTIVE = "object", "active"     # universo 1 e 2


def obj_item(digest_hex: str) -> tuple:
    return (OBJECT, digest_hex)


def active_item(scope_id: int, segment_id: int) -> tuple:
    return (ACTIVE, f"{scope_id:08x}:{segment_id:08x}")


# ---------------- raízes e plano ----------------
@dataclass(frozen=True)
class GcRoots:
    """Digests de publicação que são RAÍZES vivas + objetos/ACTIVE de operações preparadas. Nada aqui
    vem de nome fornecido pelo caller: o `current` sai do `CURRENT` durável, os `retained` da cadeia
    `previous_publication_sha256`, os `pinned` do registro de pins, os `prepared_*` de pacotes com digest
    verificado (E4)."""
    current_publication_sha256: bytes | None
    pinned_publication_sha256s: tuple = ()
    retained_publication_sha256s: tuple = ()
    prepared_object_sha256s: tuple = ()          # objetos content-addressed de prepared vivos
    prepared_active_segments: tuple = ()         # (scope_id, segment_id) do próximo ACTIVE preparado

    def all_publication_digests(self):
        seen, out = set(), []
        for d in ((self.current_publication_sha256,) if self.current_publication_sha256 else ()) \
                + tuple(self.pinned_publication_sha256s) + tuple(self.retained_publication_sha256s):
            if d and d not in seen:
                seen.add(d)
                out.append(d)
        return out


class GcState(Enum):
    PLANNED = 0
    NO_CURRENT = 1               # CURRENT ausente (genesis não inicializado) — nada a coletar, sem erro
    ABORTED_CORRUPT = 2          # descriptor/objeto não autenticado na closure de uma raiz → fail-closed
    ABORTED_MISSING = 3          # objeto obrigatório de uma raiz ausente → fail-closed
    ABORTED_LIMIT = 4            # limite de travessia excedido → fail-closed


@dataclass(frozen=True)
class GcPlan:
    state: GcState
    reachable: frozenset = frozenset()      # itens (kind, key) provadamente VIVOS
    unreachable: frozenset = frozenset()    # itens presentes em disco e NÃO alcançáveis → elegíveis
    deferred: frozenset = frozenset()       # itens preservados por conservadorismo (nunca coletados aqui)
    reasons: dict = field(default_factory=dict)
    plan_digest: bytes = b""
    reason: str = ""


def canonical_plan_digest(reachable, unreachable, deferred) -> bytes:
    """Digest CANÔNICO do plano (E0 §2): sobre os conjuntos ordenados de itens, com separação de domínio.
    Dois planos idênticos têm o mesmo digest; o protocolo (E2) o usa para detectar mudança de raízes."""
    h = hashlib.sha256()
    h.update(_PLAN_DOMAIN)
    for label, s in ((b"R", reachable), (b"U", unreachable), (b"D", deferred)):
        h.update(label)
        h.update(struct.pack("<I", len(s)))
        for kind, key in sorted(s):
            h.update(f"{kind}\x00{key}\x00".encode())
    return h.digest()


# ---------------- enumeração do que EXISTE em disco ----------------
def enumerate_objects(object_store) -> set:
    """Todos os `objects/<sha>.hobj` presentes (universo 1)."""
    d = Path(object_store.root) / "objects"
    if not d.is_dir():
        return set()
    return {obj_item(p.stem) for p in d.glob("*.hobj") if p.is_file()}


def enumerate_active(wal_store) -> set:
    """Todos os ACTIVE `wal/active/<scope>/<segment>.hwal` presentes (universo 2)."""
    d = Path(wal_store.root) / "wal" / "active"
    if not d.is_dir():
        return set()
    out = set()
    for scope_dir in d.iterdir():
        if not scope_dir.is_dir():
            continue
        try:
            scope_id = int(scope_dir.name, 16)
        except ValueError:
            continue
        for p in scope_dir.glob("*.hwal"):
            if p.is_file():
                try:
                    seg = int(p.stem, 16)
                except ValueError:
                    continue
                out.add(active_item(scope_id, seg))
    return out


# ---------------- travessia autenticada a partir de uma publicação ----------------
class _Abort(Exception):
    def __init__(self, state, reason):
        self.state, self.reason = state, reason


def _reach_from_publication(pub_digest, object_store, wal_store, keyring, reachable, limits, budget):
    """FH-03.1 §6 — fecho VERIFICADO INTEGRALMENTE de uma publicação. Além de parsear o record e o
    manifesto (digest + MAC), ABRE a geração inteira com `open_generation` (os 5 artefatos com limites +
    digest + parser + MAC, validação cruzada da base, cada WAL SEALED lido pelo `WalStore`, o ACTIVE
    declarado validado, e scope/generation/fact_count conferidos). Qualquer ausência/corrupção/
    incompatibilidade → `_Abort` (a incerteza nunca vira `unreachable`)."""
    from horizon_memory._engine.horizon_manifest import open_generation
    max_obj = limits.get("max_object_bytes", 1 << 30)
    pub_hex = pub_digest.hex()
    budget[0] -= 1
    if budget[0] < 0:
        raise _Abort(GcState.ABORTED_LIMIT, "orçamento de travessia excedido")
    reachable.add(obj_item(pub_hex))                      # o próprio PublicationRecord

    rr = object_store.get_limited(pub_hex, max_obj)
    if not rr.ok:
        raise _Abort(GcState.ABORTED_MISSING, f"PublicationRecord {pub_hex[:12]} ausente/ilegível")
    pst, record, why = parse_publication_record(rr.blob, keyring)
    if pst != PublicationState.VALID:
        raise _Abort(GcState.ABORTED_CORRUPT, f"PublicationRecord {pub_hex[:12]}: {why}")

    man_hex = record.manifest_sha256.hex()
    reachable.add(obj_item(man_hex))                      # o manifesto
    mr = object_store.get_limited(man_hex, max_obj)
    if not mr.ok:
        raise _Abort(GcState.ABORTED_MISSING, f"manifesto {man_hex[:12]} ausente")
    if not hmac.compare_digest(hashlib.sha256(mr.blob).digest(), record.manifest_sha256):
        raise _Abort(GcState.ABORTED_CORRUPT, "manifesto não casa manifest_sha256")
    mstate, man, mwhy = parse_manifest(mr.blob, keyring)
    if mstate != OpenGenerationState.VALID:
        raise _Abort(GcState.ABORTED_CORRUPT, f"manifesto {man_hex[:12]}: {mwhy}")
    # ABERTURA COMPLETA da geração — a autoridade única de validação (5 artefatos, base cruzada, SEALED
    # pelo WalStore, ACTIVE, replay até R). Sem isto, um artefato/SEALED corrompido passaria despercebido.
    og = open_generation(mr.blob, object_store, wal_store, keyring)
    if og.state != OpenGenerationState.VALID:
        raise _Abort(GcState.ABORTED_CORRUPT, f"geração {man_hex[:12]}: {og.reason}")
    h = og.handle
    if h.scope_id != man.scope_id or h.generation_id != man.generation_id or h.fact_count != man.fact_count:
        raise _Abort(GcState.ABORTED_CORRUPT, "handle ↔ manifesto incoerentes (scope/gen/fact_count)")

    for kind in BASE_KINDS:                               # os 5 artefatos da base (já validados na abertura)
        desc = man.base_descriptors.get(kind)
        if desc is None:
            raise _Abort(GcState.ABORTED_CORRUPT, f"base sem {kind}")
        reachable.add(obj_item(desc.sha256.hex()))
    for s in man.segments:                                # segmentos WAL
        if s.status == STATE_SEALED:
            if s.sealed_object_sha256 == _ZERO32:
                raise _Abort(GcState.ABORTED_CORRUPT, "SEALED sem sealed_object_sha256")
            reachable.add(obj_item(s.sealed_object_sha256.hex()))
        elif s.status == STATE_ACTIVE:
            reachable.add(active_item(man.scope_id, s.segment_id))
        else:
            raise _Abort(GcState.ABORTED_CORRUPT, f"status de segmento desconhecido: {s.status}")


def compute_roots(publication_store, object_store, keyring, *, pinned_publication_sha256s=(),
                  retention: int = 1, prepared_roots=None, limits=None) -> tuple:
    """(GcState, GcRoots|None, reason). Lê o `CURRENT` durável e monta as raízes. `retention >= 1`:
    quantas publicações (a vigente + anteriores pela cadeia `previous_publication_sha256`) são retidas.
    FH-03.1 §7 — se um record declara um ancestral NÃO-zero e ele falha ao abrir, ABORTA (nunca encerra a
    cadeia em silêncio). `prepared_roots` é um `PreparedRoots` autenticado (E4), nunca listas cruas."""
    lim = limits or {}
    max_obj = lim.get("max_object_bytes", 1 << 30)
    cst, cur, why = read_current(publication_store.directory, keyring)
    if cst == PublicationState.MISSING:
        return (GcState.NO_CURRENT, None, "CURRENT ausente")
    if cst != PublicationState.VALID:
        return (GcState.ABORTED_CORRUPT, None, f"CURRENT: {why}")
    current = cur.publication_sha256

    retained = []
    d = current
    for _ in range(max(1, retention) - 1):
        rr = object_store.get_limited(d.hex(), max_obj)
        if not rr.ok:
            return (GcState.ABORTED_MISSING, None, f"publicação retida {d.hex()[:12]} ausente")
        pst, rec, w = parse_publication_record(rr.blob, keyring)
        if pst != PublicationState.VALID:
            return (GcState.ABORTED_CORRUPT, None, f"publicação retida {d.hex()[:12]}: {w}")
        if rec.previous_publication_sha256 == _ZERO32:
            break                                        # genesis — fim legítimo da cadeia
        prev = rec.previous_publication_sha256
        # §7 — o ancestral DECLARADO tem que ABRIR; falha ao abrir ABORTA (não encerra a cadeia em silêncio)
        pr = object_store.get_limited(prev.hex(), max_obj)
        if not pr.ok:
            return (GcState.ABORTED_MISSING, None, f"ancestral retido {prev.hex()[:12]} ausente")
        pst2, _rec2, w2 = parse_publication_record(pr.blob, keyring)
        if pst2 != PublicationState.VALID:
            return (GcState.ABORTED_CORRUPT, None, f"ancestral retido {prev.hex()[:12]}: {w2}")
        d = prev
        retained.append(d)

    pr = prepared_roots if prepared_roots is not None else PreparedRoots()
    roots = GcRoots(current, tuple(pinned_publication_sha256s), tuple(retained),
                    tuple(pr.object_sha256s), tuple(pr.active_segments))
    return (GcState.PLANNED, roots, "ok")


def plan_reachability(publication_store, wal_store, object_store, keyring, roots, *, limits=None) -> GcPlan:
    """Fase PURA (E0): calcula o fecho de alcançabilidade das raízes e o conjunto elegível
    (`unreachable = presente_em_disco − reachable`). NÃO apaga nem move nada. Qualquer incerteza na
    closure de uma raiz aborta com `unreachable` VAZIO (a incerteza nunca vira `unreachable`)."""
    lim = limits or {}
    reachable = set()
    budget = [lim.get("max_traversal_nodes", 100_000)]
    try:
        for pub_digest in roots.all_publication_digests():
            _reach_from_publication(pub_digest, object_store, wal_store, keyring, reachable, lim, budget)
        for od in roots.prepared_object_sha256s:          # objetos de prepared vivos (E4)
            reachable.add(obj_item(od.hex() if isinstance(od, (bytes, bytearray)) else od))
        for (scope_id, seg) in roots.prepared_active_segments:
            reachable.add(active_item(scope_id, seg))
    except _Abort as a:
        return GcPlan(a.state, reason=a.reason)           # unreachable = frozenset() (vazio) por padrão

    present = enumerate_objects(object_store) | enumerate_active(wal_store)
    reachable_present = reachable & present               # o que está vivo E existe
    unreachable = present - reachable                     # existe mas ninguém alcança → elegível
    reasons = {it: "reachable" for it in reachable_present}
    reasons.update({it: "unreachable" for it in unreachable})
    plan_digest = canonical_plan_digest(reachable_present, unreachable, frozenset())
    return GcPlan(GcState.PLANNED, frozenset(reachable_present), frozenset(unreachable),
                  frozenset(), reasons, plan_digest, "ok")


# ---------------- validação de nomes + leitura endurecida (FH-03.1 §3) ----------------
_MAX_NAME = 128
_MAX_RECORD_BYTES = 1 << 16          # pins/prepared são pequenos; recusa qualquer coisa maior ANTES de alocar


def valid_name(name) -> bool:
    """Nome CANÔNICO e limitado: `[A-Za-z0-9._-]{1,128}`, sem `/`, `\\`, `..`, NUL ou traversal."""
    if not isinstance(name, str) or not (1 <= len(name) <= _MAX_NAME):
        return False
    if name in (".", "..") or ".." in name or "\x00" in name or "/" in name or "\\" in name:
        return False
    return all(c.isalnum() or c in "._-" for c in name)


class ScanState(Enum):
    VALID = 0
    CORRUPT = 1                      # registro ilegível/truncado/MAC inválido → ABORTA o GC
    IO_ERROR = 2
    RESOURCE_LIMIT = 3               # registro maior que o limite (antes de alocar)


def _read_record_limited(path: Path):
    """(ScanState, bytes|None). Lê um registro pequeno com `S_ISREG`, `O_NOFOLLOW` e LIMITE antes de
    alocar (symlink/arquivo enorme recusados). Nunca lança."""
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(str(path), flags)
    except OSError as e:
        if e.errno == errno.ELOOP:
            return (ScanState.CORRUPT, None)             # symlink → recusa
        return (ScanState.IO_ERROR, None)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return (ScanState.CORRUPT, None)
        if st.st_size > _MAX_RECORD_BYTES:
            return (ScanState.RESOURCE_LIMIT, None)      # enorme → recusa antes de alocar
        data = os.read(fd, st.st_size)
        if len(data) != st.st_size:
            return (ScanState.CORRUPT, None)
        return (ScanState.VALID, data)
    except OSError:
        return (ScanState.IO_ERROR, None)
    finally:
        os.close(fd)


def _durable_write_record(path: Path, blob: bytes, *, best_effort: bool = False) -> None:
    """Escrita DURÁVEL e endurecida (§3): temp `O_EXCL` → write-all → fsync(temp) → rename → fsync_dir."""
    from horizon_memory._engine.horizon_durability import durable_replace
    tmp = path.parent / f".{path.name}.tmp.{os.urandom(6).hex()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(tmp), flags, 0o600)
    try:
        mv, n = memoryview(blob), 0
        while n < len(blob):
            w = os.write(fd, mv[n:])
            if w <= 0:
                raise OSError("write incompleto")
            n += w
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        try:
            os.unlink(str(tmp))
        except FileNotFoundError:
            pass
        raise
    else:
        os.close(fd)
    durable_replace(tmp, path, best_effort=best_effort)


# ================= E4/§4-§5: registro DURÁVEL e AUTENTICADO de prepared ativos =================
# A API operacional NÃO aceita listas cruas de prepared roots. Um `CompactionPrepared`/`RotationPrepared`
# SELADO (digest recomputado) REGISTRA seus objetos aqui; o GC lê SÓ deste registro. Omitir um prepared
# numa chamada JAMAIS pode desprotegê-lo — a autoridade é o registro durável, não o argumento.
_PREP_MAGIC = b"HGCP"
_PREP = struct.Struct("<4sHBIII")    # magic, ver, kind(B), key_id(I), n_obj(I), n_active(I)
_PREP_TAG = 16
_PREP_KIND = {"compaction": 1, "rotation": 2}


@dataclass(frozen=True)
class PreparedRoots:
    object_sha256s: tuple = ()       # digests (bytes) de objetos protegidos
    active_segments: tuple = ()      # (scope_id, segment_id) de ACTIVE protegidos


def _prepared_record_blob(kind: str, key_id: int, obj_digests, active_segments, scope_id, key: bytes):
    header = _PREP.pack(_PREP_MAGIC, 1, _PREP_KIND[kind], key_id, len(obj_digests), len(active_segments))
    body = b"".join(bytes(d) for d in obj_digests)
    body += b"".join(struct.pack("<II", scope_id, seg) for seg in active_segments)
    mac = hmac.new(key, b"HORIZONGCPREP" + header + body, hashlib.sha256).digest()[:_PREP_TAG]
    return header + body + mac


def _parse_prepared_record(blob: bytes, keyring):
    """(ok, PreparedRoots|None). Parser SEGURO contra truncamento/comprimento falso/cauda extra."""
    if len(blob) < _PREP.size + _PREP_TAG:
        return (False, None)
    magic, ver, kind, key_id, n_obj, n_active = _PREP.unpack_from(blob, 0)
    if magic != _PREP_MAGIC or ver != 1 or kind not in _PREP_KIND.values():
        return (False, None)
    if n_obj > 4096 or n_active > 4096:
        return (False, None)
    exp_len = _PREP.size + n_obj * 32 + n_active * 8 + _PREP_TAG
    if len(blob) != exp_len:                              # comprimento EXATO (sem cauda extra)
        return (False, None)
    key = keyring.get(key_id)
    if key is None:
        return (False, None)
    exp = hmac.new(key, b"HORIZONGCPREP" + blob[:-_PREP_TAG], hashlib.sha256).digest()[:_PREP_TAG]
    if not hmac.compare_digest(exp, blob[-_PREP_TAG:]):
        return (False, None)
    off = _PREP.size
    objs = [blob[off + i * 32:off + (i + 1) * 32] for i in range(n_obj)]
    off += n_obj * 32
    actives = []
    for i in range(n_active):
        sc, seg = struct.unpack_from("<II", blob, off + i * 8)
        actives.append((sc, seg))
    return (True, PreparedRoots(tuple(objs), tuple(actives)))


class PreparedRegistry:
    """Registro durável/autenticado de operações preparadas VIVAS (E4). Só pacotes SELADos com digest
    recomputado registram roots; a remoção acontece ao publicar/ativar/descartar o prepared."""

    def __init__(self, publication_store):
        self.pub = publication_store
        self.scope_id = publication_store.scope_id
        self.dir = Path(publication_store.directory) / "gc-prepared"
        self.dir.mkdir(parents=True, exist_ok=True)

    def register_compaction(self, name: str, prepared, keyring, *, best_effort: bool = False) -> None:
        from horizon_memory._engine.horizon_compaction import (
            compaction_prepared_digest, is_sealed_compaction)
        if not valid_name(name):
            raise ValueError("nome de prepared inválido")
        if not is_sealed_compaction(prepared):           # digest RECOMPUTADO exige selo íntegro
            raise ValueError("CompactionPrepared não selado / digest divergente")
        _ = compaction_prepared_digest(prepared)         # recomputa (falha estrutural já barrada por is_sealed)
        na = prepared.next_active_descriptor
        objs = [d.sha256 for _k, d in prepared.base_descriptors] + [prepared.candidate_manifest_sha256]
        self._write(name, "compaction", na.key_id, objs, [na.segment_id], keyring, best_effort=best_effort)

    def register_rotation(self, name: str, prepared, keyring, *, best_effort: bool = False) -> None:
        from horizon_memory._engine.horizon_rotation import is_sealed_prepared
        if not valid_name(name):
            raise ValueError("nome de prepared inválido")
        if not is_sealed_prepared(prepared):
            raise ValueError("RotationPrepared não selado")
        old, na = prepared.old_sealed_descriptor, prepared.next_active_descriptor
        objs = [old.sealed_object_sha256]                # o novo SEALED (objeto ainda não publicado no CURRENT)
        self._write(name, "rotation", na.key_id, objs, [na.segment_id], keyring, best_effort=best_effort)

    def _write(self, name, kind, key_id, obj_digests, active_segids, keyring, *, best_effort):
        key = keyring.get(key_id)
        if key is None:
            raise ValueError("key_id desconhecido para prepared")
        blob = _prepared_record_blob(kind, key_id, obj_digests, active_segids, self.scope_id, key)
        _durable_write_record(self.dir / f"{name}.prep", blob, best_effort=best_effort)

    def unregister(self, name: str) -> None:
        if not valid_name(name):
            return
        try:
            os.unlink(str(self.dir / f"{name}.prep"))
        except FileNotFoundError:
            return
        fsync_dir(self.dir, best_effort=True)

    def scan(self, keyring) -> tuple:
        """(ScanState, PreparedRoots|None). Um único registro ilegível/corrompido ABORTA o GC (nunca é
        tratado como ausência)."""
        objs, actives = set(), set()
        if not self.dir.is_dir():
            return (ScanState.VALID, PreparedRoots())
        for p in sorted(self.dir.glob("*.prep")):
            if not valid_name(p.stem):
                return (ScanState.CORRUPT, None)
            rst, data = _read_record_limited(p)
            if rst != ScanState.VALID:
                return (rst, None)
            ok, pr = _parse_prepared_record(data, keyring)
            if not ok:
                return (ScanState.CORRUPT, None)
            objs.update(pr.object_sha256s)
            actives.update(pr.active_segments)
        return (ScanState.VALID, PreparedRoots(tuple(objs), tuple(actives)))


# ================= E1: pin / unpin seguro de snapshots =================
# Ordem GLOBAL de locks (FH-03.1 §1), para evitar deadlock:
#     writer lease → publish.lock → maintenance/gc lock (.gc.lock) → pin.lock
# A CAPTURA de snapshot segura `pin.lock` COMPARTILHADO durante `CURRENT → registrar pin → open_generation`.
# O GC segura `pin.lock` EXCLUSIVO da SEGUNDA leitura de raízes até terminar os renames — assim nenhuma
# captura conclui no meio da execução do GC (ou o GC espera, ou a captura espera).
#
# Dois tipos de pin (E1 §1): EFÊMERO de processo (flock — liberado no crash) e NOMEADO persistente
# (registro autenticado). Layout sob `<publication_store.directory>/gc-pins/`:
#   eph/<digest>.<pid>.<rand>   — lock efêmero (o processo mantém flock EX; GC prova morte com EX-NB)
#   named/<name>.pin            — registro autenticado {name, publication_sha256, key_id, mac}
_PIN_LOCK = "pin.lock"
_PIN_MAGIC = b"HPIN"
_PIN = struct.Struct("<4sH32sI")     # magic, ver, publication_sha256(32s), key_id(I)
_PIN_TAG = 16


def _pins_dir(publication_store) -> Path:
    return Path(publication_store.directory) / "gc-pins"


class EphemeralPin:
    """Pin de processo: mantém um flock EXCLUSIVO num arquivo `eph/<digest>.<pid>.<rand>`. Se o processo
    morre, o SO libera o flock e o GC prova a morte adquirindo EX não-bloqueante. `close()`/GC recuperam
    o arquivo."""

    def __init__(self, path: str, fd: int, publication_sha256: bytes):
        self.path = path
        self._fd = fd
        self.publication_sha256 = publication_sha256
        self.held = True

    def close(self) -> None:
        if not self.held:
            return
        self.held = False
        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(self._fd)
        try:
            os.unlink(self.path)                 # remoção do próprio lock (unpin idempotente)
        except FileNotFoundError:
            pass


def _named_pin_blob(name: str, publication_sha256: bytes, key_id: int, key: bytes) -> bytes:
    body = _PIN.pack(_PIN_MAGIC, 1, publication_sha256, key_id)
    name_b = name.encode()
    body += struct.pack("<H", len(name_b)) + name_b
    mac = hmac.new(key, b"HORIZONGCPIN" + body, hashlib.sha256).digest()[:_PIN_TAG]
    return body + mac


def _parse_named_pin(blob: bytes, keyring):
    if len(blob) < _PIN.size + 2 + _PIN_TAG:
        return None
    magic, ver, psha, key_id = _PIN.unpack_from(blob, 0)
    if magic != _PIN_MAGIC or ver != 1:
        return None
    off = _PIN.size
    (nlen,) = struct.unpack_from("<H", blob, off)
    off += 2
    if len(blob) != off + nlen + _PIN_TAG:
        return None
    key = keyring.get(key_id)
    if key is None:
        return None
    exp = hmac.new(key, b"HORIZONGCPIN" + blob[:-_PIN_TAG], hashlib.sha256).digest()[:_PIN_TAG]
    if not hmac.compare_digest(exp, blob[-_PIN_TAG:]):
        return None
    return (bytes(psha), key_id, blob[off:off + nlen].decode())


class PinRegistry:
    """Registro de pins de um scope. Efêmeros por flock; nomeados por registro autenticado durável."""

    def __init__(self, publication_store):
        self.pub = publication_store
        self.dir = _pins_dir(publication_store)
        (self.dir / "eph").mkdir(parents=True, exist_ok=True)
        (self.dir / "named").mkdir(parents=True, exist_ok=True)

    def pin_ephemeral(self, publication_sha256: bytes) -> EphemeralPin:
        """E1 §4 — protocolo de captura: o caller já leu o CURRENT; aqui REGISTRA o digest com um flock
        EXCLUSIVO antes de abrir o snapshot. O lock vive enquanto o processo viver."""
        name = f"{publication_sha256.hex()}.{os.getpid():x}.{os.urandom(4).hex()}"
        path = self.dir / "eph" / name
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(str(path), flags, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        fsync_dir(self.dir / "eph", best_effort=True)
        return EphemeralPin(str(path), fd, publication_sha256)

    def pin_named(self, name: str, publication_sha256: bytes, key_id: int, keyring, *,
                  best_effort: bool = False) -> None:
        """Pin persistente NOMEADO: registro autenticado, gravado DURAVELMENTE (§3: nome canônico, temp
        O_EXCL → write-all → fsync → rename → fsync_dir)."""
        if not valid_name(name):
            raise ValueError("nome de pin inválido")
        key = keyring.get(key_id)
        if key is None:
            raise ValueError("key_id desconhecido para pin nomeado")
        blob = _named_pin_blob(name, publication_sha256, key_id, key)
        _durable_write_record(self.dir / "named" / f"{name}.pin", blob, best_effort=best_effort)

    def unpin_named(self, name: str) -> None:
        """Idempotente (E1 §5): remover um pin ausente é um no-op."""
        if not valid_name(name):
            return
        try:
            os.unlink(str(self.dir / "named" / f"{name}.pin"))
        except FileNotFoundError:
            return
        fsync_dir(self.dir / "named", best_effort=True)

    def scan(self, keyring) -> tuple:
        """FH-03.1 §2 — (ScanState, set_de_digests|None). Digests VIVOS: nomeados válidos + efêmeros com
        flock preso. Um pin NOMEADO ilegível/truncado/MAC inválido/nome não-canônico/enorme → `CORRUPT`
        (ou IO/limite), o que ABORTA o GC. Corrupção NUNCA é interpretada como unpin (perda silenciosa)."""
        out = set()
        nd = self.dir / "named"
        if nd.is_dir():
            for p in sorted(nd.glob("*.pin")):
                if not valid_name(p.stem):
                    return (ScanState.CORRUPT, None)
                rst, data = _read_record_limited(p)
                if rst != ScanState.VALID:
                    return (rst, None)
                parsed = _parse_named_pin(data, keyring)
                if parsed is None:
                    return (ScanState.CORRUPT, None)
                out.add(parsed[0])
        ed = self.dir / "eph"
        if ed.is_dir():
            for p in ed.glob("*"):
                if not p.is_file():
                    continue
                digest_hex = p.name.split(".", 1)[0]
                if self._ephemeral_alive(p):
                    try:
                        out.add(bytes.fromhex(digest_hex))
                    except ValueError:
                        pass
        return (ScanState.VALID, out)

    def live_pinned_digests(self, keyring) -> set:
        """Conveniência (não fail-closed): só os digests vivos, ignorando registros corrompidos. O GC usa
        `scan()` (que aborta em corrupção); esta é para inspeção/planejamento não destrutivo."""
        st, digests = self.scan(keyring)
        return digests if st == ScanState.VALID and digests is not None else set()

    def _ephemeral_alive(self, path: Path) -> bool:
        """True se o flock EX está preso por OUTRO processo (pin vivo). Não bloqueia; um EX-NB que
        SUCEDE prova que ninguém segura → pin morto (crashado)."""
        try:
            fd = os.open(str(path), os.O_RDWR)
        except OSError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False                         # adquiriu → ninguém segura → morto
        except OSError as e:
            return e.errno in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES)   # preso → vivo
        finally:
            os.close(fd)

    def reap_dead_ephemeral(self) -> int:
        """Remove locks efêmeros de processos mortos (crashados). Só o GC (sob o lock de manutenção) o
        chama. Retorna quantos foram removidos."""
        ed = self.dir / "eph"
        n = 0
        if not ed.is_dir():
            return 0
        for p in ed.glob("*"):
            if p.is_file() and not self._ephemeral_alive(p):
                try:
                    os.unlink(str(p))
                    n += 1
                except FileNotFoundError:
                    pass
        return n


def capture_snapshot(publication_store, object_store, wal_store, keyring, registry, *, name=None):
    """E1 §4 + FH-03.1 §1 — protocolo de captura sob `pin.lock` COMPARTILHADO: adquire o lock shared,
    LÊ o CURRENT, REGISTRA o digest do pin e ABRE a geração — só então libera o lock. Enquanto o GC
    segura `pin.lock` EXCLUSIVO (2ª leitura → fim dos renames), esta captura BLOQUEIA (ou o GC espera);
    assim nenhuma captura conclui no meio da execução do GC. Com `name` cria um pin NOMEADO persistente;
    sem `name`, um EFÊMERO de processo. Devolve `(handle, publication_sha256, pin)`."""
    from horizon_memory._engine.horizon_manifest import open_generation
    from horizon_memory._engine.horizon_publication import open_published_cursor, CursorState
    pin_fd = open_hardened_lock(Path(publication_store.directory), _PIN_LOCK)
    fcntl.flock(pin_fd, fcntl.LOCK_SH)                        # §1: captura sob lock COMPARTILHADO
    try:
        cst, cursor, why = open_published_cursor(publication_store, object_store, keyring)
        if cst != CursorState.VALID:
            return (None, None, None)
        pub_digest = cursor.proof.publication_sha256
        pin = None
        if name is not None:
            registry.pin_named(name, pub_digest, cursor.manifest.key_id, keyring)
        else:
            pin = registry.pin_ephemeral(pub_digest)
        og = open_generation(cursor.manifest_blob, object_store, wal_store, keyring)
        if og.state != OpenGenerationState.VALID:
            if pin is not None:
                pin.close()
            return (None, None, None)
        return (og.handle, pub_digest, pin)
    finally:
        fcntl.flock(pin_fd, fcntl.LOCK_UN)
        os.close(pin_fd)


# ================= E2/E3: protocolo conservador + quarantine + journal =================
# Ordem GLOBAL de locks (E2 §1): writer lease → publish.lock → maintenance/gc lock → pin lock.
_GC_LOCK = ".gc.lock"
_QUARANTINE = "gc-quarantine"
_JOURNALS = "journals"
_JOURNAL_MAGIC = b"HGCJ"
_JRN = struct.Struct("<4sH32sI")     # magic, ver, plan_digest(32s), n_items(I)


class GcRunState(Enum):
    DONE = 0                     # plano executado (quarantine aplicada)
    NO_CURRENT = 1
    NOTHING_TO_COLLECT = 2
    STALE_PLAN = 3              # CURRENT/pins mudaram entre plano e execução → nada movido
    ABORTED = 4                 # plano/registro abortou (incerteza) → nada movido
    GC_BUSY = 5                # outro GC ativo (lock exclusivo ocupado)
    MOVE_FAILED = 6            # falha física (EIO/ENOSPC/fsync) num rename → NUNCA vira DONE (§10)


@dataclass(frozen=True)
class GcRunResult:
    state: GcRunState
    plan: GcPlan | None
    quarantined: tuple = ()
    reason: str = ""


def _authenticated_journal(plan_digest: bytes, items, key: bytes) -> bytes:
    items = tuple(sorted(set(items)))
    header = _JRN.pack(_JOURNAL_MAGIC, 1, plan_digest, len(items))
    body = b"".join(struct.pack("<BH", 0 if kind == OBJECT else 1, len(key_s.encode())) + key_s.encode()
                    for kind, key_s in items)
    mac = hmac.new(key, b"HORIZONGCJRN" + header + body, hashlib.sha256).digest()[:16]
    return header + body + mac


def _parse_journal(blob: bytes, key: bytes):
    """FH-03.1 §8 — parser SEGURO: comprimento mínimo, MAC, e travessia contra truncamento, comprimento
    falso e cauda extra. `None` para qualquer anomalia (o chamador conserva a quarantine, fail-closed)."""
    if len(blob) < _JRN.size + 16:
        return None
    magic, ver, plan_digest, n = _JRN.unpack_from(blob, 0)
    if magic != _JOURNAL_MAGIC or ver != 1 or n > 1_000_000:
        return None
    exp = hmac.new(key, b"HORIZONGCJRN" + blob[:-16], hashlib.sha256).digest()[:16]
    if not hmac.compare_digest(exp, blob[-16:]):
        return None
    items, off, end = [], _JRN.size, len(blob) - 16
    for _ in range(n):
        if off + 3 > end:
            return None                                  # truncado
        k, ln = struct.unpack_from("<BH", blob, off)
        off += 3
        if k not in (0, 1) or off + ln > end:
            return None                                  # comprimento falso
        try:
            key_s = blob[off:off + ln].decode()
        except UnicodeDecodeError:
            return None
        off += ln
        items.append((OBJECT if k == 0 else ACTIVE, key_s))
    if off != end:
        return None                                      # cauda extra
    if items != sorted(set(items)):
        return None                                      # representação não canônica/duplicada
    for kind, key_s in items:
        if kind == OBJECT and (len(key_s) != 64 or any(c not in "0123456789abcdef" for c in key_s)):
            return None
    return (bytes(plan_digest), items)


def _registry_pair(publication_store, pin_registry, prepared_registry):
    """FH-03.2: registries operacionais nunca são opcionais semanticamente. Quando o caller não fornece
    instâncias, elas são derivadas do PublicationStore e portanto registros já persistidos em disco
    continuam sendo autoridade."""
    pins = pin_registry if pin_registry is not None else PinRegistry(publication_store)
    prepared = prepared_registry if prepared_registry is not None else PreparedRegistry(publication_store)
    if pins.pub.scope_id != publication_store.scope_id or prepared.scope_id != publication_store.scope_id:
        raise ValueError("registry de outro scope")
    return pins, prepared


def _journal_path(object_store, plan_digest: bytes) -> Path:
    return Path(object_store.root) / _QUARANTINE / _JOURNALS / f"{plan_digest.hex()}.hgcj"


def _publish_journal_immutable(object_store, plan_digest: bytes, blob: bytes, *, best_effort=False):
    """Publica um journal imutável por plan_digest. Um existente só é aceito se for byte-idêntico."""
    path = _journal_path(object_store, plan_digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        st, old = _read_record_limited(path)
        if st != ScanState.VALID or old != blob:
            raise OSError("journal existente divergente")
        return path
    tmp = path.parent / f".{path.name}.tmp.{os.urandom(6).hex()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(tmp), flags, 0o600)
    try:
        mv, n = memoryview(blob), 0
        while n < len(blob):
            try:
                w = os.write(fd, mv[n:])
            except InterruptedError:
                continue
            if w <= 0:
                raise OSError("write incompleto")
            n += w
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        try:
            os.unlink(str(tmp))
        except FileNotFoundError:
            pass
        raise
    else:
        os.close(fd)
    try:
        os.link(str(tmp), str(path))                 # exclusivo; nunca substitui journal anterior
    except FileExistsError:
        st, old = _read_record_limited(path)
        if st != ScanState.VALID or old != blob:
            raise OSError("journal concorrente divergente")
    finally:
        try:
            os.unlink(str(tmp))
        except FileNotFoundError:
            pass
    fsync_dir(path.parent, best_effort=best_effort)
    return path


def _quarantine_paths(object_store, wal_store, item):
    """(origem, destino_quarantine) de um item. SÓ objetos content-addressed vão para quarantine no E3
    inicial (ACTIVE nunca é coletado enquanto declarado). Devolve None para itens não movíveis."""
    kind, key = item
    if kind != OBJECT:
        return None
    src = Path(object_store.root) / "objects" / f"{key}.hobj"
    qdir = Path(object_store.root) / _QUARANTINE
    return (src, qdir / f"{key}.hobj")


def _move_file(src: Path, dst: Path, *, best_effort: bool = False) -> tuple:
    """FH-03.1 §10 — rename atômico na MESMA fs + fsync dos dois diretórios. STRICT por padrão: qualquer
    `OSError` (EIO/ENOSPC) no rename, ou falha de `fsync_dir` quando NÃO `best_effort`, retorna
    `(False, motivo)` — o chamador NUNCA pode terminar como DONE. Idempotente: origem sumiu e destino
    existe → `(True, ...)`. (ok, reason)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.rename(str(src), str(dst))
    except FileNotFoundError:
        return (dst.exists(), "idempotente" if dst.exists() else "origem ausente")
    except OSError as e:
        return (False, f"rename errno {getattr(e, 'errno', '?')}")
    try:
        fsync_dir(src.parent, best_effort=best_effort)
        fsync_dir(dst.parent, best_effort=best_effort)
    except OSError as e:
        if not best_effort:
            return (False, f"fsync_dir errno {getattr(e, 'errno', '?')}")
    return (True, "ok")


def _gather_roots(publication_store, object_store, keyring, *, pin_registry, prepared_registry,
                  retention, limits):
    """(GcState|ScanState-aborted, GcRoots|None, live_pins, reason). Coleta as raízes de forma FAIL-CLOSED:
    escaneia pins (§2) e prepared (§4) — corrupção em qualquer um ABORTA; nunca trata corrupção como
    ausência."""
    if pin_registry is not None:
        pst, live_pins = pin_registry.scan(keyring)
        if pst != ScanState.VALID:
            return (GcState.ABORTED_CORRUPT, None, (), f"pins: {pst.name}")
    else:
        live_pins = set()
    if prepared_registry is not None:
        prst, prep_roots = prepared_registry.scan(keyring)
        if prst != ScanState.VALID:
            return (GcState.ABORTED_CORRUPT, None, (), f"prepared: {prst.name}")
    else:
        prep_roots = PreparedRoots()
    st, roots, why = compute_roots(publication_store, object_store, keyring,
                                   pinned_publication_sha256s=tuple(live_pins), retention=retention,
                                   prepared_roots=prep_roots, limits=limits)
    return (st, roots, tuple(live_pins), why)


def _root_signature(publication_store, keyring, pin_registry, prepared_registry):
    """Assinatura FAIL-CLOSED das raízes vivas (CURRENT + pins + prepared) — `None` se algo estiver
    corrompido, o que força um veredito distinto (não STALE silencioso)."""
    cst, cur, _ = read_current(publication_store.directory, keyring)
    current = cur.publication_sha256 if cst == PublicationState.VALID else None
    pst, pins = (pin_registry.scan(keyring) if pin_registry is not None else (ScanState.VALID, set()))
    prst, prep = (prepared_registry.scan(keyring) if prepared_registry is not None
                  else (ScanState.VALID, PreparedRoots()))
    if pst != ScanState.VALID or prst != ScanState.VALID:
        return None
    return (current, frozenset(pins), frozenset(prep.object_sha256s), frozenset(prep.active_segments))


def run_gc(publication_store, wal_store, object_store, keyring, *, pin_registry=None,
           prepared_registry=None, retention: int = 1, journal_key_id: int = 0, limits=None,
           best_effort: bool = False, failpoint=None) -> GcRunResult:
    """E2+E3+FH-03.1 — protocolo conservador. Ordem de locks: `.gc.lock` (manutenção, EXCLUSIVO — dois GCs
    nunca simultâneos) → `pin.lock` (EXCLUSIVO da 2ª leitura de raízes até o fim dos renames). Coleta as
    raízes FAIL-CLOSED (pins/prepared autenticados; corrupção ABORTA), constrói o plano PURO, RELÊ as
    raízes e aborta `STALE_PLAN` se mudaram, grava um `GcJournal` autenticado e move os objetos
    INALCANÇÁVEIS para quarantine na MESMA fs em modo STRICT (falha física → `MOVE_FAILED`, nunca DONE).
    NÃO apaga definitivamente; ACTIVE declarado nunca é coletado."""
    lim = limits or {}
    directory = Path(publication_store.directory)
    fp = failpoint or (lambda s: None)
    try:
        pin_registry, prepared_registry = _registry_pair(
            publication_store, pin_registry, prepared_registry)
    except (TypeError, ValueError) as e:
        return GcRunResult(GcRunState.ABORTED, None, (), f"registries: {e}")
    gc_fd = open_hardened_lock(directory, _GC_LOCK)
    pin_fd = None
    try:
        try:
            fcntl.flock(gc_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)     # dois GCs não executam juntos (gate)
        except OSError:
            return GcRunResult(GcRunState.GC_BUSY, None, (), "outro GC ativo")
        if pin_registry is not None:
            pin_registry.reap_dead_ephemeral()
        st, roots, live_pins, why = _gather_roots(publication_store, object_store, keyring,
                                                  pin_registry=pin_registry,
                                                  prepared_registry=prepared_registry,
                                                  retention=retention, limits=lim)
        if st == GcState.NO_CURRENT:
            return GcRunResult(GcRunState.NO_CURRENT, None, (), "CURRENT ausente")
        if st != GcState.PLANNED:
            return GcRunResult(GcRunState.ABORTED, None, (), f"raízes: {why}")
        sig_before = _root_signature(publication_store, keyring, pin_registry, prepared_registry)
        plan = plan_reachability(publication_store, wal_store, object_store, keyring, roots, limits=lim)
        if plan.state != GcState.PLANNED:
            return GcRunResult(GcRunState.ABORTED, plan, (), f"plano: {plan.reason}")
        # §1 — pin.lock EXCLUSIVO da 2ª leitura de raízes até o fim dos renames (bloqueia captura no meio)
        pin_fd = open_hardened_lock(directory, _PIN_LOCK)
        fcntl.flock(pin_fd, fcntl.LOCK_EX)
        fp("after_plan_before_reread")                            # janela p/ mudar raízes (testes)
        sig_after = _root_signature(publication_store, keyring, pin_registry, prepared_registry)
        if sig_before is None or sig_after is None or sig_after != sig_before:
            state = GcRunState.ABORTED if (sig_before is None or sig_after is None) else GcRunState.STALE_PLAN
            return GcRunResult(state, plan, (), "raízes mudaram/ilegíveis entre plano e execução")
        movable = [it for it in sorted(plan.unreachable) if it[0] == OBJECT]
        if not movable:
            return GcRunResult(GcRunState.NOTHING_TO_COLLECT, plan, (), "nada elegível")
        jkey = keyring.get(journal_key_id)
        if jkey is None:
            return GcRunResult(GcRunState.ABORTED, plan, (), "journal key_id desconhecido")
        qdir = Path(object_store.root) / _QUARANTINE
        qdir.mkdir(parents=True, exist_ok=True)
        journal_blob = _authenticated_journal(plan.plan_digest, movable, jkey)
        fp("before_journal")
        try:
            _publish_journal_immutable(object_store, plan.plan_digest, journal_blob,
                                       best_effort=best_effort)
        except OSError as e:
            return GcRunResult(GcRunState.MOVE_FAILED, plan, (), f"journal: {e}")
        fp("after_journal")
        moved = []
        for idx, it in enumerate(movable):
            paths = _quarantine_paths(object_store, wal_store, it)
            fp(f"before_move_{idx}")
            if paths is not None:
                ok, mreason = _move_file(paths[0], paths[1], best_effort=best_effort)
                if not ok:                                       # §10: falha física NUNCA vira DONE
                    return GcRunResult(GcRunState.MOVE_FAILED, plan, tuple(moved),
                                       f"rename de {it[1][:12]}: {mreason}")
                moved.append(it)
            fp(f"after_move_{idx}")
        return GcRunResult(GcRunState.DONE, plan, tuple(moved), "quarantine aplicada")
    finally:
        if pin_fd is not None:
            fcntl.flock(pin_fd, fcntl.LOCK_UN)
            os.close(pin_fd)
        fcntl.flock(gc_fd, fcntl.LOCK_UN)
        os.close(gc_fd)


def recover_gc(publication_store, wal_store, object_store, keyring, *, retention: int = 1,
               pin_registry=None, prepared_registry=None, journal_key_id: int = 0, limits=None,
               best_effort: bool = False) -> dict:
    """E3 §4 + §8 — recovery GOVERNADO pelo `GcJournal` AUTENTICADO, idempotente. Sem journal → nada a
    recuperar. Journal inválido/forjado/truncado → CONSERVA a quarantine e retorna `state='fail_closed'`
    (nunca restaura/move com base num journal não autenticado). Só objetos LISTADOS no journal que
    VOLTARAM a ser alcançáveis são restaurados; o resto permanece em quarantine. Nunca apaga."""
    lim = limits or {}
    qdir = Path(object_store.root) / _QUARANTINE
    result = {"restored": [], "kept": [], "state": "ok"}
    try:
        pin_registry, prepared_registry = _registry_pair(
            publication_store, pin_registry, prepared_registry)
    except (TypeError, ValueError):
        result["state"] = "fail_closed"
        return result
    gc_fd = open_hardened_lock(Path(publication_store.directory), _GC_LOCK)
    pin_fd = None
    try:
        try:
            fcntl.flock(gc_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            result["state"] = "gc_busy"
            return result
        pin_fd = open_hardened_lock(Path(publication_store.directory), _PIN_LOCK)
        fcntl.flock(pin_fd, fcntl.LOCK_EX)
        jdir = qdir / _JOURNALS
        journals = sorted(jdir.glob("*.hgcj")) if jdir.is_dir() else []
        # compatibilidade de recovery com o journal singleton antigo, sem voltar a sobrescrevê-lo.
        legacy = qdir / "gc-journal.hgcj"
        if legacy.is_file():
            journals.append(legacy)
        if not journals:
            return result
        jkey = keyring.get(journal_key_id)
        parsed_journals = []
        for jpath in journals:
            jst, jdata = _read_record_limited(jpath)
            parsed = _parse_journal(jdata, jkey) if (jst == ScanState.VALID and jkey is not None) else None
            if parsed is None:
                result["state"] = "fail_closed"
                return result
            plan_digest, items = parsed
            if jpath.parent == jdir and jpath.stem != plan_digest.hex():
                result["state"] = "fail_closed"          # filename é o vínculo imutável do plan digest
                return result
            parsed_journals.append((plan_digest, items))
        # Um objeto que voltou a ser raiz (por exemplo, um snapshot nomeado antigo) pode estar ele
        # próprio na quarantine. Nesse caso não é possível calcular sua closure enquanto ele estiver
        # ausente de objects/. Restauramos conservadoramente TODOS os itens autenticados antes de
        # recalcular a alcançabilidade; depois devolvemos à quarantine apenas os que continuam
        # inalcançáveis. Crash nesta janela causa somente vazamento seguro em objects/, nunca perda.
        objdir = Path(object_store.root) / "objects"
        all_items = sorted(set(it for _pd, items in parsed_journals for it in items))
        restored_temporarily = set()
        for kind, key_s in all_items:
            if kind != OBJECT:
                continue
            qp = qdir / f"{key_s}.hobj"
            if not qp.is_file():
                continue
            ok, _r = _move_file(qp, objdir / qp.name, best_effort=best_effort)
            if not ok:
                result["state"] = "move_failed"
                return result
            restored_temporarily.add(key_s)

        st, roots, _live, _why = _gather_roots(
            publication_store, object_store, keyring, pin_registry=pin_registry,
            prepared_registry=prepared_registry, retention=retention, limits=lim)
        if st != GcState.PLANNED:
            result["state"] = "fail_closed"
            return result
        plan = plan_reachability(publication_store, wal_store, object_store, keyring, roots, limits=lim)
        if plan.state != GcState.PLANNED:
            result["state"] = "fail_closed"
            return result
        reachable = plan.reachable
        for kind, key_s in all_items:
            if kind != OBJECT:
                continue
            item = obj_item(key_s)
            if item in reachable:
                if key_s in restored_temporarily:
                    result["restored"].append(key_s)
            else:
                op = objdir / f"{key_s}.hobj"
                qp = qdir / op.name
                if op.is_file():
                    ok, _r = _move_file(op, qp, best_effort=best_effort)
                    if not ok:
                        result["state"] = "move_failed"
                        return result
                result["kept"].append(key_s)
        return result
    finally:
        if pin_fd is not None:
            fcntl.flock(pin_fd, fcntl.LOCK_UN)
            os.close(pin_fd)
        fcntl.flock(gc_fd, fcntl.LOCK_UN)
        os.close(gc_fd)
