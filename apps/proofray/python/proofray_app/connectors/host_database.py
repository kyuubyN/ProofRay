from __future__ import annotations

from typing import Callable, Iterator, Mapping

from .base import (
    CAPABILITIES, ConnectorCapabilities, ConnectorConfig, ConnectorKind,
    ConnectorNamespace, DocumentMapping, SchemaSample,
)


HostCall = Callable[[str, dict[str, object]], dict[str, object]]


class HostDatabaseConnector:
    """SQLite/DuckDB adapter whose physical connection is owned by Flutter."""

    def __init__(self, config: ConnectorConfig, call_host: HostCall):
        if config.kind not in (ConnectorKind.SQLITE, ConnectorKind.DUCKDB):
            raise ValueError("host database connector only supports SQLite and DuckDB")
        self.config = config
        self.kind = config.kind
        self.capabilities: ConnectorCapabilities = CAPABILITIES[self.kind]
        self._call_host = call_host
        self.last_checkpoint: dict[str, object] = {}

    def _call(self, operation: str, payload: dict[str, object] | None = None) \
            -> dict[str, object]:
        return self._call_host(f"connector.{self.kind.value}.{operation}", {
            "endpoint": self.config.endpoint,
            "options": dict(self.config.options),
            "secret": self.config.secret,
            **(payload or {}),
        })

    def test_connection(self) -> None:
        if self._call("test").get("reachable") is not True:
            raise RuntimeError("host database probe failed")

    def discover(self) -> tuple[ConnectorNamespace, ...]:
        rows = self._call("discover").get("namespaces")
        if not isinstance(rows, list):
            raise RuntimeError("host database discovery is invalid")
        return tuple(ConnectorNamespace(
            str(item["identity"]), str(item["display_name"]),
            tuple(item["fields"]), tuple(item.get("primary_keys", ())),
            item.get("estimated_rows"),
        ) for item in rows if isinstance(item, dict))

    def sample(self, namespace: str, *, limit: int = 50) -> SchemaSample:
        if not 1 <= limit <= 50:
            raise ValueError("sample limit must be in [1,50]")
        descriptor = next(
            (item for item in self.discover() if item.identity == namespace), None)
        if descriptor is None:
            raise ValueError("unknown host database namespace")
        rows = self._call("sample", {"namespace": namespace, "limit": limit}).get("rows")
        if not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows):
            raise RuntimeError("host database sample is invalid")
        return SchemaSample(descriptor, tuple(rows))

    def stream(self, mapping: DocumentMapping, *, batch_size: int = 256,
               checkpoint: Mapping[str, object] | None = None) \
            -> Iterator[tuple[Mapping[str, object], ...]]:
        if not 1 <= batch_size <= 2048:
            raise ValueError("sync batch size must be in [1,2048]")
        if checkpoint and checkpoint.get("complete") is True:
            return
        cursor = dict(checkpoint or {"offset": 0})
        while True:
            page = self._call("page", {
                "namespace": mapping.namespace,
                "id_field": mapping.id_field,
                "checkpoint": cursor,
                "limit": batch_size,
            })
            rows = page.get("rows")
            next_checkpoint = page.get("checkpoint")
            complete = page.get("complete")
            if (not isinstance(rows, list) or any(not isinstance(item, dict) for item in rows)
                    or not isinstance(next_checkpoint, dict) or not isinstance(complete, bool)):
                raise RuntimeError("host database page is invalid")
            cursor = dict(next_checkpoint)
            cursor["complete"] = complete
            self.last_checkpoint = cursor
            if rows:
                yield tuple(rows)
            if complete:
                break
            if not rows:
                raise RuntimeError("host database pagination made no progress")

    def create_managed_namespace(self, name: str = "proofray_memory") -> str:
        if not bool(self.config.options.get("managed_write", False)):
            raise RuntimeError("managed namespace requires explicit write authorization")
        if name != "proofray_memory":
            raise ValueError("host managed namespace is fixed")
        value = self._call("managed_create", {"name": name}).get("namespace")
        if not isinstance(value, str):
            raise RuntimeError("host managed namespace creation failed")
        return value

    def close(self) -> None:
        return None


__all__ = ["HostDatabaseConnector"]
