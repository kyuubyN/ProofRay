from __future__ import annotations

from .base import Connector, ConnectorConfig, ConnectorKind


def create_connector(config: ConnectorConfig) -> Connector:
    """Import only the selected connector and its optional dependencies."""
    if config.kind == ConnectorKind.SQLITE:
        from .relational import SQLiteConnector
        return SQLiteConnector(config)
    if config.kind == ConnectorKind.DUCKDB:
        from .relational import DuckDBConnector
        return DuckDBConnector(config)
    if config.kind == ConnectorKind.POSTGRESQL:
        from .relational import PostgreSQLConnector
        return PostgreSQLConnector(config)
    if config.kind == ConnectorKind.MYSQL:
        from .relational import MySQLConnector
        return MySQLConnector(config)
    if config.kind == ConnectorKind.MONGODB:
        from .mongodb import MongoDBConnector
        return MongoDBConnector(config)
    if config.kind == ConnectorKind.REDIS:
        from .redis_connector import RedisConnector
        return RedisConnector(config)
    if config.kind == ConnectorKind.DYNAMODB:
        from .dynamodb import DynamoDBConnector
        return DynamoDBConnector(config)
    if config.kind == ConnectorKind.ELASTICSEARCH:
        from .elasticsearch import ElasticsearchConnector
        return ElasticsearchConnector(config)
    if config.kind == ConnectorKind.SPACETIMEDB:
        from .spacetimedb import SpacetimeDBConnector
        return SpacetimeDBConnector(config)
    raise ValueError("unsupported connector kind")


__all__ = ["create_connector"]
