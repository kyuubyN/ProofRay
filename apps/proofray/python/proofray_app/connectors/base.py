from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import ipaddress
from pathlib import Path
import re
from typing import Iterator, Mapping, Protocol
from urllib.parse import parse_qsl, urlparse


class ConnectorKind(str, Enum):
    SQLITE = "sqlite"
    DUCKDB = "duckdb"
    MONGODB = "mongodb"
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    REDIS = "redis"
    DYNAMODB = "dynamodb"
    ELASTICSEARCH = "elasticsearch"
    SPACETIMEDB = "spacetimedb"


_APP_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,159}")
_CONNECTOR_SCHEMES: dict[ConnectorKind, frozenset[str]] = {
    ConnectorKind.SQLITE: frozenset({"", "file", "sqlite", "sqlite3"}),
    ConnectorKind.DUCKDB: frozenset({"", "file", "duckdb"}),
    ConnectorKind.MONGODB: frozenset({"mongodb", "mongodb+srv"}),
    ConnectorKind.POSTGRESQL: frozenset({"postgres", "postgresql"}),
    ConnectorKind.MYSQL: frozenset({"mysql", "mysql+pymysql"}),
    ConnectorKind.REDIS: frozenset({"redis", "rediss"}),
    ConnectorKind.DYNAMODB: frozenset({"dynamodb", "aws+dynamodb"}),
    ConnectorKind.ELASTICSEARCH: frozenset({
        "http", "https", "elasticsearch+http", "elasticsearch+https",
        "opensearch+http", "opensearch+https",
    }),
    ConnectorKind.SPACETIMEDB: frozenset({
        "http", "https", "spacetimedb+http", "spacetimedb+https",
    }),
}


MAX_MAPPED_TEXT_BYTES = 128 * 1024


@dataclass(frozen=True)
class ConnectorCapabilities:
    discovery: bool = True
    incremental_sync: bool = True
    managed_namespace: bool = True
    host_runtime: bool = False
    read_only_source: bool = True


CAPABILITIES: dict[ConnectorKind, ConnectorCapabilities] = {
    ConnectorKind.SQLITE: ConnectorCapabilities(host_runtime=True),
    ConnectorKind.DUCKDB: ConnectorCapabilities(host_runtime=True),
    ConnectorKind.MONGODB: ConnectorCapabilities(),
    ConnectorKind.POSTGRESQL: ConnectorCapabilities(),
    ConnectorKind.MYSQL: ConnectorCapabilities(),
    ConnectorKind.REDIS: ConnectorCapabilities(),
    ConnectorKind.DYNAMODB: ConnectorCapabilities(),
    ConnectorKind.ELASTICSEARCH: ConnectorCapabilities(),
    ConnectorKind.SPACETIMEDB: ConnectorCapabilities(
        managed_namespace=False),
}


@dataclass(frozen=True)
class ConnectorConfig:
    connector_id: str
    kind: ConnectorKind
    endpoint: str
    options: Mapping[str, object] = field(default_factory=dict)
    secret: str | None = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if (not _APP_IDENTIFIER.fullmatch(self.connector_id)
                or not self.endpoint or len(self.endpoint) > 4096 or
                any(ord(character) < 32 for character in self.endpoint)):
            raise ValueError("connector needs a safe identity and endpoint")
        parsed = urlparse(self.endpoint)
        scheme = "" if re.fullmatch(r"[A-Za-z]:[\\/].*", self.endpoint) \
            else parsed.scheme.casefold()
        if scheme not in _CONNECTOR_SCHEMES[self.kind]:
            raise ValueError("connector endpoint scheme differs from selected kind")
        if parsed.password is not None:
            raise ValueError("connector credentials must use an ephemeral secret lease")
        if any(re.search(
                r"token|secret|password|passphrase|api.?key|credential|access.?key",
                key, re.IGNORECASE) for key, _value in parse_qsl(parsed.query)):
            raise ValueError("connector query credentials must use an ephemeral secret lease")
        if parsed.fragment or (
                parsed.query and self.kind not in (
                    ConnectorKind.MONGODB, ConnectorKind.REDIS)):
            raise ValueError("connector endpoint contains unsupported URL components")
        _validate_secretless_options(self.options)
        # Detach retained configuration from a mutable mapping owned by the
        # caller. Managed-write grants are one-shot and handled by the manager.
        object.__setattr__(self, "options", dict(self.options))
        if (self.kind == ConnectorKind.REDIS and parsed.scheme == "redis"
                and not is_loopback_host(parsed.hostname)):
            raise ValueError("remote Redis endpoints require rediss TLS")


