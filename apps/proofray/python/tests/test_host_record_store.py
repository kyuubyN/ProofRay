import base64
import hashlib

import pytest

from proofray_app.host_record_store import HostAuthorizedSidecarRecordStore


class _Host:
    def __init__(self, records=()):
        self.records = list(records)
        self.calls = []
        self.reject = False
        self.plans = {}

    def __call__(self, method, payload):
        self.calls.append((method, payload))
        if method == "sidecar.load":
            after = payload["after_sequence"]
            limit = payload["limit"]
            page = self.records[after:after + limit]
            return {
                "records": [base64.b64encode(item).decode("ascii") for item in page],
                "complete": after + len(page) >= len(self.records),
            }
        if method == "sidecar.replace_begin":
            self.plans[payload["transaction_id"]] = {
                **payload, "chunks": {},
            }
            return {"staged": True}
        if method == "sidecar.replace_chunk":
            self.plans[payload["transaction_id"]]["chunks"][payload["chunk_index"]] = \
                payload["records"]
            return {"staged": True}
        if method == "sidecar.replace_commit":
            plan = self.plans[payload["transaction_id"]]
            flattened = [item for index in sorted(plan["chunks"])
                         for item in plan["chunks"][index]]
            assert len(flattened) == plan["total_records"]
            self._replace(plan["common_prefix"], plan["common_prefix_sha256"], flattened)
            return {"committed": True}
        assert method == "sidecar.replace_suffix"
        if self.reject:
            return {"committed": False}
        self._replace(
            payload["common_prefix"], payload["common_prefix_sha256"],
            payload["records"])
        return {"committed": True}

    def _replace(self, prefix, prefix_digest, records):
        if prefix:
            assert prefix_digest == hashlib.sha256(
                self.records[prefix - 1]).hexdigest()
        self.records[prefix:] = [base64.b64decode(item) for item in records]


def test_host_store_pages_load_and_appends_only_changed_suffix():
    host = _Host((b"one", b"two", b"three"))
    store = HostAuthorizedSidecarRecordStore("personal", host, page_size=2)
    assert store.load() == (b"one", b"two", b"three")
    store.replace((b"one", b"two", b"three", b"four"))
    method, payload = host.calls[-1]
    assert method == "sidecar.replace_suffix"
    assert payload["common_prefix"] == 3
    assert [base64.b64decode(item) for item in payload["records"]] == [b"four"]


def test_host_store_does_not_publish_rejected_replacement():
    host = _Host((b"one",))
    store = HostAuthorizedSidecarRecordStore("personal", host)
    assert store.load() == (b"one",)
    host.reject = True
    with pytest.raises(OSError):
        store.replace((b"one", b"two"))
    assert store.load() == (b"one",)


def test_large_rechain_is_chunked_and_committed_once():
    host = _Host((b"old",))
    store = HostAuthorizedSidecarRecordStore("personal", host)
    store.load()
    replacement = tuple(bytes([index % 251]) * 220_000 for index in range(5))
    store.replace(replacement)
    methods = [method for method, _payload in host.calls]
    assert methods.count("sidecar.replace_begin") == 1
    assert methods.count("sidecar.replace_chunk") >= 2
    assert methods.count("sidecar.replace_commit") == 1
    assert "sidecar.replace_suffix" not in methods
    assert tuple(host.records) == replacement
    assert store.load() == replacement
