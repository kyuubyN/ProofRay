# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
""""Connect a database" -- same bring-your-own-database pattern as the other examples in this
folder, this time against DuckDB. DuckDB is embedded (like SQLite) -- there's no server to run
at all, in-memory by default, so this example needs nothing beyond `pip install duckdb`.

Swap the `SELECT` for your own table/query (or point `duckdb.connect(...)` at a real `.duckdb`
file instead of `:memory:`) and the rest of this example is unchanged.

This example calls `ProofRayAnswerEngine` (the AGPL core) directly, hence the AGPL header --
see `../LICENSE_COMMERCIAL_PLACEHOLDER.md`.

Run: python3 "ProofRay Engine/examples/duckdb_documents_example.py"
Requires: pip install duckdb
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from proofray import DEFAULT_PROFILE, ProofRayAnswerEngine, RouteDocument

SCOPE_ID = 1
SESSION_ID = "duckdb-example"
TABLE_NAME = "articles"

_FIXTURE_ROWS = (
    "The Meridian project reduced compute cost by exactly 42 percent compared to "
    "the previous baseline architecture across every workload.",
    "Meridian's cost reduction came from a redesigned caching layer that "
    "eliminated redundant recomputation across adjacent pipeline stages.",
    "The Solstice project, unrelated to Meridian, focuses on latency instead of cost.",
)


def _seed_fixture(conn) -> None:
    """Stands in for rows someone already wrote to your real table -- delete this call, point
    `duckdb.connect(...)` at your own file, and query your own table instead."""
    conn.execute(f"CREATE TABLE {TABLE_NAME} (id INTEGER, body TEXT)")
    conn.executemany(
        f"INSERT INTO {TABLE_NAME} VALUES (?, ?)",
        [(i, body) for i, body in enumerate(_FIXTURE_ROWS, start=1)],
    )


def _documents_from_duckdb(conn) -> tuple[RouteDocument, ...]:
    rows = conn.execute(f"SELECT id, body FROM {TABLE_NAME} ORDER BY id").fetchall()
    documents = []
    for row_id, body in rows:
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
    import duckdb

    conn = duckdb.connect(":memory:")
    try:
        _seed_fixture(conn)
        documents = _documents_from_duckdb(conn)
    finally:
        conn.close()

    engine = ProofRayAnswerEngine(profile=DEFAULT_PROFILE, scope_id=SCOPE_ID, session_id=SESSION_ID)
    result = engine.answer("What percent did the Meridian project reduce cost by?", documents)

    print("state:", result.state)
    print("answer:", result.answer_text)
    print(f"(sourced from {result.documents_considered} documents via duckdb :memory:)")


if __name__ == "__main__":
    main()