@dataclass(frozen=True)
class ConnectorNamespace:
    identity: str
    display_name: str
    fields: tuple[str, ...]
    primary_keys: tuple[str, ...] = ()
    estimated_rows: int | None = None

    def __post_init__(self) -> None:
        if (not self.identity or not self.display_name or not self.fields or
                len(set(self.fields)) != len(self.fields) or
                any(key not in self.fields for key in self.primary_keys)):
            raise ValueError("invalid connector namespace")


@dataclass(frozen=True)
class SchemaSample:
    namespace: ConnectorNamespace
    rows: tuple[Mapping[str, object], ...]

    def __post_init__(self) -> None:
        if len(self.rows) > 50:
            raise ValueError("schema sampling is capped at 50 rows")


@dataclass(frozen=True)
class DocumentMapping:
    namespace: str
    id_field: str
    text_field: str
    source_field: str | None = None
    session_field: str | None = None
    sequence_field: str | None = None
    event_time_field: str | None = None
    role_field: str | None = None
    speaker_field: str | None = None
    version_field: str | None = None
    scope_id: int = 1

    def __post_init__(self) -> None:
        values = (
            self.namespace, self.id_field, self.text_field,
            self.source_field, self.session_field, self.sequence_field,
            self.event_time_field, self.role_field, self.speaker_field,
            self.version_field,
        )
        if (not self.namespace or not self.id_field or not self.text_field or
                any(value is not None and (not isinstance(value, str) or not value)
                    for value in values) or not 0 <= self.scope_id < (1 << 32)):
            raise ValueError("invalid document mapping")


@dataclass(frozen=True)
class MappedDocument:
    fact_id: int
    text: str
    scope_id: int
    session_id: str
    version: int
    source: str
    sequence: int | None
    event_time: int | None
    role: str | None
    speaker: str | None
    source_primary_key: str
    content_sha256: str


class Connector(Protocol):
    kind: ConnectorKind
    capabilities: ConnectorCapabilities

    def test_connection(self) -> None: ...
    def discover(self) -> tuple[ConnectorNamespace, ...]: ...
    def sample(self, namespace: str, *, limit: int = 50) -> SchemaSample: ...
    def stream(self, mapping: DocumentMapping, *, batch_size: int = 256,
               checkpoint: Mapping[str, object] | None = None) \
            -> Iterator[tuple[Mapping[str, object], ...]]: ...
    def create_managed_namespace(self, name: str = "proofray_memory") -> str: ...
    def close(self) -> None: ...


_SAFE_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_SENSITIVE_OPTION = re.compile(
    r"token|secret|password|passphrase|api.?key|credential|access.?key",
    re.IGNORECASE,
)


def _validate_secretless_options(options: Mapping[str, object]) -> None:
    if not isinstance(options, Mapping):
        raise ValueError("connector options must be a mapping")

    def inspect(value: object) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                if not isinstance(key, str) or _SENSITIVE_OPTION.search(key):
                    raise ValueError("connector options cannot contain credentials")
                inspect(item)
            return
        if isinstance(value, (tuple, list)):
            for item in value:
                inspect(item)
            return
        if isinstance(value, str) and "://" in value:
            parsed = urlparse(value)
            if parsed.password is not None or any(
                    _SENSITIVE_OPTION.search(key)
                    for key, _item in parse_qsl(parsed.query)):
                raise ValueError("connector options cannot contain credential URIs")

    inspect(options)


