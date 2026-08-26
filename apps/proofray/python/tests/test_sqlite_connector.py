import sqlite3

import pytest

from proofray_app.connectors import (
    ConnectorConfig, ConnectorKind, DocumentMapping, create_connector,
)
from proofray_app.connectors.base import map_record


def _database(path):
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE messages (id INTEGER PRIMARY KEY, body TEXT NOT NULL, thread TEXT, turn INT)")
    connection.executemany(
        "INSERT INTO messages (body, thread, turn) VALUES (?, ?, ?)",
        [("Alpha", "s1", 0), ("Beta", "s1", 1), ("Gamma", "s2", 0)],
    )
    connection.commit()
    connection.close()


def test_sqlite_discovery_preview_and_stream_are_read_only_by_default(tmp_path):
    path = tmp_path / "memory.sqlite"
    _database(path)
    config = ConnectorConfig("sqlite-1", ConnectorKind.SQLITE, str(path))
    connector = create_connector(config)
    try:
        connector.test_connection()
        namespaces = connector.discover()
        assert [item.identity for item in namespaces] == ["messages"]
        assert namespaces[0].primary_keys == ("id",)
        sample = connector.sample("messages", limit=2)
        assert [row["body"] for row in sample.rows] == ["Alpha", "Beta"]
        mapping = DocumentMapping(
            "messages", "id", "body", session_field="thread", sequence_field="turn")
        batches = tuple(connector.stream(mapping, batch_size=2))
        assert [len(batch) for batch in batches] == [2, 1]
        documents = [map_record(config, mapping, row) for batch in batches for row in batch]
        assert [item.text for item in documents] == ["Alpha", "Beta", "Gamma"]
        assert connector.last_checkpoint == {"offset": 3}
        with pytest.raises(RuntimeError, match="explicit"):
            connector.create_managed_namespace()
    finally:
        connector.close()


def test_sqlite_managed_namespace_requires_separate_explicit_write_config(tmp_path):
    path = tmp_path / "managed.sqlite"
    _database(path)
    connector = create_connector(ConnectorConfig(
        "managed", ConnectorKind.SQLITE, str(path), {"managed_write": True}))
    try:
        assert connector.create_managed_namespace() == "proofray_memory"
        with pytest.raises(ValueError, match="fixed"):
            connector.create_managed_namespace("another_table")
        assert "proofray_memory" in {item.identity for item in connector.discover()}
    finally:
        connector.close()


def test_sqlite_read_only_probe_never_creates_a_missing_source(tmp_path):
    missing = tmp_path / "missing.sqlite"
    connector = create_connector(ConnectorConfig(
        "sqlite", ConnectorKind.SQLITE, str(missing)))
    try:
        with pytest.raises(sqlite3.OperationalError):
            connector.test_connection()
        assert not missing.exists()
    finally:
        connector.close()


def test_connector_config_rejects_credentials_inside_endpoint():
    with pytest.raises(ValueError, match="ephemeral secret lease"):
        ConnectorConfig(
            "pg", ConnectorKind.POSTGRESQL,
            "postgresql://user:password@localhost/database")
    with pytest.raises(ValueError, match="query credentials"):
        ConnectorConfig(
            "elastic", ConnectorKind.ELASTICSEARCH,
            "elasticsearch+https://example.test?api_key=secret")


def test_connector_config_rejects_nested_credentials_in_options():
    with pytest.raises(ValueError, match="cannot contain credentials"):
        ConnectorConfig(
            "mongo", ConnectorKind.MONGODB, "mongodb://localhost/database",
            {"nested": {"access_key": "must-not-be-retained"}},
        )
    with pytest.raises(ValueError, match="credential URIs"):
        ConnectorConfig(
            "space", ConnectorKind.SPACETIMEDB,
            "spacetimedb+https://example.test/module",
            {"callback": "https://user:password@example.test/path"},
        )


def test_relational_mapping_rejects_unknown_namespace(tmp_path):
    path = tmp_path / "memory.sqlite"
    _database(path)
    connector = create_connector(ConnectorConfig("sqlite", ConnectorKind.SQLITE, str(path)))
    try:
        with pytest.raises(ValueError, match="unknown relational namespace"):
            tuple(connector.stream(DocumentMapping("missing", "id", "body")))
    finally:
        connector.close()


def test_sqlite_quotes_existing_nontrivial_identifiers_without_widening_writes(tmp_path):
    path = tmp_path / "quoted.sqlite"
    connection = sqlite3.connect(path)
    connection.execute(
        'CREATE TABLE "source notes" ("record id" INTEGER PRIMARY KEY, "body text" TEXT)')
    connection.execute('INSERT INTO "source notes" VALUES (1, \'exact\')')
    connection.commit()
    connection.close()
    connector = create_connector(ConnectorConfig(
        "quoted", ConnectorKind.SQLITE, str(path)))
    try:
        rows = tuple(connector.stream(DocumentMapping(
            "source notes", "record id", "body text")))
        assert rows == (({"record id": 1, "body text": "exact"},),)
    finally:
        connector.close()
