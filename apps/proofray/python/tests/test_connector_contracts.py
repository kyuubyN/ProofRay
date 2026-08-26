from __future__ import annotations

from decimal import Decimal
import json

import pytest

from proofray_app.connector_manager import ConnectorManager
from proofray_app.connectors.base import (
    CAPABILITIES, ConnectorConfig, ConnectorKind, ConnectorNamespace,
    DocumentMapping, SchemaSample,
)
from proofray_app.connectors.dynamodb import DynamoDBConnector
from proofray_app.connectors.elasticsearch import ElasticsearchConnector
from proofray_app.connectors.http_json import MAX_JSON_RESPONSE_BYTES, SafeJsonTransport
from proofray_app.connectors.mongodb import (
    MongoDBConnector, _checkpoint_id, _connection_endpoint_and_credentials, _resume_id,
)
from proofray_app.connectors.redis_connector import RedisConnector
from proofray_app.connectors.spacetimedb import SpacetimeDBConnector


@pytest.mark.parametrize("kind", tuple(ConnectorKind))
def test_every_connector_declares_existing_sources_read_only(kind):
    assert CAPABILITIES[kind].read_only_source is True
    assert CAPABILITIES[kind].discovery is True
    assert CAPABILITIES[kind].incremental_sync is True
    assert CAPABILITIES[kind].managed_namespace is (kind != ConnectorKind.SPACETIMEDB)


def test_mongodb_object_id_checkpoint_round_trips_as_plain_json():
    from bson import ObjectId

    original = ObjectId("64f0c2a117d4553b3a0f0001")
    encoded = _checkpoint_id(original)
    assert json.loads(json.dumps(encoded)) == encoded
    assert _resume_id(encoded) == original
    with pytest.raises(ValueError):
        _resume_id({"type": "mongodb_object_id", "hex": "bad"})


def test_mongodb_secret_lease_preserves_seed_list_without_entering_uri():
    endpoint, credentials = _connection_endpoint_and_credentials(
        "mongodb://user@host1:27017,host2:27017/db", "secret")
    assert endpoint == "mongodb://host1:27017,host2:27017/db"
    assert credentials == {"username": "user", "password": "secret"}
    assert "secret" not in endpoint


class _MongoCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    def sort(self, field, direction):
        assert direction == 1
        self.rows.sort(key=lambda row: row[field])
        return self

    def batch_size(self, _value):
        return self

    def __iter__(self):
        return iter(self.rows)


class _MongoCollection:
    def __init__(self, rows=()):
        self.rows = [dict(row) for row in rows]
        self.indexes = []

    def find(self, query):
        rows = self.rows
        if query:
            field, condition = next(iter(query.items()))
            rows = [row for row in rows if row[field] > condition["$gt"]]
        return _MongoCursor(rows)

    def estimated_document_count(self):
        return len(self.rows)

    def create_index(self, field, unique=False):
        self.indexes.append((field, unique))


class _MongoDatabase:
    def __init__(self):
        self.collections = {
            "notes": _MongoCollection((
                {"_id": 1, "text": "one"},
                {"_id": 2, "text": "two"},
                {"_id": 3, "text": "three"},
            )),
        }

    def list_collection_names(self):
        return list(self.collections)

    def __getitem__(self, name):
        return self.collections[name]

    def create_collection(self, name):
        self.collections[name] = _MongoCollection()


class _MongoClient:
    def __init__(self):
        self.database = _MongoDatabase()

    def __getitem__(self, _name):
        return self.database

    def close(self):
        return None


def test_mongodb_fake_service_pages_resumes_and_scopes_managed_collection():
    connector = MongoDBConnector(ConnectorConfig(
        "mongo", ConnectorKind.MONGODB, "mongodb://localhost/app"))
    connector._client = _MongoClient()
    mapping = DocumentMapping("app.notes", "_id", "text")
    assert [item.identity for item in connector.discover()] == ["app.notes"]
    assert [len(batch) for batch in connector.stream(mapping, batch_size=2)] == [2, 1]
    assert connector.last_checkpoint == {"last_id": 3}
    resumed = tuple(connector.stream(
        mapping, batch_size=2, checkpoint={"last_id": 2}))
    assert resumed == (({"_id": 3, "text": "three"},),)

    managed = MongoDBConnector(ConnectorConfig(
        "mongo", ConnectorKind.MONGODB, "mongodb://localhost/app",
        {"managed_write": True}))
    managed._client = connector._client
    assert managed.create_managed_namespace() == "app.proofray_memory"
    assert managed._client.database.collections["proofray_memory"].indexes == [("id", True)]
    with pytest.raises(ValueError, match="fixed"):
        managed.create_managed_namespace("another")


def test_dynamodb_composite_decimal_checkpoint_round_trips_as_plain_json():
    original = {"account": "user-1", "sequence": Decimal("9007199254740993")}
    encoded = DynamoDBConnector._encode_key(original)
    persisted = json.loads(json.dumps(encoded))
    assert DynamoDBConnector._decode_key(persisted) == original


