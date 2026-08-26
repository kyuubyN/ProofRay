from __future__ import annotations

import json
from typing import Iterator, Mapping
from urllib.parse import urlparse

from .base import (
    CAPABILITIES, ConnectorCapabilities, ConnectorConfig, ConnectorKind,
    ConnectorNamespace, DocumentMapping, SchemaSample, require_safe_identifier,
)


class DynamoDBConnector:
    kind = ConnectorKind.DYNAMODB
    capabilities: ConnectorCapabilities = CAPABILITIES[kind]

    def __init__(self, config: ConnectorConfig):
        if config.kind != self.kind:
            raise ValueError("connector kind differs from implementation")
        self.config = config
        self._resource = None
        self.last_checkpoint: dict[str, object] = {}

    def _region_table(self) -> tuple[str, str | None]:
        parsed = urlparse(self.config.endpoint)
        region = str(self.config.options.get("region") or parsed.hostname or "")
        table = str(self.config.options.get("table") or parsed.path.lstrip("/")) or None
        if not region:
            raise ValueError("DynamoDB region is required")
        return region, table

    @property
    def resource(self):
        if self._resource is None:
            import boto3
            from botocore.config import Config
            region, _ = self._region_table()
            credentials = {}
            if self.config.secret:
                try:
                    decoded = json.loads(self.config.secret)
                except json.JSONDecodeError:
                    raise ValueError("DynamoDB secret lease must be credential JSON") from None
                if not isinstance(decoded, dict):
                    raise ValueError("DynamoDB secret lease must be an object")
                allowed = {
                    "aws_access_key_id", "aws_secret_access_key", "aws_session_token",
                }
                if set(decoded) - allowed:
                    raise ValueError("DynamoDB secret lease contains unknown fields")
                credentials = decoded
            self._resource = boto3.resource(
                "dynamodb", region_name=region,
                config=Config(
                    connect_timeout=10,
                    read_timeout=30,
                    retries={"max_attempts": 3, "mode": "standard"},
                ),
                **credentials)
        return self._resource

    def test_connection(self) -> None:
        self.resource.meta.client.list_tables(Limit=1)

    def discover(self) -> tuple[ConnectorNamespace, ...]:
        client = self.resource.meta.client
        names = []
        start = None
        while True:
            request = {"Limit": 100}
            if start:
                request["ExclusiveStartTableName"] = start
            response = client.list_tables(**request)
            names.extend(response.get("TableNames", ()))
            start = response.get("LastEvaluatedTableName")
            if not start:
                break
        result = []
        for name in sorted(names):
            description = client.describe_table(TableName=name)["Table"]
            keys = tuple(item["AttributeName"] for item in description.get("KeySchema", ()))
            sample = self.resource.Table(name).scan(Limit=50).get("Items", ())
            fields = tuple(sorted({str(key) for item in sample for key in item} | set(keys)))
            result.append(ConnectorNamespace(
                name, name, fields or keys or ("id",), keys,
                int(description.get("ItemCount", 0)),
            ))
        return tuple(result)

    def sample(self, namespace: str, *, limit: int = 50) -> SchemaSample:
        if not 1 <= limit <= 50:
            raise ValueError("sample limit must be in [1,50]")
        known = {item.identity: item for item in self.discover()}
        descriptor = known.get(namespace)
        if descriptor is None:
            raise ValueError("unknown DynamoDB table")
        rows = tuple(self.resource.Table(namespace).scan(Limit=limit).get("Items", ()))
        return SchemaSample(descriptor, rows)

    def stream(self, mapping: DocumentMapping, *, batch_size: int = 256,
               checkpoint: Mapping[str, object] | None = None) \
            -> Iterator[tuple[Mapping[str, object], ...]]:
        if not 1 <= batch_size <= 2048:
            raise ValueError("sync batch size must be in [1,2048]")
        if checkpoint and checkpoint.get("complete") is True:
            return
        if mapping.namespace not in {item.identity for item in self.discover()}:
            raise ValueError("unknown DynamoDB table")
        table = self.resource.Table(mapping.namespace)
        request = {"Limit": batch_size}
        if checkpoint and checkpoint.get("last_evaluated_key"):
            request["ExclusiveStartKey"] = self._decode_key(
                checkpoint["last_evaluated_key"])
        prior_key = request.get("ExclusiveStartKey")
        while True:
            response = table.scan(**request)
            rows = tuple(response.get("Items", ()))
            last = response.get("LastEvaluatedKey")
            if last and last == prior_key:
                raise RuntimeError("DynamoDB pagination made no progress")
            self.last_checkpoint = (
                {"last_evaluated_key": self._encode_key(last), "complete": False}
                if last else {"complete": True})
            if rows:
                yield rows
            if not last:
                break
            request["ExclusiveStartKey"] = last
            prior_key = last

    @staticmethod
    def _encode_key(value: object) -> dict[str, object]:
        if not isinstance(value, Mapping) or not value:
            raise ValueError("invalid DynamoDB continuation key")
        from boto3.dynamodb.types import TypeSerializer
        serializer = TypeSerializer()
        return {str(key): serializer.serialize(item) for key, item in value.items()}

    @staticmethod
    def _decode_key(value: object) -> dict[str, object]:
        if not isinstance(value, Mapping) or not value:
            raise ValueError("invalid DynamoDB checkpoint")
        from boto3.dynamodb.types import TypeDeserializer
        deserializer = TypeDeserializer()
        return {str(key): deserializer.deserialize(item)
                for key, item in value.items()}

    def create_managed_namespace(self, name: str = "proofray_memory") -> str:
        if not bool(self.config.options.get("managed_write", False)):
            raise RuntimeError("managed namespace requires explicit write authorization")
        if name != "proofray_memory":
            raise ValueError("DynamoDB managed namespace is fixed")
        table_name = require_safe_identifier(name)
        if table_name in {item.identity for item in self.discover()}:
            return table_name
        table = self.resource.create_table(
            TableName=table_name,
            KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        return table_name

    def close(self) -> None:
        self._resource = None


__all__ = ["DynamoDBConnector"]
