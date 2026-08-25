from __future__ import annotations

from typing import Iterator, Mapping
from urllib.parse import quote, urlparse, urlunparse

from .base import (
    CAPABILITIES, ConnectorCapabilities, ConnectorConfig, ConnectorKind,
    ConnectorNamespace, DocumentMapping, SchemaSample, require_safe_identifier,
)
from .http_json import SafeJsonTransport, authorization_headers


def _base_and_database(config: ConnectorConfig) -> tuple[str, str]:
    parsed = urlparse(config.endpoint)
    scheme = parsed.scheme.split("+")[-1]
    database = str(config.options.get("database") or parsed.path.strip("/"))
    if not database:
        raise ValueError("SpacetimeDB database/module is required")
    base = urlunparse(parsed._replace(scheme=scheme, path="", query="", fragment=""))
    return base.rstrip("/"), database


class SpacetimeDBConnector:
    kind = ConnectorKind.SPACETIMEDB
    capabilities: ConnectorCapabilities = CAPABILITIES[kind]

    def __init__(self, config: ConnectorConfig, *, transport: SafeJsonTransport | None = None):
        if config.kind != self.kind:
            raise ValueError("connector kind differs from implementation")
        self.config = config
        self.transport = transport or SafeJsonTransport(timeout=10)
        self.last_checkpoint: dict[str, object] = {}
        self._headers = {
            "Content-Type": "text/plain",
            **authorization_headers(config.secret, "bearer"),
        }

    def _query(self, sql: str) -> tuple[dict[str, object], ...]:
        base, database = _base_and_database(self.config)
        payload = self.transport.request(
            "POST", f"{base}/database/{quote(database, safe='')}/sql",
            headers=self._headers, body=sql.encode("utf-8"))
        if isinstance(payload, list):
            if all(isinstance(row, dict) for row in payload):
                return tuple(dict(row) for row in payload)
            if not payload:
                return ()
        if isinstance(payload, dict) and isinstance(payload.get("rows"), list) \
                and isinstance(payload.get("schema"), list):
            columns = tuple(
                str(item.get("name")) if isinstance(item, dict) else str(item)
                for item in payload["schema"])
            return tuple(dict(zip(columns, row)) for row in payload["rows"])
        raise RuntimeError("SpacetimeDB SQL response is invalid")

    def test_connection(self) -> None:
        self._query("SELECT 1")

    def discover(self) -> tuple[ConnectorNamespace, ...]:
        configured = self.config.options.get("tables")
        if not isinstance(configured, (list, tuple)) or not configured:
            raise ValueError("SpacetimeDB requires module-declared table names")
        result = []
        for raw_name in configured:
            name = require_safe_identifier(str(raw_name))
            rows = self._query(f"SELECT * FROM {name} LIMIT 1")
            fields = tuple(rows[0]) if rows else ("id", "body")
            result.append(ConnectorNamespace(name, name, fields, ("id",)))
        return tuple(result)

    def sample(self, namespace: str, *, limit: int = 50) -> SchemaSample:
        if not 1 <= limit <= 50:
            raise ValueError("sample limit must be in [1,50]")
        known = {item.identity: item for item in self.discover()}
        descriptor = known.get(namespace)
        if descriptor is None:
            raise ValueError("unknown SpacetimeDB table")
        rows = self._query(f"SELECT * FROM {require_safe_identifier(namespace)} LIMIT {limit}")
        return SchemaSample(descriptor, rows)

    def stream(self, mapping: DocumentMapping, *, batch_size: int = 256,
               checkpoint: Mapping[str, object] | None = None) \
            -> Iterator[tuple[Mapping[str, object], ...]]:
        if not 1 <= batch_size <= 2048:
            raise ValueError("sync batch size must be in [1,2048]")
        if mapping.namespace not in {item.identity for item in self.discover()}:
            raise ValueError("unknown SpacetimeDB table")
        offset = 0 if checkpoint is None else int(checkpoint.get("offset", 0))
        while True:
            rows = self._query(
                f"SELECT * FROM {require_safe_identifier(mapping.namespace)} "
                f"ORDER BY {require_safe_identifier(mapping.id_field)} "
                f"LIMIT {batch_size} OFFSET {offset}")
            if not rows:
                break
            offset += len(rows)
            self.last_checkpoint = {"offset": offset}
            yield rows
            if len(rows) < batch_size:
                break

    def create_managed_namespace(self, name: str = "proofray_memory") -> str:
        raise RuntimeError("spacetimedb_managed_namespace_unsupported")

    def close(self) -> None:
        return None


__all__ = ["SpacetimeDBConnector"]
