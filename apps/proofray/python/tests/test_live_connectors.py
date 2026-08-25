from __future__ import annotations

import json
import os
import urllib.request

import pytest

from proofray_app.connectors import ConnectorConfig, ConnectorKind, DocumentMapping
from proofray_app.connectors.elasticsearch import ElasticsearchConnector
from proofray_app.connectors.relational import MySQLConnector, PostgreSQLConnector


pytestmark = pytest.mark.integration


def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} is not provisioned")
    return value


def test_postgresql_read_source_and_dedicated_managed_table():
    endpoint = _required("PROOFRAY_TEST_POSTGRESQL")
    secret = _required("PROOFRAY_TEST_POSTGRESQL_PASSWORD")
    import pg8000.dbapi
    setup = pg8000.dbapi.connect(
        user="proofray", password=secret, host="127.0.0.1", port=5432,
        database="proofray")
    cursor = setup.cursor()
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS source_notes "
        "(id VARCHAR(32) PRIMARY KEY, text TEXT NOT NULL)")
    cursor.execute("DELETE FROM source_notes")
    cursor.execute("INSERT INTO source_notes VALUES (%s, %s)", ("a", "one"))
    setup.commit()
    cursor.close()
    setup.close()

    connector = PostgreSQLConnector(ConnectorConfig(
        "postgres-live", ConnectorKind.POSTGRESQL, endpoint, secret=secret))
    try:
        connector.test_connection()
        assert "public.source_notes" in {item.identity for item in connector.discover()}
        rows = tuple(connector.stream(DocumentMapping(
            "public.source_notes", "id", "text")))
        assert rows == (({"id": "a", "text": "one"},),)
        with pytest.raises(RuntimeError, match="explicit"):
            connector.create_managed_namespace()
    finally:
        connector.close()
    managed = PostgreSQLConnector(ConnectorConfig(
        "postgres-live", ConnectorKind.POSTGRESQL, endpoint,
        {"managed_write": True}, secret=secret))
    try:
        assert managed.create_managed_namespace() == "proofray_memory"
    finally:
        managed.close()


def test_mysql_read_source_and_dedicated_managed_table():
    endpoint = _required("PROOFRAY_TEST_MYSQL")
    secret = _required("PROOFRAY_TEST_MYSQL_PASSWORD")
    import pymysql
    setup = pymysql.connect(
        user="proofray", password=secret, host="127.0.0.1", port=3306,
        database="proofray")
    cursor = setup.cursor()
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS source_notes "
        "(id VARCHAR(32) PRIMARY KEY, text TEXT NOT NULL)")
    cursor.execute("DELETE FROM source_notes")
    cursor.execute("INSERT INTO source_notes VALUES (%s, %s)", ("a", "one"))
    setup.commit()
    cursor.close()
    setup.close()

    connector = MySQLConnector(ConnectorConfig(
        "mysql-live", ConnectorKind.MYSQL, endpoint, secret=secret))
    try:
        connector.test_connection()
        assert "proofray.source_notes" in {item.identity for item in connector.discover()}
        rows = tuple(connector.stream(DocumentMapping(
            "proofray.source_notes", "id", "text")))
        assert rows == (({"id": "a", "text": "one"},),)
        with pytest.raises(RuntimeError, match="explicit"):
            connector.create_managed_namespace()
    finally:
        connector.close()
    managed = MySQLConnector(ConnectorConfig(
        "mysql-live", ConnectorKind.MYSQL, endpoint,
        {"managed_write": True}, secret=secret))
    try:
        assert managed.create_managed_namespace() == "proofray_memory"
    finally:
        managed.close()


def _elastic_request(method: str, url: str, body: object | None = None) -> object:
    encoded = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = response.read()
    return None if not payload else json.loads(payload)


def test_elasticsearch_read_source_and_dedicated_managed_index():
    endpoint = _required("PROOFRAY_TEST_ELASTICSEARCH").rstrip("/")
    try:
        _elastic_request("DELETE", f"{endpoint}/source-notes")
    except Exception:
        pass
    _elastic_request("PUT", f"{endpoint}/source-notes", {
        "mappings": {"properties": {
            "id": {"type": "keyword"}, "text": {"type": "text"},
        }},
    })
    _elastic_request("PUT", f"{endpoint}/source-notes/_doc/a?refresh=true", {
        "id": "a", "text": "one",
    })
    connector = ElasticsearchConnector(ConnectorConfig(
        "elastic-live", ConnectorKind.ELASTICSEARCH,
        endpoint.replace("http://", "elasticsearch+http://", 1),
        {"managed_write": True}))
    connector.test_connection()
    assert "source-notes" in {item.identity for item in connector.discover()}
    rows = tuple(connector.stream(DocumentMapping(
        "source-notes", "id", "text")))
    assert rows == (({"_id": "a", "id": "a", "text": "one"},),)
    assert connector.create_managed_namespace() == "proofray-memory"
