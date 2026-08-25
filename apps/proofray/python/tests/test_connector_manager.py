from proofray_app.connector_manager import ConnectorManager
from proofray_app.connectors import (
    ConnectorCapabilities, ConnectorConfig, ConnectorKind, ConnectorNamespace,
    DocumentMapping, SchemaSample,
)


class _Connector:
    kind = ConnectorKind.SQLITE
    capabilities = ConnectorCapabilities()

    def __init__(self, config):
        self.config = config
        self.closed = False
        self.last_checkpoint = {}

    def test_connection(self):
        return None

    def discover(self):
        return (ConnectorNamespace("notes", "notes", ("id", "text"), ("id",), 2),)

    def sample(self, namespace, *, limit=50):
        return SchemaSample(self.discover()[0], ({"id": 1, "text": "one"},))

    def stream(self, mapping, *, batch_size=256, checkpoint=None):
        assert batch_size == 256
        self.last_checkpoint = {"offset": 2}
        yield ({"id": 1, "text": "one"}, {"id": 2, "text": "two"})

    def create_managed_namespace(self, name="proofray_memory"):
        return name

    def close(self):
        self.closed = True


def test_detection_is_local_and_http_is_ambiguous():
    assert ConnectorManager.detect("mongodb://localhost/db")["kind"] == "mongodb"
    assert ConnectorManager.detect("https://example.test")["requires_confirmation"] is True


def test_duckdb_never_silently_ignores_a_credential_lease():
    manager = ConnectorManager(factory=_Connector)
    manager.configure(ConnectorConfig(
        "duck", ConnectorKind.DUCKDB, "memory.duckdb"))
    try:
        manager.test_connection("duck", secret="unused-secret")
    except ValueError as error:
        assert "does not accept" in str(error)
    else:
        raise AssertionError("DuckDB silently ignored a credential")


def test_manager_does_not_retain_secret_and_syncs_in_256_batches():
    manager = ConnectorManager(factory=_Connector)
    manager.configure(ConnectorConfig(
        "c", ConnectorKind.SQLITE, "sqlite:///tmp/notes.db", secret="lease"))
    assert manager._configs["c"].secret is None
    namespaces = manager.discover("c", secret="new-lease")
    assert namespaces[0]["identity"] == "notes"
    suggestion = manager.suggest_mapping(namespaces[0])
    assert suggestion["id_field"] == "id" and suggestion["text_field"] == "text"
    mapping = DocumentMapping("notes", "id", "text")
    assert len(manager.preview("c", mapping, secret="call-only")) == 1
    batches = []
    result = manager.sync(
        "c", mapping,
        secret="call-only", ingest_batch=lambda rows, identity: batches.append((rows, identity)))
    assert result["documents_committed"] == 2
    assert result["checkpoint"] == {"offset": 2}
    assert len(batches) == 1 and len(batches[0][0]) == 2
    assert batches[0][1].startswith("connector:c:")
    assert len(batches[0][1].split(":")) == 3


def test_resumed_batches_never_reuse_identity_for_different_documents():
    class ResumedConnector(_Connector):
        def stream(self, mapping, *, batch_size=256, checkpoint=None):
            offset = int((checkpoint or {}).get("offset", 0))
            row = {"id": offset + 1, "text": f"row-{offset + 1}"}
            self.last_checkpoint = {"offset": offset + 1}
            yield (row,)

    manager = ConnectorManager(factory=ResumedConnector)
    manager.configure(ConnectorConfig(
        "source", ConnectorKind.SQLITE, "sqlite:///tmp/source.db"))
    mapping = DocumentMapping("notes", "id", "text")
    manager.preview("source", mapping)
    identities = []
    manager.sync(
        "source", mapping,
        ingest_batch=lambda _rows, identity: identities.append(identity),
    )
    manager.sync(
        "source", mapping, checkpoint={"offset": 1},
        ingest_batch=lambda _rows, identity: identities.append(identity),
    )
    assert identities[0] != identities[1]


def test_managed_namespace_requires_explicit_call_authority():
    created = []

    class ManagedConnector(_Connector):
        def create_managed_namespace(self, name="proofray_memory"):
            created.append(dict(self.config.options))
            return name

    manager = ConnectorManager(factory=ManagedConnector)
    manager.configure(ConnectorConfig(
        "managed", ConnectorKind.SQLITE, "/tmp/source.db",
        {"managed_write": False}))

    try:
        manager.create_managed_namespace("managed")
    except ValueError as error:
        assert "explicit authorization" in str(error)
    else:
        raise AssertionError("managed write was accepted without explicit authority")
    try:
        manager.create_managed_namespace("managed", authorized=True)
    except ValueError as error:
        assert "preview" in str(error)
    else:
        raise AssertionError("managed write was accepted before preview")
    manager.preview("managed", DocumentMapping("notes", "id", "text"))
    assert manager.create_managed_namespace(
        "managed", authorized=True) == "proofray_memory"
    assert created == [{"managed_write": True}]


def test_managed_write_grant_cannot_be_retained_in_normal_configuration():
    manager = ConnectorManager(factory=_Connector)
    try:
        manager.configure(ConnectorConfig(
            "managed", ConnectorKind.SQLITE, "/tmp/source.db",
            {"managed_write": True},
        ))
    except ValueError as error:
        assert "one-shot" in str(error)
    else:
        raise AssertionError("persistent managed-write authority was accepted")


def test_empty_terminal_page_still_commits_connector_checkpoint():
    class EmptyConnector(_Connector):
        def stream(self, mapping, *, batch_size=256, checkpoint=None):
            self.last_checkpoint = {"cursor": 0, "complete": True}
            return
            yield

    manager = ConnectorManager(factory=EmptyConnector)
    manager.configure(ConnectorConfig(
        "empty", ConnectorKind.REDIS, "redis://localhost/0"))
    manager.preview("empty", DocumentMapping("notes", "id", "text"))
    result = manager.sync(
        "empty", DocumentMapping("notes", "id", "text"),
        ingest_batch=lambda *_args: (_ for _ in ()).throw(
            AssertionError("empty connector must not commit a batch")))
    assert result["documents_committed"] == 0
    assert result["checkpoint"] == {"cursor": 0, "complete": True}


def test_sync_requires_exact_mapping_that_was_previewed():
    manager = ConnectorManager(factory=_Connector)
    manager.configure(ConnectorConfig(
        "source", ConnectorKind.SQLITE, "sqlite:///tmp/source.db"))
    previewed = DocumentMapping("notes", "id", "text")
    manager.preview("source", previewed)
    try:
        manager.sync(
            "source", DocumentMapping("notes", "id", "text", scope_id=2),
            ingest_batch=lambda *_args: None,
        )
    except ValueError as error:
        assert "exact authorized preview" in str(error)
    else:
        raise AssertionError("unpreviewed mapping was synchronized")
