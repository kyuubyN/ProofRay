# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
""""Connect a database" -- same bring-your-own-database pattern as the other examples in this
folder, this time against Amazon DynamoDB.

By default this runs against `moto` (an in-process mock of AWS services -- `pip install
moto[dynamodb]`), so it works with no real AWS account or server at all and is safe to run in
CI. Point it at a real table by setting `DYNAMODB_USE_REAL_AWS=1` plus `DYNAMODB_TABLE` and
standard AWS credentials/region env vars:

    DYNAMODB_USE_REAL_AWS=1 DYNAMODB_TABLE=your-table AWS_DEFAULT_REGION=us-east-1 \
        python3 "ProofRay Engine/examples/dynamodb_documents_example.py"

Nothing else in this file changes -- boto3 talks the same way to a real table or to moto's
in-process mock, which is the entire point of the bring-your-own-database pattern: ProofRay
never knows or cares which one served the documents.

This example calls `ProofRayAnswerEngine` (the AGPL core) directly, hence the AGPL header --
see `../LICENSE_COMMERCIAL_PLACEHOLDER.md`.

Run: python3 "ProofRay Engine/examples/dynamodb_documents_example.py"
Requires: pip install boto3 moto
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from proofray import DEFAULT_PROFILE, ProofRayAnswerEngine, RouteDocument

SCOPE_ID = 1
SESSION_ID = "dynamodb-example"
TABLE_NAME = os.environ.get("DYNAMODB_TABLE", "articles")

_FIXTURE_ROWS = (
    "The Meridian project reduced compute cost by exactly 42 percent compared to "
    "the previous baseline architecture across every workload.",
    "Meridian's cost reduction came from a redesigned caching layer that "
    "eliminated redundant recomputation across adjacent pipeline stages.",
    "The Solstice project, unrelated to Meridian, focuses on latency instead of cost.",
)


def _seed_fixture(table) -> None:
    """Stands in for items someone already wrote to your real table -- delete this call
    entirely once DYNAMODB_USE_REAL_AWS points at a table that already has real content."""
    with table.batch_writer() as batch:
        for i, body in enumerate(_FIXTURE_ROWS, start=1):
            batch.put_item(Item={"id": str(i), "body": body})


def _documents_from_dynamodb(table) -> tuple[RouteDocument, ...]:
    response = table.scan()
    documents = []
    for item in sorted(response["Items"], key=lambda x: x["id"]):
        row_id = item["id"]
        documents.append(RouteDocument(
            fact_id=int(row_id) if row_id.isdigit() else (hash(row_id) & 0x7FFFFFFF),
            text=item["body"],
            scope_id=SCOPE_ID,
            session_id=SESSION_ID,
            version=1,
            source=f"{TABLE_NAME}:{row_id}",
        ))
    return tuple(documents)


def _get_table():
    import boto3

    if os.environ.get("DYNAMODB_USE_REAL_AWS") == "1":
        dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_DEFAULT_REGION"))
        return dynamodb.Table(TABLE_NAME), False

    from moto import mock_aws

    mock_aws().start()
    dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
    dynamodb.create_table(
        TableName=TABLE_NAME,
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    table = dynamodb.Table(TABLE_NAME)
    table.wait_until_exists()
    _seed_fixture(table)
    return table, True


def main() -> None:
    table, is_mock = _get_table()
    documents = _documents_from_dynamodb(table)

    engine = ProofRayAnswerEngine(profile=DEFAULT_PROFILE, scope_id=SCOPE_ID, session_id=SESSION_ID)
    result = engine.answer("What percent did the Meridian project reduce cost by?", documents)

    print("state:", result.state)
    print("answer:", result.answer_text)
    backend = "moto (no real AWS)" if is_mock else f"DynamoDB table {TABLE_NAME!r}"
    print(f"(sourced from {result.documents_considered} documents via {backend})")


if __name__ == "__main__":
    main()