def require_safe_identifier(value: str) -> str:
    if not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value):
        raise ValueError("unsafe database identifier")
    return value


def is_loopback_host(host: str | None) -> bool:
    if host is None:
        return False
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def detect_connector_kind(value: str) -> ConnectorKind | None:
    if not isinstance(value, str) or not value.strip():
        return None
    stripped = value.strip()
    lowered = stripped.casefold()
    path = Path(stripped)
    if lowered.endswith((".sqlite", ".sqlite3", ".db")):
        return ConnectorKind.SQLITE
    if lowered.endswith(".duckdb"):
        return ConnectorKind.DUCKDB
    parsed = urlparse(stripped)
    scheme = parsed.scheme.casefold()
    if scheme in ("sqlite", "sqlite3"):
        return ConnectorKind.SQLITE
    if scheme == "duckdb":
        return ConnectorKind.DUCKDB
    if scheme in ("mongodb", "mongodb+srv"):
        return ConnectorKind.MONGODB
    if scheme in ("postgres", "postgresql"):
        return ConnectorKind.POSTGRESQL
    if scheme in ("mysql", "mysql+pymysql"):
        return ConnectorKind.MYSQL
    if scheme in ("redis", "rediss"):
        return ConnectorKind.REDIS
    if scheme in ("dynamodb", "aws+dynamodb"):
        return ConnectorKind.DYNAMODB
    if scheme in ("elasticsearch+http", "elasticsearch+https", "opensearch+http",
                  "opensearch+https"):
        return ConnectorKind.ELASTICSEARCH
    if scheme in ("spacetimedb+http", "spacetimedb+https"):
        return ConnectorKind.SPACETIMEDB
    if path.suffix and not parsed.scheme:
        return None
    # Plain HTTP(S) is deliberately ambiguous: never probe arbitrary endpoints
    # or attach credentials until the user confirms Elasticsearch/SpacetimeDB.
    return None


def stable_connector_fact_id(connector_id: str, namespace: str,
                             primary_key: object) -> int:
    if not connector_id or not namespace:
        raise ValueError("connector and namespace identities are required")
    payload = (connector_id.encode("utf-8") + b"\x00" +
               namespace.encode("utf-8") + b"\x00" +
               _canonical_primary_key(primary_key))
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") & ((1 << 62) - 1)


def _canonical_primary_key(value: object) -> bytes:
    """Type-tag one database scalar so distinct primary keys cannot alias."""
    if isinstance(value, bool):
        return b"bool:" + (b"1" if value else b"0")
    if isinstance(value, int):
        return b"int:" + str(value).encode("ascii")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("primary-key decimal must be finite")
        return b"decimal:" + str(value).encode("ascii")
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("primary-key float must be finite")
        return b"float:" + value.hex().encode("ascii")
    if isinstance(value, str):
        return b"text:" + value.encode("utf-8")
    if isinstance(value, bytes):
        return b"bytes:" + value
    if isinstance(value, datetime):
        return b"datetime:" + value.isoformat().encode("utf-8")
    if isinstance(value, date):
        return b"date:" + value.isoformat().encode("ascii")
    raise ValueError("primary key must be a deterministic database scalar")


def _required(row: Mapping[str, object], field_name: str) -> object:
    if field_name not in row or row[field_name] is None:
        raise ValueError(f"mapped field {field_name!r} is absent")
    return _transport_scalar(row[field_name])


def _optional(row: Mapping[str, object], field_name: str | None) -> object | None:
    return None if field_name is None else _transport_scalar(row.get(field_name))


