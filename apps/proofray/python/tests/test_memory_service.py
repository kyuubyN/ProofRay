from datetime import datetime, timezone
import hashlib

import pytest

from proofray_app.memory_service import (
    ConversationMemoryService, MemoryReply, is_authoritative_observation, should_consult_memory,
    stable_fact_id, _fit_utf8_prefix,
)


def test_stable_fact_id_is_deterministic_and_inside_public_domain():
    source = "conversation:thread-1:message-1"
    assert stable_fact_id(source) == stable_fact_id(source)
    assert 0 <= stable_fact_id(source) < (1 << 62)
    assert stable_fact_id(source) != stable_fact_id(source + "x")


def test_connector_fact_id_collision_cannot_be_relabelled_as_a_new_version():
    from proofray_app.connectors.base import MappedDocument

    service = ConversationMemoryService()
    first = MappedDocument(
        fact_id=7, text="first", scope_id=1, session_id="session",
        version=1, source="connector:a:notes:key-1", sequence=1,
        event_time=None, role=None, speaker=None, source_primary_key="key-1",
        content_sha256=hashlib.sha256(b"first").hexdigest())
    collision = MappedDocument(
        fact_id=7, text="collision", scope_id=1, session_id="session",
        version=2, source="connector:a:notes:key-2", sequence=2,
        event_time=None, role=None, speaker=None, source_primary_key="key-2",
        content_sha256=hashlib.sha256(b"collision").hexdigest())
    service.ingest_mapped_documents((first,), "batch-1")
    with pytest.raises(RuntimeError, match="fact_id_collision"):
        service.ingest_mapped_documents((collision,), "batch-2")


@pytest.mark.parametrize(("mode", "text", "tool", "expected"), [
    ("off", "lembra da viagem?", False, False),
    ("forceNext", "qualquer pergunta", False, True),
    ("tool", "lembra da viagem?", False, False),
    ("tool", "qualquer pergunta", True, True),
    ("keywords", "Você lembra da viagem?", False, True),
    ("keywords", "Como vai você?", False, False),
])
def test_activation_is_explicit(mode, text, tool, expected):
    assert should_consult_memory(mode, text, tool_requested=tool) is expected


def test_keyword_activation_uses_unicode_word_boundaries_not_substrings():
    assert should_consult_memory(
        "keywords", "Please remember this", keywords=frozenset({"remember"}))
    assert not should_consult_memory(
        "keywords", "This is a remembering exercise", keywords=frozenset({"remember"}))
    assert not should_consult_memory(
        "keywords", "membership", keywords=frozenset({"mem"}))


@pytest.mark.parametrize(("text", "expected"), [
    ("Minha bicicleta é azul cobalto.", True),
    ("I bought a cobalt bicycle yesterday.", True),
    ("Você lembra qual é a cor da minha bicicleta?", False),
    ("What color is my bicycle?", False),
    ("Remember that my bicycle is blue.", False),
    ("Lembre que minha bicicleta é azul.", False),
    ("Por favor encontre minhas anotações.", False),
    ("", False),
])
def test_only_declarative_user_turns_become_authority(text, expected):
    assert is_authoritative_observation(text) is expected


def test_chat_and_import_text_limits_prevent_oversized_host_frames():
    service = ConversationMemoryService()
    with pytest.raises(ValueError):
        service.remember_user_message(
            conversation_id="thread", message_id="large",
            text="á" * (64 * 1024),
            timestamp=datetime(2026, 8, 25, tzinfo=timezone.utc))
    with pytest.raises(ValueError):
        service.import_local_chunk(
            file_name="large.txt", file_sha256="a" * 64,
            byte_start=0, byte_end=128 * 1024 + 1,
            text="x" * (128 * 1024 + 1))


def test_question_is_not_allowed_to_answer_itself_and_marker_is_truthful():
    service = ConversationMemoryService(profile_name="Kaue", timezone_name="America/Sao_Paulo")
    first = service.answer_and_remember(
        conversation_id="thread-1", message_id="m1",
        text="Você lembra qual é a cor da minha bicicleta?", mode="forceNext",
        timestamp=datetime(2026, 8, 25, 12, tzinfo=timezone.utc),
    )
    assert first.authority == "abstention"
    assert first.memory_consulted is True

    silent = service.answer_and_remember(
        conversation_id="thread-1", message_id="m2",
        text="Minha bicicleta é azul cobalto.", mode="off",
        timestamp=datetime(2026, 8, 25, 13, tzinfo=timezone.utc),
    )
    assert silent.authority == "model"
    assert silent.memory_consulted is False

    recalled = service.answer_and_remember(
        conversation_id="thread-1", message_id="m3",
        text="Você lembra qual é a cor da minha bicicleta?", mode="forceNext",
        timestamp=datetime(2026, 8, 25, 14, tzinfo=timezone.utc),
    )
    assert recalled.memory_consulted is True
    assert recalled.authority in {"proved", "evidence"}
    assert "cobalto" in recalled.text.casefold()
    assert recalled.sources[0]["text"] == "Minha bicicleta é azul cobalto."


