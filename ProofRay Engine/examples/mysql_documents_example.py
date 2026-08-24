# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
""""Connect a database" -- same bring-your-own-database pattern as the other examples in this
folder, this time against MySQL. Swap the `SELECT` for your own and the rest is unchanged.

Like Postgres (and unlike SQLite/MongoDB/Redis), there is no pure-Python, no-server stand-in for
MySQL, so this requires a real instance and, deliberately, will not start one for you. Point it
at yours with these env vars (pymysql takes keyword arguments rather than a single URL):

    MYSQL_HOST=localhost MYSQL_USER=root MYSQL_PASSWORD=secret MYSQL_DB=yourdb \
        python3 "ProofRay Engine/examples/mysql_documents_example.py"

Without MYSQL_HOST set, this prints setup instructions and exits cleanly instead of failing.

This example calls `ProofRayAnswerEngine` (the AGPL core) directly, hence the AGPL header --
see `../LICENSE_COMMERCIAL_PLACEHOLDER.md`.

Run: python3 "ProofRay Engine/examples/mysql_documents_example.py"
Requires: pip install pymysql
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from proofray import DEFAULT_PROFILE, ProofRayAnswerEngine, RouteDocument

SCOPE_ID = 1
SESSION_ID = "mysql-example"
TABLE_NAME = "articles"

_FIXTURE_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
    id INT AUTO_INCREMENT PRIMARY KEY,
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
    once MYSQL_* points at a table that already has real content."""
    cursor.execute(_FIXTURE_DDL)
    cursor.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}")
    (count,) = cursor.fetchone()
    if count == 0:
        cursor.executemany(
            f"INSERT INTO {TABLE_NAME} (body) VALUES (%s)",
            [(row,) for row in _FIXTURE_ROWS],
        )


def _documents_from_mysql(cursor) -> tuple[RouteDocument, ...]:
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
    host = os.environ.get("MYSQL_HOST")
    if not host:
        print("MYSQL_HOST is not set -- nothing to connect to, so this example won't run.")
        print("Like Postgres, there is no in-process MySQL stand-in on purpose (a real server "
              "is heavier than this demo needs, and this project won't spin one up for you).")
        print('Set it and re-run, e.g.:\n'
              '  MYSQL_HOST=localhost MYSQL_USER=root MYSQL_PASSWORD=secret MYSQL_DB=yourdb '
              'python3 "ProofRay Engine/examples/mysql_documents_example.py"')
        return

    import pymysql
    conn = pymysql.connect(
        host=host,
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ.get("MYSQL_USER", "root"),
        password=os.environ.get("MYSQL_PASSWORD", ""),
        database=os.environ.get("MYSQL_DB", "horizon_example"),
    )
    try:
        with conn.cursor() as cursor:
            _seed_if_empty(cursor)
            conn.commit()
            documents = _documents_from_mysql(cursor)
    finally:
        conn.close()

    engine = ProofRayAnswerEngine(profile=DEFAULT_PROFILE, scope_id=SCOPE_ID, session_id=SESSION_ID)
    result = engine.answer("What percent did the Meridian project reduce cost by?", documents)

    port = os.environ.get("MYSQL_PORT", "3306")
    db = os.environ.get("MYSQL_DB", "horizon_example")
    print("state:", result.state)
    print("answer:", result.answer_text)
    print(f"(sourced from {result.documents_considered} documents via mysql://{host}:{port}/{db})")


if __name__ == "__main__":
    main()
