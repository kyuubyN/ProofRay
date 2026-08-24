# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
""""Connect a database" -- ProofRay has no database backend of its own; every call takes
`documents: list[str] | tuple[RouteDocument, ...]` directly, so "connecting a database" is
entirely the caller's own query, not a ProofRay subsystem. This example builds a small SQLite
fixture, queries it, and feeds the rows straight into `ProofRayAnswerEngine` -- swap the SQLite
query for your own Postgres/MySQL/whatever query and the rest of this example is unchanged.

This example calls `ProofRayAnswerEngine` (the AGPL core) directly, hence the AGPL header --
see `../LICENSE_COMMERCIAL_PLACEHOLDER.md`.

Run: python3 "ProofRay Engine/examples/sqlite_documents_example.py"
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from proofray import DEFAULT_PROFILE, ProofRayAnswerEngine, RouteDocument

SCOPE_ID = 1
SESSION_ID = "sqlite-example"


def _build_fixture_database(path: str) -> None:
    """Stands in for your own production database -- swap this whole function for a real
    connection + SELECT in a real deployment; nothing downstream needs to know the difference."""
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE support_articles (id INTEGER PRIMARY KEY, body TEXT)")
    connection.executemany(
        "INSERT INTO support_articles (body) VALUES (?)",
        [("The Meridian project reduced compute cost by exactly 42 percent compared to the "
          "previous baseline architecture across every workload.",),
         ("Meridian's cost reduction came from a redesigned caching layer that eliminated "
          "redundant recomputation across adjacent pipeline stages.",),
         ("The Solstice project, unrelated to Meridian, focuses on latency instead of cost.",)])
    connection.commit()
    connection.close()


def _documents_from_database(path: str) -> tuple[RouteDocument, ...]:
    connection = sqlite3.connect(path)
    rows = connection.execute("SELECT id, body FROM support_articles ORDER BY id").fetchall()
    connection.close()
    return tuple(
        RouteDocument(row_id, body, SCOPE_ID, SESSION_ID, 1, f"support_articles:{row_id}")
        for row_id, body in rows)


def main() -> None:
    with tempfile.TemporaryDirectory() as workdir:
        db_path = str(Path(workdir) / "fixture.db")
        _build_fixture_database(db_path)
        documents = _documents_from_database(db_path)

        engine = ProofRayAnswerEngine(
            profile=DEFAULT_PROFILE, scope_id=SCOPE_ID, session_id=SESSION_ID)
        result = engine.answer(
            "What percent did the Meridian project reduce cost by?", documents)

        print("state:", result.state)
        print("answer:", result.answer_text)
        print(f"(sourced from {result.documents_considered} rows in a real SQLite database)")


if __name__ == "__main__":
    main()