def test_evidence_fallback_uses_selected_exact_source_not_every_document():
    service = ConversationMemoryService(profile_name="Kaue")
    service.remember_user_message(
        conversation_id="thread", message_id="m1",
        text="Minha bicicleta é azul cobalto.",
        timestamp=datetime(2026, 8, 25, 12, tzinfo=timezone.utc),
    )
    service.remember_user_message(
        conversation_id="thread", message_id="m2",
        text="Meu jantar favorito é sopa de abóbora.",
        timestamp=datetime(2026, 8, 25, 13, tzinfo=timezone.utc),
    )

    reply = service.answer_prior(
        "thread", "Você lembra qual é a cor da minha bicicleta?")

    assert reply.authority == "evidence"
    assert "azul cobalto" in reply.text.casefold()
    assert "sopa" not in reply.text.casefold()
    assert tuple(item["text"] for item in reply.sources) == (
        "Minha bicicleta é azul cobalto.",)


def test_personal_field_spans_conversations_without_flattening_sessions():
    service = ConversationMemoryService(profile_name="Kaue")
    service.remember_user_message(
        conversation_id="thread-1", message_id="m1",
        text="I bought a cobalt bicycle.",
        timestamp=datetime(2026, 8, 25, 12, tzinfo=timezone.utc),
    )
    service.remember_user_message(
        conversation_id="thread-2", message_id="m2",
        text="I repaired the bicycle yesterday.",
        timestamp=datetime(2026, 8, 26, 12, tzinfo=timezone.utc),
    )
    assert service._field is not None
    assert tuple(document.session_id for document in service._field._documents) == (
        "thread-1", "thread-2")


def test_local_import_preserves_file_digest_and_byte_span_in_source_identity():
    service = ConversationMemoryService()
    digest = "a" * 64
    result = service.import_local_chunk(
        file_name="notes.md", file_sha256=digest,
        byte_start=4, byte_end=15, text="hello world")
    assert result["state"] == "APPLIED"
    assert result["source_id"] == f"file:{digest}:4:15"
    assert result["byte_span"] == [4, 15]
    with pytest.raises(ValueError, match="invalid local import"):
        service.import_local_chunk(
            file_name="notes.md", file_sha256="not-a-digest",
            byte_start=0, byte_end=5, text="hello")
    with pytest.raises(ValueError, match="invalid local import"):
        service.import_local_chunk(
            file_name="notes.md", file_sha256=digest,
            byte_start=0, byte_end=6, text="hello")


def test_confirmed_model_text_becomes_a_new_user_attested_observation():
    service = ConversationMemoryService(profile_name="Kaue")
    result = service.confirm_user_observation(
        conversation_id="thread", message_id="confirmed-1",
        text="My favorite trail is Serra Fina.",
        timestamp=datetime(2026, 8, 25, 12, tzinfo=timezone.utc), sequence=4)
    assert result["state"] == "APPLIED"
    assert service._field is not None
    document = service._field.documents_snapshot()[0]
    assert document.role == "user" and document.speaker == "Kaue"
    assert document.source == "conversation:thread:confirmed-1"


def test_profile_update_changes_only_future_observation_coordinates():
    service = ConversationMemoryService(profile_name="Old", timezone_name="UTC")
    service.remember_user_message(
        conversation_id="thread", message_id="m1", text="First fact.",
        timestamp=datetime(2026, 8, 25, 1, tzinfo=timezone.utc))
    service.update_profile(
        profile_name="New", timezone_name="America/Sao_Paulo")
    service.remember_user_message(
        conversation_id="thread", message_id="m2", text="Second fact.",
        timestamp=datetime(2026, 8, 25, 1, tzinfo=timezone.utc))
    documents = service._field.documents_snapshot()
    assert tuple(item.speaker for item in documents) == ("Old", "New")
    assert documents[0].event_time == datetime(2026, 8, 25).date().toordinal()
    assert documents[1].event_time == datetime(2026, 8, 24).date().toordinal()

    with pytest.raises(ValueError, match="IANA"):
        service.update_profile(profile_name="New", timezone_name="not-a-zone")


def test_warm_materializes_the_personal_field_before_first_query():
    service = ConversationMemoryService()
    assert service._field is None
    assert service.warm() == {"warmed": True, "documents": 0}
    field = service._field
    assert field is not None
    assert service.warm() == {"warmed": True, "documents": 0}
    assert service._field is field


def test_source_text_reopens_by_fact_and_source():
    service = ConversationMemoryService()
    service.remember_user_message(
        conversation_id="thread", message_id="large",
        text="My memory detail is cobalt.",
        timestamp=datetime(2026, 8, 25, tzinfo=timezone.utc))
    reply = service.answer_prior("thread", "Do you remember the memory detail?")
    source = reply.sources[0]
    payload_source = reply.payload()["sources"][0]
    assert payload_source["text_deferred"] is False
    reopened = service.get_source(
        fact_id=source["fact_id"], source_id=source["source_id"])
    assert reopened["text"] == source["text"]
    with pytest.raises(ValueError, match="does not match"):
        service.get_source(fact_id=source["fact_id"], source_id="wrong")


def test_reply_defers_source_text_after_the_inline_byte_budget():
    sources = tuple({
        "fact_id": index,
        "source_id": f"source:{index}",
        "text": character * (256 * 1024),
    } for index, character in enumerate(("a", "b"), 1))
    payload = MemoryReply("evidence", "related", True, sources=sources).payload()
    assert payload["sources"][0]["text_deferred"] is False
    assert payload["sources"][1]["text"] == ""
    assert payload["sources"][1]["text_deferred"] is True


def test_evidence_text_is_utf8_safe_and_never_exceeds_product_budget():
    fitted, truncated = _fit_utf8_prefix("á" * 20_000, 24_576)
    assert truncated is True
    assert len(fitted.encode("utf-8")) <= 24_576
    assert fitted.encode("utf-8").decode("utf-8") == fitted
