from proofray_app.connectors import ConnectorConfig, ConnectorKind, DocumentMapping
from proofray_app.connectors.host_database import HostDatabaseConnector


class _Host:
    def __init__(self):
        self.calls = []

    def __call__(self, method, payload):
        self.calls.append((method, payload))
        if method.endswith(".test"):
            return {"reachable": True}
        if method.endswith(".discover"):
            return {"namespaces": [{
                "identity": "notes", "display_name": "notes",
                "fields": ["id", "text"], "primary_keys": ["id"],
                "estimated_rows": 2,
            }]}
        if method.endswith(".sample"):
            return {"rows": [{"id": 1, "text": "one"}]}
        if method.endswith(".page"):
            offset = payload["checkpoint"]["offset"]
            rows = ([{"id": 1, "text": "one"}, {"id": 2, "text": "two"}]
                    if offset == 0 else [])
            return {
                "rows": rows,
                "checkpoint": {"offset": offset + len(rows)},
                "complete": True,
            }
        if method.endswith(".managed_create"):
            return {"namespace": "proofray_memory"}
        raise AssertionError(method)


def test_sqlite_and_duckdb_are_physically_delegated_to_host():
    for kind in (ConnectorKind.SQLITE, ConnectorKind.DUCKDB):
        host = _Host()
        connector = HostDatabaseConnector(
            ConnectorConfig("c", kind, f"{kind.value}:///tmp/source.db"), host)
        connector.test_connection()
        assert connector.discover()[0].identity == "notes"
        assert connector.sample("notes").rows[0]["text"] == "one"
        batches = tuple(connector.stream(DocumentMapping("notes", "id", "text")))
        assert len(batches) == 1 and len(batches[0]) == 2
        assert connector.last_checkpoint == {"offset": 2, "complete": True}
        assert all(call[0].startswith(f"connector.{kind.value}.") for call in host.calls)
