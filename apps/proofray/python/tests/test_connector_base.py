from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from proofray_app.connectors.base import (
    ConnectorConfig, ConnectorKind, DocumentMapping, detect_connector_kind,
    map_record, require_safe_identifier, stable_connector_fact_id,
)


@pytest.mark.parametrize(("value", "kind"), [
    ("memory.sqlite", ConnectorKind.SQLITE),
    ("file.duckdb", ConnectorKind.DUCKDB),
    ("mongodb://localhost/db", ConnectorKind.MONGODB),
    ("mongodb+srv://cluster/db", ConnectorKind.MONGODB),
    ("postgresql://localhost/db", ConnectorKind.POSTGRESQL),
    ("mysql://localhost/db", ConnectorKind.MYSQL),
    ("rediss://localhost/0", ConnectorKind.REDIS),
    ("dynamodb://us-east-1/table", ConnectorKind.DYNAMODB),
    ("elasticsearch+https://localhost:9200", ConnectorKind.ELASTICSEARCH),
    ("spacetimedb+http://localhost:3000/module", ConnectorKind.SPACETIMEDB),
    ("https://localhost:9200", None),
])
def test_detection_is_scheme_based_and_http_fails_ambiguous(value, kind):
    assert detect_connector_kind(value) == kind


def test_identifier_validation_prevents_query_injection():
    assert require_safe_identifier("proofray_memory") == "proofray_memory"
    for value in ("articles; DROP TABLE users", "a.b", "", "two words"):
        with pytest.raises(ValueError):
            require_safe_identifier(value)


def test_connector_identity_cannot_ambiguous_source_prefixes():
    with pytest.raises(ValueError):
        ConnectorConfig(
            "connector:ambiguous", ConnectorKind.MONGODB,
            "mongodb://localhost/database",
        )
    with pytest.raises(ValueError, match="scheme differs"):
        ConnectorConfig(
            "source", ConnectorKind.MONGODB,
            "redis://localhost/0",
        )


def test_plain_http_requires_explicit_kind_but_is_accepted_after_confirmation():
    assert detect_connector_kind("https://search.example.test") is None
    ConnectorConfig(
        "search", ConnectorKind.ELASTICSEARCH,
        "https://search.example.test",
    )
    with pytest.raises(ValueError, match="unsupported URL components"):
        ConnectorConfig(
            "search", ConnectorKind.ELASTICSEARCH,
            "https://search.example.test?pretty=true",
        )
    ConnectorConfig(
        "mongo", ConnectorKind.MONGODB,
        "mongodb://localhost/app?retryWrites=true",
    )


def test_remote_redis_requires_tls_but_loopback_can_be_plaintext():
    with pytest.raises(ValueError, match="rediss"):
        ConnectorConfig("r", ConnectorKind.REDIS, "redis://cache.example.test/0")
    ConnectorConfig("local", ConnectorKind.REDIS, "redis://127.0.0.1/0")
    ConnectorConfig("secure", ConnectorKind.REDIS, "rediss://cache.example.test/0")


def test_mapping_preserves_authority_coordinates_and_digest():
    config = ConnectorConfig("mongo-1", ConnectorKind.MONGODB, "mongodb://localhost")
    mapping = DocumentMapping(
        "chat.messages", "_id", "body", source_field="source",
        session_field="thread", sequence_field="turn", event_time_field="created_at",
        role_field="role", speaker_field="speaker", version_field="version")
    row = {
        "_id": "abc", "body": "Minha bicicleta é azul cobalto.",
        "source": "chat:abc", "thread": "summer", "turn": 7,
        "created_at": "2026-08-25T14:30:00-03:00", "role": "user",
        "speaker": "Kaue", "version": 2,
    }
    document = map_record(config, mapping, row)
    assert document.fact_id == stable_connector_fact_id("mongo-1", "chat.messages", "abc")
    assert document.source == "connector:mongo-1:chat:abc"
    assert document.session_id == "summer"
    assert document.sequence == 7
    assert document.event_time == date(2026, 8, 25).toordinal()
    assert document.role == "user"
    assert document.speaker == "Kaue"
    assert document.version == 2
    assert len(document.content_sha256) == 64


def test_fact_identity_type_tags_primary_keys_before_hashing():
    values = (
        1, "1", True, 1.0, Decimal("1"), b"1",
        date(2026, 8, 25), datetime(2026, 8, 25, tzinfo=timezone.utc),
    )
    fact_ids = {
        stable_connector_fact_id("c", "notes", value)
        for value in values
    }
    assert len(fact_ids) == len(values)
    with pytest.raises(ValueError, match="finite"):
        stable_connector_fact_id("c", "notes", float("nan"))
    with pytest.raises(ValueError, match="deterministic database scalar"):
        stable_connector_fact_id("c", "notes", {"not": "a scalar"})


def test_mapping_preserves_exact_source_whitespace():
    config = ConnectorConfig("c", ConnectorKind.SQLITE, "memory.sqlite")
    document = map_record(
        config,
        DocumentMapping("notes", "id", "text"),
        {"id": 1, "text": "  exact source span\n"},
    )
    assert document.text == "  exact source span\n"


def test_mapping_never_stringifies_non_text_values_into_authority():
    config = ConnectorConfig("c", ConnectorKind.SQLITE, "memory.sqlite")
    with pytest.raises(ValueError, match="exact source text"):
        map_record(
            config,
            DocumentMapping("notes", "id", "text"),
            {"id": 1, "text": {"fabricated": "representation"}},
        )


def test_typed_transport_scalars_preserve_preview_and_sync_coordinates():
    config = ConnectorConfig("c", ConnectorKind.SQLITE, "memory.sqlite")
    document = map_record(
        config,
        DocumentMapping(
            "notes", "id", "text", sequence_field="sequence",
            event_time_field="event_time",
        ),
        {
            "id": {
                "__proofray_transport_scalar_v1__": "decimal",
                "value": "9007199254740993",
            },
            "text": "exact",
            "sequence": {
                "__proofray_transport_scalar_v1__": "decimal",
                "value": "7",
            },
            "event_time": {
                "__proofray_transport_scalar_v1__": "datetime",
                "value": "2026-08-25T10:00:00-03:00",
            },
        },
    )
    assert document.source_primary_key == "9007199254740993"
    assert document.sequence == 7
    assert document.event_time == date(2026, 8, 25).toordinal()


def test_external_source_cannot_collide_with_conversation_authority():
    config = ConnectorConfig("external", ConnectorKind.MONGODB, "mongodb://localhost/db")
    mapping = DocumentMapping("db.notes", "id", "text", source_field="source")
    document = map_record(config, mapping, {
        "id": "1", "text": "fact", "source": "conversation:thread:message"})
    assert document.source == \
        "connector:external:conversation:thread:message"


@pytest.mark.parametrize("value", [
    date(2026, 8, 25), datetime(2026, 8, 25, 10, tzinfo=timezone.utc),
    "2026-08-25", "2026-08-25T10:00:00Z", 1787666400,
])
def test_supported_clocks_map_to_the_same_observed_day(value):
    config = ConnectorConfig("c", ConnectorKind.SQLITE, "memory.sqlite")
    mapping = DocumentMapping("events", "id", "text", event_time_field="clock")
    result = map_record(config, mapping, {"id": 1, "text": "event", "clock": value})
    assert result.event_time == date(2026, 8, 25).toordinal()
