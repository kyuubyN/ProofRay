from __future__ import annotations

from pathlib import Path
import ssl
from typing import Iterator, Mapping
from urllib.parse import unquote, urlparse

from .base import (
    CAPABILITIES, ConnectorCapabilities, ConnectorConfig, ConnectorKind,
    ConnectorNamespace, DocumentMapping, SchemaSample,
    is_loopback_host,
)


def _sql_identifier(value: str) -> str:
    if (not isinstance(value, str) or not value or len(value) > 128
            or any(ord(character) < 32 for character in value)):
        raise ValueError("unsafe database identifier")
    return value


class RelationalConnector:
    """Read existing relational sources and create only a dedicated namespace."""

    kind: ConnectorKind

    def __init__(self, config: ConnectorConfig):
        if config.kind != self.kind:
            raise ValueError("connector kind differs from implementation")
        self.config = config
        self.capabilities: ConnectorCapabilities = CAPABILITIES[self.kind]
        self._connection = None
        self.last_checkpoint: dict[str, object] = {}

    @property
    def connection(self):
        if self._connection is None:
            self._connection = self._connect()
        return self._connection

    def _connect(self):
        raise NotImplementedError

    def _quote(self, identifier: str) -> str:
        value = _sql_identifier(identifier)
        return f'"{value.replace(chr(34), chr(34) * 2)}"'

    def _namespace_parts(self, identity: str) -> tuple[str | None, str]:
        parts = identity.split(".")
        if len(parts) == 1:
            return None, _sql_identifier(parts[0])
        if len(parts) == 2:
            return _sql_identifier(parts[0]), _sql_identifier(parts[1])
        raise ValueError("relational namespace must be table or schema.table")

    def _qualified(self, identity: str) -> str:
        schema, table = self._namespace_parts(identity)
        return self._quote(table) if schema is None else \
            f"{self._quote(schema)}.{self._quote(table)}"

    def _execute(self, statement: str, parameters=()):
        cursor = self.connection.cursor()
        cursor.execute(statement, parameters)
        return cursor

    def test_connection(self) -> None:
        cursor = self._execute("SELECT 1")
        try:
            row = cursor.fetchone()
            if not row or int(row[0]) != 1:
                raise RuntimeError("relational connection probe failed")
        finally:
            cursor.close()

    def _discovery_rows(self):
        raise NotImplementedError

    def discover(self) -> tuple[ConnectorNamespace, ...]:
        grouped: dict[str, list[tuple[str, bool]]] = {}
        for schema, table, column, primary in self._discovery_rows():
            identity = str(table) if not schema else f"{schema}.{table}"
            grouped.setdefault(identity, []).append((str(column), bool(primary)))
        return tuple(
            ConnectorNamespace(
                identity, identity,
                tuple(column for column, _primary in columns),
                tuple(column for column, primary in columns if primary),
            )
            for identity, columns in sorted(grouped.items())
        )

    def sample(self, namespace: str, *, limit: int = 50) -> SchemaSample:
        if not 1 <= limit <= 50:
            raise ValueError("sample limit must be in [1,50]")
        known = {item.identity: item for item in self.discover()}
        descriptor = known.get(namespace)
        if descriptor is None:
            raise ValueError("unknown relational namespace")
        cursor = self._execute(f"SELECT * FROM {self._qualified(namespace)} LIMIT {int(limit)}")
        try:
            names = tuple(item[0] for item in cursor.description or ())
            rows = tuple(dict(zip(names, row)) for row in cursor.fetchall())
        finally:
            cursor.close()
        return SchemaSample(descriptor, rows)

    def stream(self, mapping: DocumentMapping, *, batch_size: int = 256,
               checkpoint: Mapping[str, object] | None = None) \
            -> Iterator[tuple[Mapping[str, object], ...]]:
        if not 1 <= batch_size <= 2048:
            raise ValueError("sync batch size must be in [1,2048]")
        if mapping.namespace not in {item.identity for item in self.discover()}:
            raise ValueError("unknown relational namespace")
        offset = 0 if checkpoint is None else int(checkpoint.get("offset", 0))
        if offset < 0:
            raise ValueError("invalid sync checkpoint")
        _, table = self._namespace_parts(mapping.namespace)
        order = self._quote(mapping.id_field)
        qualified = self._qualified(mapping.namespace)
        while True:
            cursor = self._execute(
                f"SELECT * FROM {qualified} ORDER BY {order} LIMIT {int(batch_size)} "
                f"OFFSET {int(offset)}")
            try:
                names = tuple(item[0] for item in cursor.description or ())
                rows = tuple(dict(zip(names, row)) for row in cursor.fetchall())
            finally:
                cursor.close()
            if not rows:
                break
            offset += len(rows)
            self.last_checkpoint = {"offset": offset}
            yield rows
            if len(rows) < batch_size:
                break

    def create_managed_namespace(self, name: str = "proofray_memory") -> str:
        if not bool(self.config.options.get("managed_write", False)):
            raise RuntimeError("managed namespace requires explicit write authorization")
        if name != "proofray_memory":
            raise ValueError("relational managed namespace is fixed")
        table = name
        quoted = self._quote(table)
        self._execute(
            f"CREATE TABLE IF NOT EXISTS {quoted} ("
            "id VARCHAR(160) PRIMARY KEY, text TEXT NOT NULL, source VARCHAR(512) NOT NULL, "
            "session_id VARCHAR(160), sequence BIGINT, event_time BIGINT, role VARCHAR(16), "
            "speaker VARCHAR(256), version BIGINT NOT NULL, sha256 CHAR(64) NOT NULL, "
            "created_at VARCHAR(40) NOT NULL)"
        ).close()
        self.connection.commit()
        return table

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None


