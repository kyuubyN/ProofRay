from __future__ import annotations

import re
from typing import Iterator, Mapping
from urllib.parse import quote, urlparse, urlunparse

from .base import (
    CAPABILITIES, ConnectorCapabilities, ConnectorConfig, ConnectorKind,
    ConnectorNamespace, DocumentMapping, SchemaSample,
)
from .http_json import SafeJsonTransport, authorization_headers


def _base_url(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    scheme = parsed.scheme.split("+")[-1]
    return urlunparse(parsed._replace(scheme=scheme)).rstrip("/")


_INDEX_NAME = re.compile(r"[a-z0-9.][a-z0-9._-]{0,254}")


def _index_name(value: str) -> str:
    if (not isinstance(value, str) or value in (".", "..")
            or not _INDEX_NAME.fullmatch(value)):
        raise ValueError("unsafe Elasticsearch index name")
    return value


class ElasticsearchConnector:
    kind = ConnectorKind.ELASTICSEARCH
    capabilities: ConnectorCapabilities = CAPABILITIES[kind]

    def __init__(self, config: ConnectorConfig, *, transport: SafeJsonTransport | None = None):
        if config.kind != self.kind:
            raise ValueError("connector kind differs from implementation")
        self.config = config
        self.transport = transport or SafeJsonTransport()
        self.last_checkpoint: dict[str, object] = {}
        self._headers = authorization_headers(
            config.secret, str(config.options.get("auth_kind", "bearer")))

    @property
    def base_url(self) -> str:
        return _base_url(self.config.endpoint)

    def test_connection(self) -> None:
        value = self.transport.request("GET", self.base_url, headers=self._headers)
        if not isinstance(value, dict) or "version" not in value:
            raise RuntimeError("Elasticsearch identity probe failed")

    def discover(self) -> tuple[ConnectorNamespace, ...]:
        indices = self.transport.request(
            "GET", f"{self.base_url}/_cat/indices?format=json&h=index,docs.count",
            headers=self._headers)
        if not isinstance(indices, list):
            raise RuntimeError("Elasticsearch index discovery failed")
        result = []
        for item in indices:
            if not isinstance(item, dict) or not item.get("index"):
                continue
            try:
                name = _index_name(str(item["index"]))
            except ValueError:
                continue
            mapping = self.transport.request(
                "GET", f"{self.base_url}/{quote(name, safe='')}/_mapping",
                headers=self._headers)
            properties = {}
            if isinstance(mapping, dict):
                properties = mapping.get(name, {}).get("mappings", {}).get("properties", {})
            fields = ("_id",) + tuple(sorted(str(key) for key in properties))
            try:
                count = int(item.get("docs.count", 0))
            except (TypeError, ValueError):
                count = None
            result.append(ConnectorNamespace(name, name, fields, ("_id",), count))
        return tuple(sorted(result, key=lambda item: item.identity))

    def _hits(self, namespace: str, body: dict[str, object], *, include_sort: bool = False) \
            -> tuple[dict[str, object], ...]:
        _index_name(namespace)
        if namespace not in {item.identity for item in self.discover()}:
            raise ValueError("unknown Elasticsearch index")
        value = self.transport.request(
            "POST", f"{self.base_url}/{quote(namespace, safe='')}/_search",
            headers={"Content-Type": "application/json", **self._headers}, body=body)
        try:
            hits = value["hits"]["hits"]
        except (TypeError, KeyError):
            raise RuntimeError("Elasticsearch search response is invalid") from None
        result = []
        for hit in hits:
            if not isinstance(hit, Mapping) or not isinstance(hit.get("_id"), str) \
                    or not isinstance(hit.get("_source", {}), Mapping):
                raise RuntimeError("Elasticsearch search hit is invalid")
            row = {"_id": hit["_id"], **hit.get("_source", {})}
            if include_sort:
                sort = hit.get("sort")
                if not isinstance(sort, list) or not sort:
                    raise RuntimeError("Elasticsearch search hit lacks a stable cursor")
                row["_proofray_sort"] = sort
            result.append(row)
        return tuple(result)

    def sample(self, namespace: str, *, limit: int = 50) -> SchemaSample:
        if not 1 <= limit <= 50:
            raise ValueError("sample limit must be in [1,50]")
        known = {item.identity: item for item in self.discover()}
        descriptor = known.get(namespace)
        if descriptor is None:
            raise ValueError("unknown Elasticsearch index")
        rows = self._hits(namespace, {"size": limit, "query": {"match_all": {}}})
        return SchemaSample(descriptor, rows)

    def stream(self, mapping: DocumentMapping, *, batch_size: int = 256,
               checkpoint: Mapping[str, object] | None = None) \
            -> Iterator[tuple[Mapping[str, object], ...]]:
        if not 1 <= batch_size <= 2048:
            raise ValueError("sync batch size must be in [1,2048]")
        if mapping.id_field == "_id":
            raise ValueError(
                "Elasticsearch incremental sync requires a stable sortable ID field")
        search_after = None if checkpoint is None else checkpoint.get("search_after")
        if search_after is not None and (
                not isinstance(search_after, list) or not search_after):
            raise ValueError("invalid Elasticsearch checkpoint")
        while True:
            body = {
                "size": batch_size,
                "query": {"match_all": {}},
                "sort": [{mapping.id_field: "asc"}],
            }
            if search_after is not None:
                body["search_after"] = search_after
            raw_rows = self._hits(mapping.namespace, body, include_sort=True)
            rows = tuple({key: value for key, value in row.items()
                          if key != "_proofray_sort"} for row in raw_rows)
            if not rows:
                break
            next_search_after = raw_rows[-1]["_proofray_sort"]
            if next_search_after == search_after:
                raise RuntimeError("Elasticsearch pagination made no progress")
            search_after = next_search_after
            self.last_checkpoint = {"search_after": search_after}
            yield rows
            if len(rows) < batch_size:
                break

    def create_managed_namespace(self, name: str = "proofray-memory") -> str:
        if not bool(self.config.options.get("managed_write", False)):
            raise RuntimeError("managed namespace requires explicit write authorization")
        if name != "proofray-memory":
            raise ValueError("Elasticsearch managed index is fixed")
        self.transport.request(
            "PUT", f"{self.base_url}/{quote(name, safe='')}",
            headers={"Content-Type": "application/json", **self._headers},
            body={"mappings": {"properties": {
                "id": {"type": "keyword"}, "text": {"type": "text"},
                "source": {"type": "keyword"}, "session_id": {"type": "keyword"},
                "sequence": {"type": "long"}, "event_time": {"type": "long"},
                "role": {"type": "keyword"}, "speaker": {"type": "keyword"},
                "version": {"type": "long"}, "sha256": {"type": "keyword"},
            }}},
        )
        return name

    def close(self) -> None:
        return None


__all__ = ["ElasticsearchConnector"]
