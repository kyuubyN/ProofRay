from __future__ import annotations

from typing import Iterator, Mapping
from urllib.parse import unquote, urlparse, urlunparse

from .base import (
    CAPABILITIES, ConnectorCapabilities, ConnectorConfig, ConnectorKind,
    ConnectorNamespace, DocumentMapping, SchemaSample, require_safe_identifier,
    is_loopback_host,
)


def _checkpoint_id(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    try:
        from bson import ObjectId
    except ImportError:  # pragma: no cover - dependency is loaded with PyMongo
        ObjectId = ()
    if isinstance(value, ObjectId):
        return {"type": "mongodb_object_id", "hex": str(value)}
    raise ValueError("MongoDB incremental ID is not checkpoint-serializable")


def _resume_id(value: object) -> object:
    if isinstance(value, dict) and value.get("type") == "mongodb_object_id":
        raw = value.get("hex")
        if not isinstance(raw, str) or len(raw) != 24:
            raise ValueError("invalid MongoDB ObjectId checkpoint")
        from bson import ObjectId
        return ObjectId(raw)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("invalid MongoDB checkpoint")


def _connection_endpoint_and_credentials(
        endpoint: str, secret: str | None) -> tuple[str, dict[str, str]]:
    parsed = urlparse(endpoint)
    if not secret:
        return endpoint, {}
    if not parsed.username:
        raise ValueError("MongoDB secret requires a username in the endpoint")
    # Remove userinfo without reparsing the comma-separated seed-list. PyMongo
    # receives credentials as call-scoped kwargs, so the secret never appears
    # in a reconstructed URI or retained ConnectorConfig.
    netloc = parsed.netloc.split("@", 1)[1]
    return urlunparse(parsed._replace(netloc=netloc)), {
        "username": unquote(parsed.username),
        "password": secret,
    }


class MongoDBConnector:
    kind = ConnectorKind.MONGODB
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
            from pymongo import MongoClient
            options = {"serverSelectionTimeoutMS": 10_000}
            parsed = urlparse(self.config.endpoint)
            if not is_loopback_host(parsed.hostname):
                options["tls"] = True
                ca_file = self.config.options.get("tls_ca_file")
                if isinstance(ca_file, str) and ca_file:
                    options["tlsCAFile"] = ca_file
            endpoint, credentials = _connection_endpoint_and_credentials(
                self.config.endpoint, self.config.secret)
            self._client = MongoClient(endpoint, **credentials, **options)
        return self._client

    def _database_name(self) -> str:
        configured = self.config.options.get("database")
        parsed = urlparse(self.config.endpoint)
        name = str(configured or parsed.path.lstrip("/"))
        if not name:
            raise ValueError("MongoDB database must be selected")
        return name

    def test_connection(self) -> None:
        self.client.admin.command("ping")

    def discover(self) -> tuple[ConnectorNamespace, ...]:
        database = self.client[self._database_name()]
        result = []
        for collection_name in sorted(database.list_collection_names()):
            collection = database[collection_name]
            sample = tuple(collection.find({}).limit(50))
            fields = tuple(sorted({str(key) for row in sample for key in row})) or ("_id",)
            result.append(ConnectorNamespace(
                f"{self._database_name()}.{collection_name}", collection_name,
                fields, ("_id",),
                estimated_rows=collection.estimated_document_count(),
            ))
        return tuple(result)

    def _collection(self, namespace: str):
        parts = namespace.split(".", 1)
        if len(parts) != 2 or parts[0] != self._database_name():
            raise ValueError("unknown MongoDB namespace")
        return self.client[parts[0]][parts[1]]

    def sample(self, namespace: str, *, limit: int = 50) -> SchemaSample:
        if not 1 <= limit <= 50:
            raise ValueError("sample limit must be in [1,50]")
        known = {item.identity: item for item in self.discover()}
        descriptor = known.get(namespace)
        if descriptor is None:
            raise ValueError("unknown MongoDB namespace")
        rows = tuple(dict(row) for row in self._collection(namespace).find({}).limit(limit))
        return SchemaSample(descriptor, rows)

    def stream(self, mapping: DocumentMapping, *, batch_size: int = 256,
               checkpoint: Mapping[str, object] | None = None) \
            -> Iterator[tuple[Mapping[str, object], ...]]:
        if not 1 <= batch_size <= 2048:
            raise ValueError("sync batch size must be in [1,2048]")
        if mapping.namespace not in {item.identity for item in self.discover()}:
            raise ValueError("unknown MongoDB namespace")
        collection = self._collection(mapping.namespace)
        query = {}
        last_id = None if checkpoint is None else _resume_id(checkpoint.get("last_id"))
        if last_id is not None:
            query = {mapping.id_field: {"$gt": last_id}}
        cursor = collection.find(query).sort(mapping.id_field, 1).batch_size(batch_size)
        batch = []
        for row in cursor:
            batch.append(dict(row))
            if len(batch) == batch_size:
                self.last_checkpoint = {
                    "last_id": _checkpoint_id(batch[-1][mapping.id_field]),
                }
                yield tuple(batch)
                batch = []
        if batch:
            self.last_checkpoint = {
                "last_id": _checkpoint_id(batch[-1][mapping.id_field]),
            }
            yield tuple(batch)

    def create_managed_namespace(self, name: str = "proofray_memory") -> str:
        if not bool(self.config.options.get("managed_write", False)):
            raise RuntimeError("managed namespace requires explicit write authorization")
        if name != "proofray_memory":
            raise ValueError("MongoDB managed namespace is fixed")
        collection_name = require_safe_identifier(name)
        database = self.client[self._database_name()]
        if collection_name not in database.list_collection_names():
            database.create_collection(collection_name)
        database[collection_name].create_index("id", unique=True)
        return f"{self._database_name()}.{collection_name}"

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None


__all__ = ["MongoDBConnector"]
