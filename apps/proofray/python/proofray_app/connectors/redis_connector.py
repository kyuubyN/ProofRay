from __future__ import annotations

from typing import Iterator, Mapping
from urllib.parse import unquote, urlparse, urlunparse

from .base import (
    CAPABILITIES, ConnectorCapabilities, ConnectorConfig, ConnectorKind,
    ConnectorNamespace, DocumentMapping, SchemaSample,
)


def _connection_endpoint_and_credentials(
        endpoint: str, secret: str | None) -> tuple[str, dict[str, str]]:
    parsed = urlparse(endpoint)
    if not secret:
        return endpoint, {}
    hostname = parsed.hostname or ""
    if parsed.port is not None:
        hostname += f":{parsed.port}"
    return urlunparse(parsed._replace(netloc=hostname)), {
        **({"username": unquote(parsed.username)} if parsed.username else {}),
        "password": secret,
    }


class RedisConnector:
    kind = ConnectorKind.REDIS
    capabilities: ConnectorCapabilities = CAPABILITIES[kind]

    def __init__(self, config: ConnectorConfig):
        if config.kind != self.kind:
            raise ValueError("connector kind differs from implementation")
        self.config = config
        self._client = None
        self.last_checkpoint: dict[str, object] = {}

    @property
    def client(self):
        if self._client is None:
            import redis
            endpoint, credentials = _connection_endpoint_and_credentials(
                self.config.endpoint, self.config.secret)
            self._client = redis.Redis.from_url(
                endpoint,
                decode_responses=True, socket_connect_timeout=10,
                socket_timeout=30, **credentials,
            )
        return self._client

    def test_connection(self) -> None:
        if self.client.ping() is not True:
            raise RuntimeError("Redis ping failed")

    def discover(self) -> tuple[ConnectorNamespace, ...]:
        prefixes = set()
        total = 0
        for key in self.client.scan_iter(match="*", count=500):
            total += 1
            prefixes.add(str(key).split(":", 1)[0])
            if total >= 5000:
                break
        result = [ConnectorNamespace(
            f"{prefix}:*", f"{prefix}:*", ("key", "value"), ("key",))
                  for prefix in sorted(prefixes)]
        if not result:
            result.append(ConnectorNamespace("*", "All keys", ("key", "value"), ("key",), 0))
        return tuple(result)

    def _pattern(self, namespace: str) -> str:
        if namespace == "*":
            return namespace
        prefix, separator, wildcard = namespace.partition(":")
        if not prefix or separator != ":" or wildcard != "*" or any(
                character in prefix for character in "*?[]"):
            raise ValueError("invalid Redis namespace")
        return namespace

    def sample(self, namespace: str, *, limit: int = 50) -> SchemaSample:
        if not 1 <= limit <= 50:
            raise ValueError("sample limit must be in [1,50]")
        pattern = self._pattern(namespace)
        rows = []
        for key in self.client.scan_iter(match=pattern, count=limit):
            if self.client.type(key) != "string":
                continue
            rows.append({"key": key, "value": self.client.get(key)})
            if len(rows) == limit:
                break
        descriptor = ConnectorNamespace(namespace, namespace, ("key", "value"), ("key",))
        return SchemaSample(descriptor, tuple(rows))

    def stream(self, mapping: DocumentMapping, *, batch_size: int = 256,
               checkpoint: Mapping[str, object] | None = None) \
            -> Iterator[tuple[Mapping[str, object], ...]]:
        if not 1 <= batch_size <= 2048:
            raise ValueError("sync batch size must be in [1,2048]")
        if checkpoint and checkpoint.get("complete") is True:
            return
        cursor = 0 if checkpoint is None else int(checkpoint.get("cursor", 0))
        pattern = self._pattern(mapping.namespace)
        if pattern not in {item.identity for item in self.discover()}:
            raise ValueError("unknown Redis namespace")
        seen_cursors = set()
        while True:
            cursor, keys = self.client.scan(cursor=cursor, match=pattern, count=batch_size)
            if cursor != 0 and cursor in seen_cursors:
                raise RuntimeError("Redis pagination made no progress")
            seen_cursors.add(cursor)
            rows = []
            for key in keys:
                if self.client.type(key) == "string":
                    rows.append({"key": key, "value": self.client.get(key)})
            self.last_checkpoint = {
                "cursor": int(cursor), "complete": int(cursor) == 0,
            }
            if rows:
                yield tuple(rows)
            if cursor == 0:
                break

    def create_managed_namespace(self, name: str = "proofray_memory") -> str:
        if not bool(self.config.options.get("managed_write", False)):
            raise RuntimeError("managed namespace requires explicit write authorization")
        if name not in ("proofray_memory", "proofray:memory"):
            raise ValueError("Redis managed namespace is fixed")
        return "proofray:memory:*"

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


__all__ = ["RedisConnector"]