class SQLiteConnector(RelationalConnector):
    kind = ConnectorKind.SQLITE

    def _connect(self):
        import sqlite3
        endpoint = self.config.endpoint
        parsed = urlparse(endpoint)
        path = unquote(parsed.path) if parsed.scheme in ("sqlite", "sqlite3") else endpoint
        if not path:
            raise ValueError("SQLite path is required")
        resolved = Path(path).resolve()
        if bool(self.config.options.get("managed_write", False)):
            return sqlite3.connect(str(resolved))
        return sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)

    def create_managed_namespace(self, name: str = "proofray_memory") -> str:
        if not bool(self.config.options.get("managed_write", False)):
            raise RuntimeError("sqlite_managed_write_requires_explicit_configuration")
        return super().create_managed_namespace(name)

    def _discovery_rows(self):
        cursor = self._execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name")
        try:
            tables = tuple(row[0] for row in cursor.fetchall())
        finally:
            cursor.close()
        rows = []
        for table in tables:
            safe = _sql_identifier(str(table))
            cursor = self._execute(f"PRAGMA table_info({self._quote(safe)})")
            try:
                rows.extend((None, safe, item[1], bool(item[5])) for item in cursor.fetchall())
            finally:
                cursor.close()
        return tuple(rows)


class DuckDBConnector(RelationalConnector):
    kind = ConnectorKind.DUCKDB

    def _connect(self):
        import duckdb
        endpoint = self.config.endpoint
        parsed = urlparse(endpoint)
        path = unquote(parsed.path) if parsed.scheme == "duckdb" else endpoint
        return duckdb.connect(path, read_only=bool(self.config.options.get("read_only", True)))

    def _discovery_rows(self):
        cursor = self._execute(
            "SELECT table_schema, table_name, column_name, false "
            "FROM information_schema.columns "
            "WHERE table_schema NOT IN ('information_schema','pg_catalog') "
            "ORDER BY table_schema, table_name, ordinal_position")
        try:
            return tuple(cursor.fetchall())
        finally:
            cursor.close()


class PostgreSQLConnector(RelationalConnector):
    kind = ConnectorKind.POSTGRESQL

    def _connect(self):
        import pg8000.dbapi
        parsed = urlparse(self.config.endpoint)
        ssl_context = None
        if not is_loopback_host(parsed.hostname):
            ca_file = self.config.options.get("tls_ca_file")
            ssl_context = ssl.create_default_context(
                cafile=ca_file if isinstance(ca_file, str) and ca_file else None)
        connection = pg8000.dbapi.connect(
            user=unquote(parsed.username or ""), password=self.config.secret,
            host=parsed.hostname or "localhost", port=parsed.port or 5432,
            database=parsed.path.lstrip("/"), timeout=10,
            ssl_context=ssl_context,
        )
        if not bool(self.config.options.get("managed_write", False)):
            cursor = connection.cursor()
            cursor.execute("SET default_transaction_read_only = on")
            connection.commit()
            cursor.close()
        return connection

    def _discovery_rows(self):
        cursor = self._execute(
            "SELECT c.table_schema, c.table_name, c.column_name, "
            "CASE WHEN k.column_name IS NULL THEN false ELSE true END "
            "FROM information_schema.columns c LEFT JOIN ("
            "SELECT ku.table_schema, ku.table_name, ku.column_name "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage ku ON "
            "tc.constraint_name=ku.constraint_name AND tc.table_schema=ku.table_schema "
            "WHERE tc.constraint_type='PRIMARY KEY') k ON "
            "c.table_schema=k.table_schema AND c.table_name=k.table_name "
            "AND c.column_name=k.column_name "
            "WHERE c.table_schema NOT IN ('pg_catalog','information_schema') "
            "ORDER BY c.table_schema,c.table_name,c.ordinal_position")
        try:
            return tuple(cursor.fetchall())
        finally:
            cursor.close()


class MySQLConnector(RelationalConnector):
    kind = ConnectorKind.MYSQL

    def _connect(self):
        import pymysql
        parsed = urlparse(self.config.endpoint)
        options = {}
        if not is_loopback_host(parsed.hostname):
            ca_file = self.config.options.get("tls_ca_file")
            options["ssl"] = ssl.create_default_context(
                cafile=ca_file if isinstance(ca_file, str) and ca_file else None)
        connection = pymysql.connect(
            user=unquote(parsed.username or ""), password=self.config.secret or "",
            host=parsed.hostname or "localhost", port=parsed.port or 3306,
            database=parsed.path.lstrip("/"), connect_timeout=10,
            read_timeout=30, write_timeout=30, **options,
        )
        if not bool(self.config.options.get("managed_write", False)):
            cursor = connection.cursor()
            cursor.execute("SET SESSION TRANSACTION READ ONLY")
            connection.commit()
            cursor.close()
        return connection

    def _quote(self, identifier: str) -> str:
        value = _sql_identifier(identifier)
        return f"`{value.replace('`', '``')}`"

    def _discovery_rows(self):
        cursor = self._execute(
            "SELECT table_schema, table_name, column_name, column_key='PRI' "
            "FROM information_schema.columns WHERE table_schema=DATABASE() "
            "ORDER BY table_name, ordinal_position")
        try:
            return tuple(cursor.fetchall())
        finally:
            cursor.close()


__all__ = [
    "RelationalConnector", "SQLiteConnector", "DuckDBConnector",
    "PostgreSQLConnector", "MySQLConnector",
]