def _transport_scalar(value: object) -> object:
    if not isinstance(value, Mapping):
        return value
    kind = value.get("__proofray_transport_scalar_v1__")
    raw = value.get("value")
    if kind == "bytes" and isinstance(raw, str):
        try:
            return bytes.fromhex(raw)
        except ValueError:
            raise ValueError("invalid transported byte scalar") from None
    if kind == "decimal" and isinstance(raw, str):
        try:
            result = Decimal(raw)
        except InvalidOperation:
            raise ValueError("invalid transported decimal scalar") from None
        if not result.is_finite():
            raise ValueError("transported decimal scalar must be finite")
        return result
    if kind == "datetime" and isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("invalid transported datetime scalar") from None
    if kind == "date" and isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            raise ValueError("invalid transported date scalar") from None
    return value


def _integer(value: object | None, *, default: int | None = None,
             minimum: int = 0) -> int | None:
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError("boolean is not an integer coordinate")
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError("invalid integer coordinate") from None
    if result < minimum:
        raise ValueError("integer coordinate is outside its domain")
    return result


def _event_day(value: object | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().toordinal()
    if isinstance(value, date):
        return value.toordinal()
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        timestamp = datetime.fromtimestamp(float(value), tz=timezone.utc)
        return timestamp.date().toordinal()
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date().toordinal()
        except ValueError:
            try:
                return date.fromisoformat(value).toordinal()
            except ValueError:
                pass
    raise ValueError("event time must be an ISO date/datetime or Unix timestamp")


def map_record(config: ConnectorConfig, mapping: DocumentMapping,
               row: Mapping[str, object]) -> MappedDocument:
    primary_value = _required(row, mapping.id_field)
    # Validate typed identity before any lossy display conversion.
    fact_id = stable_connector_fact_id(
        config.connector_id, mapping.namespace, primary_value)
    primary_key = str(primary_value)
    if not primary_key or len(primary_key.encode("utf-8")) > 512:
        raise ValueError("mapped primary key is empty or too large")
    text_value = _required(row, mapping.text_field)
    if not isinstance(text_value, str):
        raise ValueError("mapped document text must already be exact source text")
    text = text_value
    if not text.strip() or len(text.encode("utf-8")) > MAX_MAPPED_TEXT_BYTES:
        raise ValueError("mapped document text is empty or too large")
    source_value = _optional(row, mapping.source_field)
    if source_value is not None:
        source_component = str(source_value)
        if not source_component or len(source_component.encode("utf-8")) > 512:
            raise ValueError("mapped source identity is empty or too large")
        source = f"connector:{config.connector_id}:{source_component}"
    else:
        source = f"connector:{config.connector_id}:{mapping.namespace}:{primary_key}"
    session_value = _optional(row, mapping.session_field)
    session = str(session_value) if session_value is not None else mapping.namespace
    if not session or len(session.encode("utf-8")) > 256:
        raise ValueError("mapped session identity is empty or too large")
    role_value = _optional(row, mapping.role_field)
    role = str(role_value).casefold() if role_value is not None else None
    if role not in (None, "user", "assistant", "system", "tool"):
        raise ValueError("mapped role is outside the closed transport enum")
    speaker_value = _optional(row, mapping.speaker_field)
    speaker = str(speaker_value).strip() if speaker_value is not None else None
    if speaker == "":
        speaker = None
    if speaker is not None and len(speaker.encode("utf-8")) > 256:
        raise ValueError("mapped speaker is too large")
    version = _integer(_optional(row, mapping.version_field), default=1, minimum=1)
    sequence = _integer(_optional(row, mapping.sequence_field), minimum=0)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return MappedDocument(
        fact_id,
        text, mapping.scope_id, session, int(version), source, sequence,
        _event_day(_optional(row, mapping.event_time_field)), role, speaker,
        primary_key, digest,
    )


__all__ = [
    "CAPABILITIES", "Connector", "ConnectorCapabilities", "ConnectorConfig",
    "ConnectorKind", "ConnectorNamespace", "DocumentMapping", "MappedDocument",
    "SchemaSample", "detect_connector_kind", "is_loopback_host", "map_record", "require_safe_identifier",
    "stable_connector_fact_id",
]
