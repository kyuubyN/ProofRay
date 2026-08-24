# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
""""Connect a database" -- ProofRay has no database backend of its own; every call takes
`documents: list[str] | tuple[RouteDocument, ...]` directly, so "connecting a database" is
entirely the caller's own query, not a ProofRay subsystem. This example queries a PostgreSQL
table and feeds the matching rows straight into `ProofRayAnswerEngine` -- swap the `SELECT` for
your own and the rest of this example is unchanged.

Unlike the SQLite/MongoDB examples, there is no pure-Python, no-server stand-in for Postgres, so
this example requires a real Postgres instance and will not start one for you (deliberately --
no background server, no extra resource usage). Point it at yours with `POSTGRES_DSN`:

    POSTGRES_DSN="postgresql://user:pass@localhost:5432/yourdb" \
        python3 "ProofRay Engine/examples/postgres_documents_example.py"

Without `POSTGRES_DSN` set, this prints setup instructions and exits cleanly (no crash, no
server started) instead of failing.

This example calls `ProofRayAnswerEngine` (the AGPL core) directly, hence the AGPL header --
see `../LICENSE_COMMERCIAL_PLACEHOLDER.md`.

Run: python3 "ProofRay Engine/examples/postgres_documents_example.py"
Requires: pip install psycopg2-binary
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from proofray import DEFAULT_PROFILE, ProofRayAnswerEngine, RouteDocument

SCOPE_ID = 1
SESSION_ID = "postgres-example"
TABLE_NAME = "articles"

_FIXTURE_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    id SERIAL PRIMARY KEY,
    body TEXT NOT NULL
);
"""

_FIXTURE_ROWS = (
    "The Meridian project reduced compute cost by exactly 42 percent compared to "
    "the previous baseline architecture across every workload.",
    "Meridian's cost reduction came from a redesigned caching layer that "
    "eliminated redundant recomputation across adjacent pipeline stages.",
    "The Solstice project, unrelated to Meridian, focuses on latency instead of cost.",
)


def _seed_if_empty(cursor) -> None:
    """Stands in for rows someone already wrote to your real table -- delete this call entirely
    once POSTGRES_DSN points at a table that already has real content."""
    cursor.execute(_FIXTURE_DDL)
    cursor.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
    (count,) = cursor.fetchone()
    if count == 0:
        cursor.executemany(
            f"INSERT INTO {TABLE_NAME} (body) VALUES (%s)",
            [(row,) for row in _FIXTURE_ROWS],
        )


def _documents_from_postgres(cursor) -> tuple[RouteDocument, ...]:
    cursor.execute(f"SELECT id, body FROM {TABLE_NAME} ORDER BY id")
    documents = []
    for row_id, body in cursor.fetchall():
        documents.append(RouteDocument(
            fact_id=row_id,
            text=body,
            scope_id=SCOPE_ID,
            session_id=SESSION_ID,
            version=1,
            source=f"{TABLE_NAME}:{row_id}",
        ))
    return tuple(documents)


def main() -> None:
    dsn = os.environ.get("POSTGRES_DSN")
    if not dsn:
        print("POSTGRES_DSN is not set -- nothing to connect to, so this example won't run.")
        print("Unlike SQLite/MongoDB there is no in-process Postgres stand-in on purpose "
              "(starting a real Postgres server just for a demo isn't worth the resources).")
        print('Set it and re-run, e.g.:\n'
              '  POSTGRES_DSN="postgresql://user:pass@localhost:5432/yourdb" '
              'python3 "ProofRay Engine/examples/postgres_documents_example.py"')
        return

    import psycopg2
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cursor:
            _seed_if_empty(cursor)
            conn.commit()
            documents = _documents_from_postgres(cursor)
    finally:
        conn.close()

    engine = ProofRayAnswerEngine(profile=DEFAULT_PROFILE, scope_id=SCOPE_ID, session_id=SESSION_ID)
    result = engine.answer("What percent did the Meridian project reduce cost by?", documents)

    print("state:", result.state)
    print("answer:", result.answer_text)
    print(f"(sourced from {result.documents_considered} documents via postgres://"
          f"{dsn.split('@')[-1]})")


if __name__ == "__main__":
    main()
