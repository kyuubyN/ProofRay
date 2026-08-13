# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Primitivas de durabilidade de NOMES (V23-C0).

`os.fsync(fd)` torna o CONTEÚDO de um arquivo durável, mas não a entrada de diretório que dá nome a
ele: após um `link`/`rename`, o nome só sobrevive a uma perda de energia se o DIRETÓRIO for
`fsync`ado. Estas primitivas centralizam essa disciplina — usadas por objetos content-addressed,
manifestos, PublicationRecords, ACTIVE novo e o ponteiro CURRENT.

Para testabilidade, o `fsync` de diretório passa por `_dir_fsync`, que os testes podem substituir por
um contador (não há como observar um `fsync` real a partir do Python).
"""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

_HAS_O_DIRECTORY = hasattr(os, "O_DIRECTORY")
# SÓ estes significam "o filesystem não suporta fsync de diretório"; EIO/ENOSPC/etc são falhas REAIS.
_UNSUPPORTED_ERRNOS = frozenset(
    e for e in (getattr(errno, "EINVAL", None), getattr(errno, "ENOTSUP", None),
                getattr(errno, "EOPNOTSUPP", None)) if e is not None)


class DirectoryFsyncUnsupported(OSError):
    """O filesystem não suporta `fsync` de diretório. No perfil rigoroso, isso IMPEDE alegar
    durabilidade forte do NOME — só é tolerado com `best_effort=True` explícito."""


def _dir_fsync(fd: int) -> None:
    os.fsync(fd)


def fsync_dir(path, *, best_effort: bool = False) -> None:
    """`fsync` do diretório `path` — torna durável a criação/rename de nomes dentro dele. NUNCA
    transforma um erro real (`EIO`/`ENOSPC`/…) em falso sucesso: só `EINVAL`/`ENOTSUP`/`EOPNOTSUPP`
    (o filesystem não suporta) são tratados como "não suportado", e mesmo assim o modo estrito
    (default) levanta `DirectoryFsyncUnsupported` — durabilidade forte não pode ser alegada sem
    `best_effort=True`."""
    flags = os.O_RDONLY | (os.O_DIRECTORY if _HAS_O_DIRECTORY else 0)
    fd = os.open(str(path), flags)
    try:
        try:
            _dir_fsync(fd)
        except OSError as exc:
            if exc.errno in _UNSUPPORTED_ERRNOS:
                if best_effort:
                    return
                raise DirectoryFsyncUnsupported(exc.errno, "fsync de diretório não suportado") from exc
            raise                     # falha real do dispositivo — jamais falso sucesso
    finally:
        os.close(fd)


def ensure_durable_directory_chain(path, root, *, best_effort: bool = False) -> None:
    """Cria (se preciso) e torna durável a cadeia de diretórios de `root` até `path` (inclusive):
    cada diretório novo é `fsync`ado E o pai é `fsync`ado, para que o próprio nome do diretório
    sobreviva à perda de energia."""
    root = Path(root).resolve()
    target = Path(path).resolve()
    if root not in target.parents and root != target:
        raise ValueError("path fora de root")
    # do topo para baixo, criando e sincronizando cada nível ausente
    rel_parts = target.relative_to(root).parts
    cur = root
    for part in rel_parts:
        parent = cur
        cur = cur / part
        if not cur.exists():
            os.mkdir(str(cur))
            fsync_dir(parent, best_effort=best_effort)   # o NOME do novo dir precisa ser durável
        fsync_dir(cur, best_effort=best_effort)


class LockFileNotRegular(OSError):
    """O arquivo de lock não é um arquivo regular (um symlink/FIFO/socket/dispositivo foi plantado no
    lugar do lock). Jamais operar sobre ele — abrir/`flock` de um objeto forjado nunca dá exclusão."""


def open_hardened_lock(directory, name, *, mode: int = 0o600) -> int:
    """Abre (criando se preciso) um arquivo de lock POSIX ENDURECIDO (C3.2): `O_NOFOLLOW` recusa
    seguir um symlink plantado no lugar do lock, e após abrir um `fstat` exige `S_ISREG` — nunca um
    FIFO/socket/dispositivo. Modo 0600. Devolve o fd (o chamador é dono do `flock`/`close`); em
    qualquer falha de validação fecha o fd e levanta, sem vazar descriptor."""
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(Path(directory) / name), flags, mode)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise LockFileNotRegular(errno.EINVAL, f"lock {name!r} não é um arquivo regular")
    except BaseException:
        os.close(fd)
        raise
    return fd


def durable_link(temp, final, *, best_effort: bool = False) -> None:
    """Hard-link exclusivo `temp`→`final` (falha se `final` existe) + `fsync` do diretório de `final`
    para tornar o nome durável."""
    os.link(str(temp), str(final))
    fsync_dir(Path(final).parent, best_effort=best_effort)


def durable_replace(temp, final, *, best_effort: bool = False) -> None:
    """Rename ATÔMICO `temp`→`final` (substitui) + `fsync` do diretório — o novo alvo do nome fica
    durável. Usado pelo ponteiro CURRENT."""
    os.replace(str(temp), str(final))
    fsync_dir(Path(final).parent, best_effort=best_effort)