class _DynamoClient:
    def __init__(self):
        self.created = []

    def list_tables(self, **request):
        assert request["Limit"] in (1, 100)
        return {"TableNames": ["notes"]}

    def describe_table(self, *, TableName):
        assert TableName == "notes"
        return {"Table": {
            "KeySchema": [{"AttributeName": "id", "KeyType": "HASH"}],
            "ItemCount": 3,
        }}


class _DynamoTable:
    def __init__(self, name):
        self.name = name
        self.waited = False

    def scan(self, **request):
        if request["Limit"] == 50:
            return {"Items": [{"id": "a", "text": "one"}]}
        if "ExclusiveStartKey" not in request:
            return {
                "Items": [{"id": "a", "text": "one"}],
                "LastEvaluatedKey": {"id": "a"},
            }
        return {"Items": [{"id": "b", "text": "two"}]}

    def wait_until_exists(self):
        self.waited = True


class _DynamoResource:
    def __init__(self):
        self.client = _DynamoClient()
        self.meta = type("Meta", (), {"client": self.client})()
        self.tables = {"notes": _DynamoTable("notes")}
        self.created = None

    def Table(self, name):
        return self.tables[name]

    def create_table(self, **request):
        self.created = request
        table = _DynamoTable(request["TableName"])
        self.tables[request["TableName"]] = table
        return table


def test_dynamodb_fake_service_pages_and_managed_table_is_fixed():
    resource = _DynamoResource()
    connector = DynamoDBConnector(ConnectorConfig(
        "dynamo", ConnectorKind.DYNAMODB, "dynamodb://us-east-1"))
    connector._resource = resource
    mapping = DocumentMapping("notes", "id", "text")
    assert [len(batch) for batch in connector.stream(mapping, batch_size=1)] == [1, 1]
    assert connector.last_checkpoint == {"complete": True}

    managed = DynamoDBConnector(ConnectorConfig(
        "dynamo", ConnectorKind.DYNAMODB, "dynamodb://us-east-1",
        {"managed_write": True}))
    managed._resource = resource
    assert managed.create_managed_namespace() == "proofray_memory"
    assert resource.created["BillingMode"] == "PAY_PER_REQUEST"
    assert resource.tables["proofray_memory"].waited is True
    with pytest.raises(ValueError, match="fixed"):
        managed.create_managed_namespace("another")


class _RedisClient:
    def scan_iter(self, *, match, count):
        assert match == "*" and count == 500
        return iter(("memory:item",))

    def scan(self, *, cursor, match, count):
        assert cursor == 0 and match == "memory:*" and count == 256
        return 0, ("memory:hash",)

    def type(self, key):
        return "hash"

    def get(self, key):
        return "value"


def test_redis_commits_terminal_cursor_even_when_page_has_no_string_rows():
    connector = RedisConnector(ConnectorConfig(
        "redis", ConnectorKind.REDIS, "redis://localhost/0"))
    connector._client = _RedisClient()
    assert tuple(connector.stream(
        DocumentMapping("memory:*", "key", "value"))) == ()
    assert connector.last_checkpoint == {"cursor": 0, "complete": True}


def test_redis_rejects_a_repeating_nonterminal_cursor():
    class Repeating(_RedisClient):
        def scan(self, *, cursor, match, count):
            return 5, ("memory:item",)

        def type(self, key):
            return "string"

    connector = RedisConnector(ConnectorConfig(
        "redis", ConnectorKind.REDIS, "redis://localhost/0"))
    connector._client = Repeating()
    with pytest.raises(RuntimeError, match="no progress"):
        tuple(connector.stream(DocumentMapping("memory:*", "key", "value")))


class _Response:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, limit):
        assert limit == MAX_JSON_RESPONSE_BYTES + 1
        return self.payload[:limit]


class _Opener:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.timeout = None

    def open(self, _request, *, timeout):
        self.timeout = timeout
        return _Response(self.payload)


def test_http_transport_caps_payload_and_passes_timeout_without_network():
    transport = SafeJsonTransport(timeout=3.5)
    opener = _Opener(b'{"ok":true}')
    transport._opener = opener
    assert transport.request("GET", "https://example.test") == {"ok": True}
    assert opener.timeout == 3.5

    transport._opener = _Opener(b"x" * (MAX_JSON_RESPONSE_BYTES + 1))
    with pytest.raises(RuntimeError, match="connector_response_too_large"):
        transport.request("GET", "https://example.test")
    with pytest.raises(ValueError, match="TLS"):
        transport.request("GET", "http://remote.example.test")


