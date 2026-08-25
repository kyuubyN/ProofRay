from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from threading import RLock
from typing import Callable, Mapping

from .connectors import (
    ConnectorConfig, ConnectorKind, DocumentMapping,
    MappedDocument, create_connector, detect_connector_kind, map_record,
)
from .connectors.base import CAPABILITIES, Connector


ConnectorFactory = Callable[[ConnectorConfig], Connector]


class ConnectorManager:
    """Secretless connector registry with bounded discovery and sync leases."""

    def __init__(self, *, factory: ConnectorFactory = create_connector):
        self._factory = factory
        self._configs: dict[str, ConnectorConfig] = {}
        self._previewed_mappings: dict[str, set[DocumentMapping]] = {}
        self._lock = RLock()

    @staticmethod
    def detect(endpoint: str) -> dict[str, object]:
        kind = detect_connector_kind(endpoint)
        if kind is None:
            return {"kind": None, "requires_confirmation": True}
        return {
            "kind": kind.value,
            "requires_confirmation": False,
            "capabilities": asdict(CAPABILITIES[kind]),
        }

    def configure(self, config: ConnectorConfig) -> None:
        if config.options.get("managed_write") is True:
            raise ValueError(
                "managed write authorization is one-shot and cannot be retained")
        with self._lock:
            self._configs[config.connector_id] = replace(config, secret=None)
            self._previewed_mappings.pop(config.connector_id, None)

    def remove(self, connector_id: str) -> None:
        with self._lock:
            self._configs.pop(connector_id, None)
            self._previewed_mappings.pop(connector_id, None)

    def _connector(self, connector_id: str, secret: str | None) -> Connector:
        with self._lock:
            config = self._configs.get(connector_id)
        if config is None:
            raise ValueError("unknown connector")
        if config.kind == ConnectorKind.DUCKDB and secret:
            raise ValueError("DuckDB connector does not accept a credential lease")
        return self._factory(replace(config, secret=secret))

    def test_connection(self, connector_id: str, *, secret: str | None = None) -> None:
        connector = self._connector(connector_id, secret)
        try:
            connector.test_connection()
        finally:
            connector.close()

    def discover(self, connector_id: str, *, secret: str | None = None) \
            -> tuple[dict[str, object], ...]:
        connector = self._connector(connector_id, secret)
        try:
            return tuple(asdict(item) for item in connector.discover())
        finally:
            connector.close()

    def sample(self, connector_id: str, namespace: str, *,
               secret: str | None = None, limit: int = 50) -> dict[str, object]:
        if limit < 1 or limit > 50:
            raise ValueError("connector sample limit is outside 1..50")
        connector = self._connector(connector_id, secret)
        try:
            sample = connector.sample(namespace, limit=limit)
            return {
                "namespace": asdict(sample.namespace),
                **_bounded_rows(sample.rows),
            }
        finally:
            connector.close()

    @staticmethod
    def suggest_mapping(namespace: Mapping[str, object]) -> dict[str, object]:
        fields_value = namespace.get("fields", ())
        fields = tuple(str(item) for item in fields_value) \
            if isinstance(fields_value, (tuple, list)) else ()
        folded = {field.casefold(): field for field in fields}

        def first(*names: str) -> str | None:
            return next((folded[name] for name in names if name in folded), None)

        identifier = first("id", "_id", "uuid", "key")
        text = first("text", "content", "message", "body", "value")
        if identifier is None or text is None:
            return {"state": "needs_user_mapping"}
        return {
            "state": "suggested",
            "id_field": identifier,
            "text_field": text,
            "source_field": first("source", "source_id"),
            "session_field": first("session", "session_id", "thread_id"),
            "sequence_field": first("sequence", "seq", "turn_index"),
            "event_time_field": first("event_time", "timestamp", "created_at", "date"),
            "role_field": first("role"),
            "speaker_field": first("speaker", "author", "user_name"),
            "version_field": first("version", "revision"),
        }

    def sync(self, connector_id: str, mapping: DocumentMapping, *,
             ingest_batch: Callable[[tuple[MappedDocument, ...], str], None],
             secret: str | None = None,
             checkpoint: Mapping[str, object] | None = None) -> dict[str, object]:
        with self._lock:
            if mapping not in self._previewed_mappings.get(connector_id, set()):
                raise ValueError("connector sync requires the exact authorized preview mapping")
        connector = self._connector(connector_id, secret)
        count = 0
        committed_checkpoint: dict[str, object] = dict(checkpoint or {})
        try:
            for rows in connector.stream(
                    mapping, batch_size=256, checkpoint=checkpoint):
                mapped = tuple(map_record(connector.config, mapping, row) for row in rows)
                identities = {item.fact_id: item.source_primary_key for item in mapped}
                if len(identities) != len(mapped):
                    raise ValueError("connector FactId collision inside sync batch")
                if mapped:
                    ingest_batch(mapped, _batch_identity(connector_id, mapped))
                    count += len(mapped)
                    native = getattr(connector, "last_checkpoint", None)
                    if isinstance(native, Mapping):
                        committed_checkpoint = dict(native)
            native = getattr(connector, "last_checkpoint", None)
            if isinstance(native, Mapping):
                committed_checkpoint = dict(native)
        finally:
            connector.close()
        return {
            "documents_committed": count,
            "checkpoint": _json_value(committed_checkpoint),
        }

    def preview(self, connector_id: str, mapping: DocumentMapping, *,
                secret: str | None = None, limit: int = 20) -> tuple[dict[str, object], ...]:
        raw = self.sample(connector_id, mapping.namespace, secret=secret, limit=limit)
        with self._lock:
            config = self._configs.get(connector_id)
        if config is None:
            raise ValueError("unknown connector")
        documents = tuple(asdict(map_record(config, mapping, row)) for row in raw["rows"])
        identities = {item["fact_id"]: item["source_primary_key"] for item in documents}
        if len(identities) != len(documents):
            raise ValueError("connector FactId collision inside preview")
        with self._lock:
            self._previewed_mappings.setdefault(connector_id, set()).add(mapping)
        return documents

    def create_managed_namespace(self, connector_id: str, *, secret: str | None = None,
                                 authorized: bool = False) -> str:
        if authorized is not True:
            raise ValueError("managed namespace creation requires explicit authorization")
        with self._lock:
            config = self._configs.get(connector_id)
            previewed = bool(self._previewed_mappings.get(connector_id))
        if config is None:
            raise ValueError("unknown connector")
        if not previewed:
            raise ValueError("managed namespace creation requires a successful preview")
        options = dict(config.options)
        options["managed_write"] = True
        connector = self._factory(replace(config, secret=secret, options=options))
        try:
            if not connector.capabilities.managed_namespace:
                raise ValueError("connector does not support a managed namespace")
            return connector.create_managed_namespace()
        finally:
            connector.close()


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {
            "__proofray_transport_scalar_v1__": "bytes",
            "value": value.hex(),
        }
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("connector decimal sample must be finite")
        return {
            "__proofray_transport_scalar_v1__": "decimal",
            "value": str(value),
        }
    if isinstance(value, datetime):
        return {
            "__proofray_transport_scalar_v1__": "datetime",
            "value": value.isoformat(),
        }
    if isinstance(value, date):
        return {
            "__proofray_transport_scalar_v1__": "date",
            "value": value.isoformat(),
        }
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    raise ValueError(f"connector sample contains unsupported scalar {type(value).__name__}")


def _bounded_rows(rows, *, byte_limit: int = 384 * 1024) -> dict[str, object]:
    published = []
    used = 2
    for row in rows:
        safe = _json_value(dict(row))
        encoded = json.dumps(
            safe, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        if published and used + len(encoded) + 1 > byte_limit:
            break
        if len(encoded) + 2 > byte_limit:
            raise ValueError("one connector sample row exceeds the preview boundary")
        published.append(safe)
        used += len(encoded) + (1 if len(published) > 1 else 0)
    return {
        "rows": published,
        "rows_truncated": len(published) < len(rows),
    }


def _batch_identity(
        connector_id: str, documents: tuple[MappedDocument, ...]) -> str:
    if not documents:
        raise ValueError("connector batch identity requires documents")
    payload = json.dumps([
        [item.fact_id, item.version, item.content_sha256]
        for item in documents
    ], separators=(",", ":"), allow_nan=False).encode("utf-8")
    return f"connector:{connector_id}:" + hashlib.sha256(payload).hexdigest()


__all__ = ["ConnectorManager"]
