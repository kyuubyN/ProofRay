# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Whole-file lock compatibility boundary for POSIX and Windows."""
from __future__ import annotations

import errno
import os


LOCK_SH = 1
LOCK_EX = 2
LOCK_NB = 4
LOCK_UN = 8


if os.name != "nt":
    import fcntl as _native

    def flock(descriptor: int, operation: int) -> None:
        _native.flock(descriptor, operation)

else:  # pragma: no cover - exercised by the Windows CI/release matrix
    import msvcrt

    def _ensure_lock_byte(descriptor: int) -> None:
        if os.fstat(descriptor).st_size == 0:
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.write(descriptor, b"\0")
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)

    def flock(descriptor: int, operation: int) -> None:
        if operation & LOCK_UN:
            mode = msvcrt.LK_UNLCK
        elif operation & LOCK_EX:
            mode = msvcrt.LK_NBLCK if operation & LOCK_NB else msvcrt.LK_LOCK
        elif operation & LOCK_SH:
            mode = msvcrt.LK_NBRLCK if operation & LOCK_NB else msvcrt.LK_RLCK
        else:
            raise ValueError("unknown file-lock operation")
        _ensure_lock_byte(descriptor)
        try:
            msvcrt.locking(descriptor, mode, 1)
        except OSError as error:
            if operation & LOCK_NB and error.errno in (
                    errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise BlockingIOError(errno.EAGAIN, "file lock is already held") from None
            raise


__all__ = ["LOCK_EX", "LOCK_NB", "LOCK_SH", "LOCK_UN", "flock"]