class _CollisionConnector:
    kind = ConnectorKind.POSTGRESQL
    capabilities = CAPABILITIES[kind]

    def __init__(self, config):
        self.config = config
        self.last_checkpoint = {"offset": 2}

    def test_connection(self):
        return None

    def discover(self):
        return (ConnectorNamespace("public.notes", "notes", ("id", "text")),)

    def sample(self, namespace, *, limit=50):
        return SchemaSample(self.discover()[0], ())

    def stream(self, mapping, *, batch_size=256, checkpoint=None):
        yield ({"id": "same", "text": "one"}, {"id": "same", "text": "two"})

    def create_managed_namespace(self, name="proofray_memory"):
        return name

    def close(self):
        return None


def test_sync_rejects_fact_id_collision_before_authoritative_commit():
    manager = ConnectorManager(factory=_CollisionConnector)
    manager.configure(ConnectorConfig(
        "postgres", ConnectorKind.POSTGRESQL, "postgresql://user@localhost/db"))
    committed = []
    manager.preview(
        "postgres", DocumentMapping("public.notes", "id", "text"))
    with pytest.raises(ValueError, match="FactId collision"):
        manager.sync(
            "postgres", DocumentMapping("public.notes", "id", "text"),
            ingest_batch=lambda *value: committed.append(value),
        )
    assert committed == []


class _ElasticTransport:
    def __init__(self):
        self.calls = []

    def request(self, method, url, *, headers=None, body=None):
        self.calls.append((method, url, body))
        if url.endswith("/_cat/indices?format=json&h=index,docs.count"):
            return [{"index": "notes", "docs.count": "1"}]
        if url.endswith("/notes/_mapping"):
            return {"notes": {"mappings": {"properties": {"text": {"type": "text"}}}}}
        if url.endswith("/notes/_search"):
            return {"hits": {"hits": [{
                "_id": "internal-a", "_source": {"id": "a", "text": "one"},
                "sort": ["a"],
            }]}}
        if method == "PUT" and url.endswith("/proofray-memory"):
            return {"acknowledged": True}
        raise AssertionError((method, url))


def test_elasticsearch_checkpoint_and_managed_index_are_fixed():
    transport = _ElasticTransport()
    connector = ElasticsearchConnector(ConnectorConfig(
        "elastic", ConnectorKind.ELASTICSEARCH,
        "elasticsearch+https://example.test"), transport=transport)
    batches = tuple(connector.stream(
        DocumentMapping("notes", "id", "text"), batch_size=256))
    assert batches == (({"_id": "internal-a", "id": "a", "text": "one"},),)
    assert connector.last_checkpoint == {"search_after": ["a"]}
    with pytest.raises(ValueError, match="stable sortable ID"):
        tuple(connector.stream(DocumentMapping("notes", "_id", "text")))
    with pytest.raises(RuntimeError, match="explicit"):
        connector.create_managed_namespace()
    managed = ElasticsearchConnector(ConnectorConfig(
        "elastic", ConnectorKind.ELASTICSEARCH,
        "elasticsearch+https://example.test", {"managed_write": True}),
        transport=transport)
    with pytest.raises(ValueError):
        managed.create_managed_namespace("existing-index")
    assert managed.create_managed_namespace() == "proofray-memory"
    assert transport.calls[-1][0] == "PUT"


@pytest.mark.parametrize(("connector_class", "kind", "endpoint"), [
    (MongoDBConnector, ConnectorKind.MONGODB, "mongodb://localhost/database"),
    (RedisConnector, ConnectorKind.REDIS, "redis://localhost/0"),
    (DynamoDBConnector, ConnectorKind.DYNAMODB, "dynamodb://us-east-1"),
])
def test_managed_namespace_is_fail_closed_before_opening_network(
        connector_class, kind, endpoint):
    connector = connector_class(ConnectorConfig("source", kind, endpoint))
    with pytest.raises(RuntimeError, match="explicit"):
        connector.create_managed_namespace()


class _SpacetimeTransport:
    def __init__(self):
        self.queries = []

    def request(self, method, url, *, headers=None, body=None):
        assert method == "POST" and url.endswith("/database/module/sql")
        query = body.decode("utf-8")
        self.queries.append(query)
        if query == "SELECT * FROM events LIMIT 1":
            return [{"id": "a", "text": "one"}]
        if query == "SELECT * FROM events ORDER BY id LIMIT 256 OFFSET 0":
            return [{"id": "a", "text": "one"}]
        raise AssertionError(query)


def test_spacetimedb_is_read_only_and_pages_in_stable_id_order():
    transport = _SpacetimeTransport()
    connector = SpacetimeDBConnector(ConnectorConfig(
        "space", ConnectorKind.SPACETIMEDB,
        "spacetimedb+https://example.test/module",
        {"tables": ["events"]}), transport=transport)
    batches = tuple(connector.stream(
        DocumentMapping("events", "id", "text"), batch_size=256))
    assert batches == (({"id": "a", "text": "one"},),)
    assert connector.last_checkpoint == {"offset": 1}
    assert any("ORDER BY id" in query for query in transport.queries)
    with pytest.raises(RuntimeError, match="unsupported"):
        connector.create_managed_namespace()
