# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
""""Connect a database" -- same bring-your-own-database pattern as the other examples in this
folder, this time against Redis. Redis has no query language of its own, so "connect a
database" here means: list the keys under your own namespace/prefix, read each value, turn it
into a `RouteDocument`. Swap the key prefix for your own and the rest is unchanged.

By default this runs against `fakeredis` (an in-process, pure-Python Redis stand-in --
`pip install fakeredis`), so it works with no server at all and is safe to run in CI. Point it
at a real deployment by setting `REDIS_URL`, e.g.:

    REDIS_URL="redis://localhost:6379/0" \
        python3 "HorizonAI Engine/examples/redis_documents_example.py"

Nothing else in this file changes -- redis-py and fakeredis expose the same client API, which is
the entire point of the bring-your-own-database pattern: Horizon never knows or cares which one
served the documents.

This example calls `HorizonAnswerEngine` (the AGPL core) directly, hence the AGPL header --
see `../LICENSE_COMMERCIAL_PLACEHOLDER.md`.

Run: python3 "HorizonAI Engine/examples/redis_documents_example.py"
Requires: pip install redis fakeredis
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from horizon_memory import DEFAULT_PROFILE, HorizonAnswerEngine, RouteDocument

SCOPE_ID = 1
SESSION_ID = "redis-example"
KEY_PREFIX = "articles:"

_FIXTURE_ROWS = (
    "The Meridian project reduced compute cost by exactly 42 percent compared to "
    "the previous baseline architecture across every workload.",
    "Meridian's cost reduction came from a redesigned caching layer that "
    "eliminated redundant recomputation across adjacent pipeline stages.",
    "The Solstice project, unrelated to Meridian, focuses on latency instead of cost.",
)


def _get_client():
    """Real deployment: set REDIS_URL and this returns a real redis-py client, unchanged below.
    No REDIS_URL: falls back to fakeredis (no server, in-process) and seeds a small fixture so
    the example is runnable standalone."""
    url = os.environ.get("REDIS_URL")
    if url:
        import redis
        return redis.from_url(url, decode_responses=True), False
    import fakeredis
    client = fakeredis.FakeStrictRedis(decode_responses=True)
    _seed_fixture(client)
    return client, True


def _seed_fixture(client) -> None:
    """Stands in for documents someone already wrote to your real database -- delete this call
    entirely once REDIS_URL points at a keyspace that already has real content."""
    for i, body in enumerate(_FIXTURE_ROWS, start=1):
        client.set(f"{KEY_PREFIX}{i}", body)


def _documents_from_redis(client) -> tuple[RouteDocument, ...]:
    documents = []
    for key in sorted(client.scan_iter(f"{KEY_PREFIX}*")):
        body = client.get(key)
        if body is None:
            continue
        documents.append(RouteDocument(
            fact_id=hash(key) & 0x7FFFFFFF,
            text=body,
            scope_id=SCOPE_ID,
            session_id=SESSION_ID,
            version=1,
            source=key,
        ))
    return tuple(documents)


def main() -> None:
    client, is_mock = _get_client()
    documents = _documents_from_redis(client)

    engine = HorizonAnswerEngine(profile=DEFAULT_PROFILE, scope_id=SCOPE_ID, session_id=SESSION_ID)
    result = engine.answer("What percent did the Meridian project reduce cost by?", documents)

    print("state:", result.state)
    print("answer:", result.answer_text)
    backend = "fakeredis (no server)" if is_mock else os.environ["REDIS_URL"].split("@")[-1]
    print(f"(sourced from {result.documents_considered} documents via {backend})")


if __name__ == "__main__":
    main()
