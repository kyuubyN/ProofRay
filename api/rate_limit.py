# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""In-memory token-bucket rate limiter, keyed by caller (the real socket peer address, never a
client-suppliable header, so it isn't spoofable by the request itself).

Refills continuously rather than resetting on a fixed clock tick, so a caller isn't punished for
bursting right after an arbitrary reset boundary and isn't silently starved either:
`HORIZON_RATE_LIMIT_PER_MINUTE` (default 60) is deploy-time env config, so an operator who
legitimately sends a high volume of documents per call (not more calls) can raise it without a
code change. A rejected request is logged at WARNING, not silently dropped, so an operator can
tell a real rate-limit hit from a client bug.
"""
from __future__ import annotations

import logging
import os
import time
from collections import OrderedDict
from threading import RLock

logger = logging.getLogger("horizon.api.rate_limit")

_DEFAULT_PER_MINUTE = 60
# Distinct-key eviction bound, matching the same "an unauthenticated caller must never make this
# process retain unbounded state" discipline `_engine_bridge.py`'s STORE already applies -- keyed
# by real peer address, so ordinary single-machine use never approaches this.
_MAX_TRACKED_KEYS = 10_000


def configured_capacity() -> int:
    raw = os.environ.get("HORIZON_RATE_LIMIT_PER_MINUTE")
    if raw is None:
        return _DEFAULT_PER_MINUTE
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_PER_MINUTE
    return value if value > 0 else _DEFAULT_PER_MINUTE


class TokenBucket:
    __slots__ = ("capacity", "refill_per_second", "tokens", "last_check")

    def __init__(self, capacity: float, refill_per_second: float):
        self.capacity = capacity
        self.refill_per_second = refill_per_second
        self.tokens = capacity
        self.last_check = time.monotonic()

    def allow(self) -> bool:
        now = time.monotonic()
        elapsed = now - self.last_check
        self.last_check = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_second)
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


class RateLimiter:
    def __init__(self, per_minute: int | None = None):
        self._capacity = per_minute if per_minute is not None else configured_capacity()
        self._refill_per_second = self._capacity / 60.0
        self._buckets: "OrderedDict[str, TokenBucket]" = OrderedDict()
        self._lock = RLock()

    def allow(self, key: str) -> bool:
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = TokenBucket(self._capacity, self._refill_per_second)
                self._buckets[key] = bucket
                while len(self._buckets) > _MAX_TRACKED_KEYS:
                    self._buckets.popitem(last=False)
            else:
                self._buckets.move_to_end(key)
            allowed = bucket.allow()
        if not allowed:
            logger.warning(
                "rate limit exceeded for %s (limit=%s/min; set HORIZON_RATE_LIMIT_PER_MINUTE to "
                "raise it)", key, self._capacity)
        return allowed

    def reset(self) -> None:
        """Test-only: clears all tracked buckets so test files don't interfere with each other."""
        with self._lock:
            self._buckets.clear()


RATE_LIMITER = RateLimiter()
