# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
""""Connect a database" -- same bring-your-own-database pattern as the other examples in this
folder, this time against SpacetimeDB.

SpacetimeDB is different from every other example here in one real way: there is no maintained
official Python SDK (Clockwork Labs' own client libraries are TypeScript/Rust/C#/C++); the
supported way to reach it from Python is its HTTP interface, specifically the SQL-over-HTTP
endpoint (`POST /database/<name>/sql`, raw SQL as the request body). This example uses that
endpoint directly via `requests` -- no extra client library needed. The exact response shape has
changed across SpacetimeDB versions; `_query_rows` below handles the two shapes seen in the
wild, but if it errors on your install, check your version's own HTTP docs and adjust it.

This requires an already-running local SpacetimeDB instance with a module already published (via
the `spacetime` CLI: `spacetime start`, then `spacetime publish <module> --server local`).
Deliberately, this script does not start a SpacetimeDB server for you -- that is a real
background process, and this project would rather you start it once, on purpose, than have an
example silently launch one. Point this script at your database with:

    SPACETIMEDB_URL="http://127.0.0.1:3000" SPACETIMEDB_DATABASE="your_module" \
        python3 "HorizonAI Engine/examples/spacetimedb_documents_example.py"

Without those set (or if the server isn't reachable), this prints setup instructions and exits
cleanly instead of failing. Also unlike every other example here, this one assumes YOUR module
already has a table with rows you want to route -- set SPACETIMEDB_TABLE (default "articles")
and it will run `SELECT * FROM <table>`, then use whichever column is named body/text/content
(checked in that order) as the document text and `id` (or the row's position) as the row id. If
your schema differs, adjust `_documents_from_spacetimedb` below -- there's no generic fixture
seeding here the way there is for Mongo/Redis, since SpacetimeDB's schema is defined by your own
published module, not something this script can create on the fly.

This example calls `HorizonAnswerEngine` (the AGPL core) directly, hence the AGPL header --
see `../LICENSE_COMMERCIAL_PLACEHOLDER.md`.

Run: python3 "HorizonAI Engine/examples/spacetimedb_documents_example.py"
Requires: pip install requests (already a HorizonAI Engine dependency)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from horizon_memory import DEFAULT_PROFILE, HorizonAnswerEngine, RouteDocument

SCOPE_ID = 1
SESSION_ID = "spacetimedb-example"
_TEXT_COLUMN_CANDIDATES = ("body", "text", "content")


def _query_rows(base_url: str, database: str, table: str) -> list[dict]:
    import requests

    headers = {"Content-Type": "text/plain"}
    token = os.environ.get("SPACETIMEDB_AUTH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = requests.post(
        f"{base_url.rstrip('/')}/database/{database}/sql",
        data=f"SELECT * FROM {table}",
        headers=headers,
        timeout=5,
    )
    response.raise_for_status()
    payload = response.json()
    # SpacetimeDB's SQL response shape has changed across versions; handle the two seen so far:
    # a plain list of row-dicts, or {"schema": [...], "rows": [[...], ...]}.
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        return payload
    if isinstance(payload, dict) and "rows" in payload and "schema" in payload:
        columns = [col["name"] if isinstance(col, dict) else col for col in payload["schema"]]
        return [dict(zip(columns, row)) for row in payload["rows"]]
    if isinstance(payload, list) and not payload:
        return []
    raise ValueError(f"Unrecognized SpacetimeDB SQL response shape: {type(payload).__name__}")


def _documents_from_spacetimedb(rows: list[dict], table: str) -> tuple[RouteDocument, ...]:
    documents = []
    for i, row in enumerate(rows):
        text_col = next((c for c in _TEXT_COLUMN_CANDIDATES if c in row), None)
        if text_col is None:
            continue
        row_id = row.get("id", i)
        fact_id = int(row_id) if str(row_id).isdigit() else (hash(str(row_id)) & 0x7FFFFFFF)
        documents.append(RouteDocument(
            fact_id=fact_id,
            text=str(row[text_col]),
            scope_id=SCOPE_ID,
            session_id=SESSION_ID,
            version=1,
            source=f"{table}:{row_id}",
        ))
    return tuple(documents)


def main() -> None:
    base_url = os.environ.get("SPACETIMEDB_URL")
    database = os.environ.get("SPACETIMEDB_DATABASE")
    if not base_url or not database:
        print("SPACETIMEDB_URL and/or SPACETIMEDB_DATABASE are not set -- nothing to connect to.")
        print("Unlike SQLite/MongoDB/Redis there is no in-process SpacetimeDB stand-in: it needs")
        print("its own server process, and this project won't start one silently on your behalf.")
        print("Start one yourself, then set both and re-run, e.g.:")
        print("  spacetime start")
        print("  spacetime publish your_module --server local")
        print('  SPACETIMEDB_URL="http://127.0.0.1:3000" SPACETIMEDB_DATABASE="your_module" \\')
        print('      python3 "HorizonAI Engine/examples/spacetimedb_documents_example.py"')
        return

    table = os.environ.get("SPACETIMEDB_TABLE", "articles")
    try:
        rows = _query_rows(base_url, database, table)
    except Exception as exc:  # noqa: BLE001 -- any of these means "not reachable/not set up yet"
        print(f"Could not query SpacetimeDB at {base_url} "
              f"(database={database!r}, table={table!r}): {exc}")
        print("Check that `spacetime start` is running, the module is published, and the table")
        print("name/column layout matches what this script expects (see the module docstring).")
        return

    documents = _documents_from_spacetimedb(rows, table)
    if not documents:
        print(f"Connected, but found no usable rows in {table!r} (need a body/text/content column).")
        return

    engine = HorizonAnswerEngine(profile=DEFAULT_PROFILE, scope_id=SCOPE_ID, session_id=SESSION_ID)
    question = os.environ.get("SPACETIMEDB_QUESTION", "What does this data say?")
    result = engine.answer(question, documents)

    print("state:", result.state)
    print("answer:", result.answer_text)
    print(f"(sourced from {result.documents_considered} documents via {base_url}/{database})")


if __name__ == "__main__":
    main()
