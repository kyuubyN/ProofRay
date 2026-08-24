# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
""""Connect a database" -- same bring-your-own-database pattern as the other examples in this
folder, this time against Elasticsearch (OpenSearch speaks the same client protocol, so this
also works unchanged against an OpenSearch cluster). Swap the query for your own index/mapping
and the rest is unchanged.

Like Postgres/MySQL, there is no pure-Python, no-server stand-in for Elasticsearch, so this
requires a real cluster and, deliberately, will not start one for you. Point it at yours with
`ELASTICSEARCH_URL`:

    ELASTICSEARCH_URL="http://localhost:9200" \
        python3 "ProofRay Engine/examples/elasticsearch_documents_example.py"

Without `ELASTICSEARCH_URL` set, this prints setup instructions and exits cleanly instead of
failing. This is also the most direct "already have a search system, want deterministic answers
instead" example in this folder: everything indexed for full-text search already has a `body`
this script can pull out and route the same way as any other document source.

This example calls `ProofRayAnswerEngine` (the AGPL core) directly, hence the AGPL header --
see `../LICENSE_COMMERCIAL_PLACEHOLDER.md`.

Run: python3 "ProofRay Engine/examples/elasticsearch_documents_example.py"
Requires: pip install elasticsearch
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from proofray import DEFAULT_PROFILE, ProofRayAnswerEngine, RouteDocument

SCOPE_ID = 1
SESSION_ID = "elasticsearch-example"
INDEX_NAME = "articles"

_FIXTURE_ROWS = (
    "The Meridian project reduced compute cost by exactly 42 percent compared to "
    "the previous baseline architecture across every workload.",
    "Meridian's cost reduction came from a redesigned caching layer that "
    "eliminated redundant recomputation across adjacent pipeline stages.",
    "The Solstice project, unrelated to Meridian, focuses on latency instead of cost.",
)


def _seed_if_empty(client) -> None:
    """Stands in for documents someone already indexed in your real cluster -- delete this call
    entirely once ELASTICSEARCH_URL points at an index that already has real content."""
    if client.indices.exists(index=INDEX_NAME):
        if client.count(index=INDEX_NAME)["count"] > 0:
            return
    else:
        client.indices.create(index=INDEX_NAME)
    for i, body in enumerate(_FIXTURE_ROWS, start=1):
        client.index(index=INDEX_NAME, id=i, document={"body": body}, refresh=True)


def _documents_from_elasticsearch(client) -> tuple[RouteDocument, ...]:
    response = client.search(index=INDEX_NAME, query={"match_all": {}}, size=1000)
    documents = []
    for hit in response["hits"]["hits"]:
        doc_id = hit["_id"]
        documents.append(RouteDocument(
            fact_id=int(doc_id) if str(doc_id).isdigit() else (hash(doc_id) & 0x7FFFFFFF),
            text=hit["_source"]["body"],
            scope_id=SCOPE_ID,
            session_id=SESSION_ID,
            version=1,
            source=f"{INDEX_NAME}:{doc_id}",
        ))
    return tuple(documents)


def main() -> None:
    url = os.environ.get("ELASTICSEARCH_URL")
    if not url:
        print("ELASTICSEARCH_URL is not set -- nothing to connect to, so this example won't run.")
        print("Like Postgres/MySQL, there is no in-process Elasticsearch stand-in on purpose "
              "(a real cluster is heavier than this demo needs).")
        print('Set it and re-run, e.g.:\n'
              '  ELASTICSEARCH_URL="http://localhost:9200" '
              'python3 "ProofRay Engine/examples/elasticsearch_documents_example.py"')
        return

    from elasticsearch import Elasticsearch

    client = Elasticsearch(url)
    _seed_if_empty(client)
    documents = _documents_from_elasticsearch(client)

    engine = ProofRayAnswerEngine(profile=DEFAULT_PROFILE, scope_id=SCOPE_ID, session_id=SESSION_ID)
    result = engine.answer("What percent did the Meridian project reduce cost by?", documents)

    print("state:", result.state)
    print("answer:", result.answer_text)
    print(f"(sourced from {result.documents_considered} documents via {url})")


if __name__ == "__main__":
    main()
