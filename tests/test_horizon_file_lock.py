# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import os

import pytest

from horizon_memory._engine import file_lock


def test_cross_platform_file_lock_excludes_a_second_descriptor(tmp_path):
    path = tmp_path / "portable.lock"
    first = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    second = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        file_lock.flock(first, file_lock.LOCK_EX | file_lock.LOCK_NB)
        with pytest.raises(BlockingIOError):
            file_lock.flock(second, file_lock.LOCK_EX | file_lock.LOCK_NB)
        file_lock.flock(first, file_lock.LOCK_UN)
        file_lock.flock(second, file_lock.LOCK_EX | file_lock.LOCK_NB)
        file_lock.flock(second, file_lock.LOCK_UN)
    finally:
        os.close(second)
        os.close(first)
