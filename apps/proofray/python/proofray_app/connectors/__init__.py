"""Database connector boundary for the native ProofRay application."""

from .base import (
    ConnectorCapabilities, ConnectorConfig, ConnectorKind, ConnectorNamespace,
    DocumentMapping, MappedDocument, SchemaSample, detect_connector_kind,
    map_record, stable_connector_fact_id,
)
from .registry import create_connector

__all__ = [
    "ConnectorCapabilities", "ConnectorConfig", "ConnectorKind",
    "ConnectorNamespace", "DocumentMapping", "MappedDocument", "SchemaSample",
    "detect_connector_kind", "map_record", "stable_connector_fact_id",
    "create_connector",
]
