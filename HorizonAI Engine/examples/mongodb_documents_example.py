# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
""""Connect a database" -- Horizon has no database backend of its own; every call takes
`documents: list[str] | tuple[RouteDocument, ...]` directly, so "connecting a database" is
entirely the caller's own query, not a Horizon subsystem. This example queries a MongoDB
collection and feeds the matching documents straight into `HorizonAnswerEngine` -- swap the
`find(...)` query for your own and the rest of this example is unchanged.

By default this runs against `mongomock` (an in-process, pure-Python MongoDB stand-in --
`pip install mongomock`), so it works with no server at all and is safe to run in CI. Point it
at a real deployment by setting the `MONGODB_URI` environment variable, e.g.:

    MONGODB_URI="mongodb://localhost:27017" python3 "HorizonAI Engine/examples/mongodb_documents_example.py"

Nothing else in this file changes -- pymongo and mongomock expose the same `find()` API, which
is the entire point of the bring-your-own-database pattern: Horizon never knows or cares which
one served the documents.

This example calls `HorizonAnswerEngine` (the AGPL core) directly, hence the AGPL header --
see `../LICENSE_COMMERCIAL_PLACEHOLDER.md`.

Run: python3 "HorizonAI Engine/examples/mongodb_documents_example.py"
Requires: pip install pymongo mongomock
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from horizon_memory import DEFAULT_PROFILE, HorizonAnswerEngine, RouteDocument

SCOPE_ID = 1
SESSION_ID = "mongodb-example"
DATABASE_NAME = "support_kb"
COLLECTION_NAME = "articles"


def _get_collection():
    """Real deployment: set MONGODB_URI and this returns a real pymongo collection, unchanged
    below. No MONGODB_URI: falls back to mongomock (no server, in-process) and seeds a small
    fixture so the example is runnable standalone."""
    uri = os.environ.get("MONGODB_URI")
    if uri:
        from pymongo import MongoClient
        client = MongoClient(uri)
        return client[DATABASE_NAME][COLLECTION_NAME], False
    import mongomock
    client = mongomock.MongoClient()
    collection = client[DATABASE_NAME][COLLECTION_NAME]
    _seed_fixture(collection)
    return collection, True


def _seed_fixture(collection) -> None:
    """Stands in for documents someone already wrote to your real database -- delete this call
    entirely once MONGODB_URI points at a collection that already has real content."""
    collection.insert_many([
        {"body": "The Meridian project reduced compute cost by exactly 42 percent compared to "
                 "the previous baseline architecture across every workload."},
        {"body": "Meridian's cost reduction came from a redesigned caching layer that "
                 "eliminated redundant recomputation across adjacent pipeline stages."},
        {"body": "The Solstice project, unrelated to Meridian, focuses on latency instead of "
                 "cost."},
    ])


def _documents_from_mongo(collection) -> tuple[RouteDocument, ...]:
    documents = []
    for row in collection.find({}, {"_id": 1, "body": 1}).sort("_id", 1):
        documents.append(RouteDocument(
            fact_id=hash(str(row["_id"])) & 0x7FFFFFFF,
            text=row["body"],
            scope_id=SCOPE_ID,
            session_id=SESSION_ID,
            version=1,
            source=f"{COLLECTION_NAME}:{row['_id']}",
        ))
    return tuple(documents)


def main() -> None:
    collection, is_mock = _get_collection()
    documents = _documents_from_mongo(collection)

    engine = HorizonAnswerEngine(profile=DEFAULT_PROFILE, scope_id=SCOPE_ID, session_id=SESSION_ID)
    result = engine.answer("What percent did the Meridian project reduce cost by?", documents)

    print("state:", result.state)
    print("answer:", result.answer_text)
    backend = "mongomock (no server)" if is_mock else os.environ["MONGODB_URI"]
    print(f"(sourced from {result.documents_considered} documents via {backend})")


if __name__ == "__main__":
    main()
