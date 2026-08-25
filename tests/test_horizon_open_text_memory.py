# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import hashlib
import json
from pathlib import Path
import pytest

from horizon_memory import (
    AnswerContextIntent, CausalAdapterBatch, DeclarativeSidecarAdapter,
    DurableAuthorizedSidecarMemory, EngineProfile, MemoryAuthorizedSidecarRecordStore,
    OpenTextHorizonMemory, RouteDocument,
    SidecarFactDeclaration, SidecarLifecycle, StructuredCausalDeclaration,
)


def _doc(fid, text, source=None, sequence=None):
    return RouteDocument(fid, text, 1, "s1", 1, source or f"doc:{fid}", sequence=sequence)


def test_arbitrary_text_crosses_sidecar_before_answer_composition():
    memory = OpenTextHorizonMemory(scope_id=1)
    documents = (
        _doc(1, "The Meridian project reduced compute cost by exactly 42 percent compared "
                "with the previous baseline architecture across every workload."),
        _doc(2, "Unrelated atmospheric measurements were collected in another experiment."),
    )
    receipt = memory.ingest_documents(documents)
    result = memory.answer("What percent did Meridian reduce compute cost by?")
    assert receipt.state == "APPLIED" and memory.fact_count == 2
    assert result.resolved and "42" in result.answer_text
    assert all(line.fact_id in (1, 2) for line in result.answer_lines)


def test_cjk_uses_the_same_lossless_surface_contract():
    memory = OpenTextHorizonMemory(scope_id=1)
    memory.ingest_documents((
        _doc(1, "北京地铁在二零二三年运送了一百二十万名乘客。"),
        _doc(2, "上海今天气温二十二度，适合户外活动。"),
    ))
    result = memory.answer("北京地铁运送了多少名乘客？")
    assert result.resolved and "一百二十万" in result.answer_text


def test_open_text_evidence_is_compact_source_exact_and_language_agnostic():
    memory = OpenTextHorizonMemory(scope_id=1)
    memory.ingest_documents((
        _doc(1, "北京地铁在二零二三年运送了一百二十万名乘客。上海天气晴朗。"),
    ))
    result = memory.retrieve_evidence("北京地铁运送了多少名乘客？", max_results=1)
    assert result.state == "evidence" and len(result.claims) == 1
    assert "一百二十万" in result.text and "天气" not in result.text
    assert result.evidence_bytes == len(result.text.encode("utf-8"))
    cached_engine = memory._evidence_index[1]
    assert memory.retrieve_evidence("北京地铁运送了多少名乘客？", max_results=1).text == result.text
    assert memory._evidence_index[1] is cached_engine
    memory.ingest_documents((_doc(2, "广州新增了一条经过市中心的地铁线路。"),))
    assert memory._evidence_index is None


def test_answer_exclusion_prevents_a_replayed_turn_from_answering_itself():
    memory = OpenTextHorizonMemory(scope_id=1)
    document = _doc(
        1, "My bicycle is cobalt blue.", source="conversation:thread:message-1")
    memory.ingest_documents((document,))
    assert memory.documents_snapshot() == (document,)
    assert memory.answer_excluding_sources(
        "What color is my bicycle?", ("conversation:thread:message-1",)) is None


def test_observed_turn_intents_survive_as_routing_metadata_not_factual_authority():
    profile = EngineProfile(name="full-intent", answer_render_mode="full_dossier")
    memory = OpenTextHorizonMemory(scope_id=1, profile=profile)
    documents = (
        _doc(1, "Turn one established that the Meridian project measured a compute cost "
                "reduction of exactly 42 percent across every evaluated workload.", sequence=1),
        _doc(2, "Turn two established that the Meridian compute cost reduction came from "
                "a redesigned cache that removed duplicate processing work.", sequence=2),
    )
    intents = (
        AnswerContextIntent("turn:1", "What reduction was measured?", (1,)),
        AnswerContextIntent("turn:2", "What caused the reduction?", (2,)),
    )
    memory.ingest_documents(documents, context_intents=intents)
    result = memory.answer("Explain the Meridian compute cost reduction and its cause.")
    assert result.resolved and "42" in result.answer_text and "cache" in result.answer_text


def test_fact_identity_is_immutable_and_scope_or_unknown_intent_is_rejected():
    memory = OpenTextHorizonMemory(scope_id=1)
    with pytest.raises(ValueError):
        memory.ingest_documents((_doc(1, "valid text"),), context_intents=(
            AnswerContextIntent("bad", "unknown", (2,)),))
    with pytest.raises(ValueError):
        memory.ingest_documents((RouteDocument(1, "wrong", 2, "s1", 1, "d"),))
    assert memory.ingest_documents((_doc(1, "A sufficiently long remembered sentence."),)).state \
        == "APPLIED"
    assert memory.ingest_documents((_doc(2, "Another sufficiently long sentence."),)).state == \
        "APPLIED"
    with pytest.raises(ValueError):
        memory.ingest_documents((_doc(1, "Rebound content."),))


def test_durable_open_text_reopens_and_accepts_an_incremental_bundle(tmp_path):
    path = tmp_path / "open-text.jsonl"
    memory = OpenTextHorizonMemory(scope_id=1, ledger_path=path)
    assert memory.ingest_documents((_doc(
        1, "The Meridian project reduced compute cost by exactly 42 percent."),)).state == \
        "APPLIED"
    reopened = OpenTextHorizonMemory(scope_id=1, ledger_path=path)
    assert reopened.fact_count == 1
    assert "42" in reopened.answer("What percent did Meridian reduce compute cost by?").answer_text
    assert reopened.ingest_documents((_doc(
        2, "The reduction came from a redesigned cache that removed duplicate work."),)).state == \
        "APPLIED"
    again = OpenTextHorizonMemory(scope_id=1, ledger_path=path)
    assert again.fact_count == 2
    result = again.answer("What caused the Meridian compute cost reduction?")
    assert result.resolved and "cache" in result.answer_text


def test_open_text_uses_host_record_store_without_plaintext_ledger():
    store = MemoryAuthorizedSidecarRecordStore()
    memory = OpenTextHorizonMemory(scope_id=1, record_store=store)
    document = _doc(1, "The local encrypted host remembers a cobalt bicycle.")
    assert memory.ingest_documents((document,), bundle_id="message:1").state == "APPLIED"
    assert store.load() and memory.fact_count == 1

    reopened = OpenTextHorizonMemory(scope_id=1, record_store=store)
    assert reopened._documents == (document,)


def test_open_text_purge_by_route_source_rechains_host_store():
    store = MemoryAuthorizedSidecarRecordStore()
    memory = OpenTextHorizonMemory(scope_id=1, record_store=store)
    first = _doc(1, "The first durable observation.", source="conversation:t:m1")
    second = _doc(2, "The second durable observation.", source="conversation:t:m2")
    assert memory.ingest_documents((first,), bundle_id="message:m1").state == "APPLIED"
    assert memory.ingest_documents((second,), bundle_id="message:m2").state == "APPLIED"
    receipt = memory.purge_source("conversation:t:m1")
    assert receipt.state == "PURGED" and receipt.removed_fact_ids == (1,)
    assert memory._documents == (second,)
    reopened = OpenTextHorizonMemory(scope_id=1, record_store=store)
    assert reopened._documents == (second,)


def test_open_text_purges_a_source_set_in_one_rechained_commit():
    store = MemoryAuthorizedSidecarRecordStore()
    memory = OpenTextHorizonMemory(scope_id=1, record_store=store)
    documents = (
        _doc(1, "First source.", source="conversation:t:m1"),
        _doc(2, "Second source.", source="conversation:t:m2"),
        _doc(3, "Third source.", source="conversation:u:m3"),
    )
    for document in documents:
        assert memory.ingest_documents(
            (document,), bundle_id=f"message:{document.fact_id}").state == "APPLIED"
    receipt = memory.purge_sources(("conversation:t:m1", "conversation:t:m2"))
    assert receipt.state == "PURGED" and receipt.removed_fact_ids == (1, 2)
    assert memory.documents_snapshot() == (documents[2],)


def test_partial_batch_purge_physically_removes_deleted_source_bytes():
    store = MemoryAuthorizedSidecarRecordStore()
    memory = OpenTextHorizonMemory(scope_id=1, record_store=store)
    deleted = _doc(
        1, "PRIVATE-DELETED-TEXT", source="connector:c:row-1")
    retained = _doc(
        2, "PUBLIC-RETAINED-TEXT", source="connector:c:row-2")
    assert memory.ingest_documents(
        (deleted, retained), bundle_id="connector:c:batch").state == "APPLIED"

    receipt = memory.purge_source("connector:c:row-1")

    assert receipt.state == "PURGED"
    assert b"PRIVATE-DELETED-TEXT" not in b"\n".join(store.load())
    assert b"PUBLIC-RETAINED-TEXT" in b"\n".join(store.load())
    reopened = OpenTextHorizonMemory(scope_id=1, record_store=store)
    assert reopened.documents_snapshot() == (retained,)


def test_open_text_purges_import_batches_even_with_custom_route_sources():
    store = MemoryAuthorizedSidecarRecordStore()
    memory = OpenTextHorizonMemory(scope_id=1, record_store=store)
    imported = _doc(1, "Imported text.", source="custom:external:source")
    retained = _doc(2, "Personal text.", source="conversation:t:m2")
    assert memory.ingest_documents(
        (imported,), bundle_id="connector:c1:0:0").state == "APPLIED"
    assert memory.ingest_documents((retained,), bundle_id="message:m2").state == "APPLIED"
    receipt = memory.purge_batch_source_prefix("connector:c1:")
    assert receipt.state == "PURGED" and receipt.removed_fact_ids == (1,)
    assert memory.documents_snapshot() == (retained,)


def test_open_text_update_is_one_durable_replacement_with_increasing_version():
    store = MemoryAuthorizedSidecarRecordStore()
    original = RouteDocument(
        9, "My bicycle is blue.", 1, "thread", 1, "conversation:thread:m1",
        sequence=3, role="user", speaker="Alice")
    memory = OpenTextHorizonMemory(scope_id=1, record_store=store)
    assert memory.ingest_documents((original,), bundle_id="message:m1").state == "APPLIED"
    replacement = RouteDocument(
        9, "My bicycle is cobalt blue.", 1, "thread", 2, "conversation:thread:m1",
        sequence=3, role="user", speaker="Alice")
    assert memory.update_document(replacement).state == "APPLIED"
    assert memory._documents == (replacement,)
    assert memory.fact_count == 1 and memory._sidecar.record_count == 1
    before = tuple(store.load())
    assert memory.update_document(replacement).state == "IDEMPOTENT"
    assert tuple(store.load()) == before
    reopened = OpenTextHorizonMemory(scope_id=1, record_store=store)
    assert reopened._documents == (replacement,)


def test_failed_open_text_update_commit_keeps_old_version_published():
    store = MemoryAuthorizedSidecarRecordStore()
    original = _doc(10, "Original durable text.", source="conversation:t:m")
    memory = OpenTextHorizonMemory(scope_id=1, record_store=store)
    assert memory.ingest_documents((original,), bundle_id="message:m").state == "APPLIED"
    store.fail_next_replace = True
    replacement = RouteDocument(
        10, "Replacement text.", 1, "s1", 2, "conversation:t:m")
    assert memory.update_document(replacement).state == "REJECTED_DURABILITY"
    assert memory._documents == (original,)


def test_open_text_upsert_atomically_combines_updates_exact_retries_and_inserts():
    store = MemoryAuthorizedSidecarRecordStore()
    original = (
        RouteDocument(20, "Old one.", 1, "connector", 1, "connector:c:n:1",
                      sequence=1),
        RouteDocument(21, "Stable two.", 1, "connector", 1, "connector:c:n:2",
                      sequence=2),
    )
    memory = OpenTextHorizonMemory(scope_id=1, record_store=store)
    assert memory.ingest_documents(original, bundle_id="connector:c:batch:1").state == \
        "APPLIED"
    updated = RouteDocument(
        20, "New one.", 1, "connector", 2, "connector:c:n:1", sequence=1)
    inserted = RouteDocument(
        22, "Novel three.", 1, "connector", 1, "connector:c:n:3", sequence=3)

    receipt = memory.upsert_documents(
        (updated, original[1], inserted), bundle_id="connector:c:batch:2")

    assert receipt.state == "APPLIED"
    assert memory.documents_snapshot() == (updated, original[1], inserted)
    assert memory._sidecar.record_count == 1
    reopened = OpenTextHorizonMemory(scope_id=1, record_store=store)
    assert reopened.documents_snapshot() == (updated, original[1], inserted)
    before = tuple(store.load())
    assert memory.upsert_documents(
        (updated, original[1], inserted),
        bundle_id="connector:c:batch:2").state == "IDEMPOTENT"
    assert tuple(store.load()) == before


def test_failed_open_text_batch_upsert_publishes_none_of_the_batch():
    store = MemoryAuthorizedSidecarRecordStore()
    original = (
        RouteDocument(30, "Old one.", 1, "connector", 1, "connector:c:n:1",
                      sequence=1),
        RouteDocument(31, "Old two.", 1, "connector", 1, "connector:c:n:2",
                      sequence=2),
    )
    memory = OpenTextHorizonMemory(scope_id=1, record_store=store)
    assert memory.ingest_documents(original, bundle_id="connector:c:batch:1").state == \
        "APPLIED"
    store.fail_next_replace = True
    attempted = (
        RouteDocument(30, "New one.", 1, "connector", 2, "connector:c:n:1",
                      sequence=1),
        RouteDocument(31, "New two.", 1, "connector", 2, "connector:c:n:2",
                      sequence=2),
        RouteDocument(32, "Novel three.", 1, "connector", 1, "connector:c:n:3",
                      sequence=3),
    )

    assert memory.upsert_documents(
        attempted, bundle_id="connector:c:batch:2").state == "REJECTED_DURABILITY"
    assert memory.documents_snapshot() == original
    reopened = OpenTextHorizonMemory(scope_id=1, record_store=store)
    assert reopened.documents_snapshot() == original


def test_open_text_upsert_rejects_source_rebinding_and_intent_fiber_replacement():
    store = MemoryAuthorizedSidecarRecordStore()
    memory = OpenTextHorizonMemory(scope_id=1, record_store=store)
    original = RouteDocument(
        40, "Original.", 1, "connector", 1, "connector:c:n:1", sequence=1)
    assert memory.ingest_documents((original,), context_intents=(
        AnswerContextIntent("intent:40", "What is original?", (40,),
                            session_id="connector"),),
        bundle_id="connector:c:batch:1").state == "APPLIED"
    with pytest.raises(ValueError, match="observed-intent"):
        memory.upsert_documents((RouteDocument(
            40, "Updated.", 1, "connector", 2, "connector:c:n:1", sequence=1),))

    plain = OpenTextHorizonMemory(
        scope_id=1, record_store=MemoryAuthorizedSidecarRecordStore())
    assert plain.ingest_documents((original,)).state == "APPLIED"
    with pytest.raises(ValueError, match="source identity"):
        plain.upsert_documents((RouteDocument(
            40, "Collision.", 1, "connector", 2, "connector:c:n:other",
            sequence=1),))


def test_attested_ingest_invalidates_prepared_answer_runtime():
    memory = OpenTextHorizonMemory(scope_id=1)
    memory.ingest_documents((_doc(1, "Meridian recorded a verified value of 42."),))
    memory.answer("What value did Meridian record?")
    prepared = memory._engine._prepared_runtime
    assert prepared is not None
    memory.ingest_documents((_doc(2, "Orion recorded a verified value of 17."),))
    assert memory._engine._prepared_runtime is None
    assert not Path(prepared.workdir).exists()


def test_durable_open_text_reopens_exact_route_metadata_across_sessions(tmp_path):
    path = tmp_path / "route-metadata.jsonl"
    documents = (
        RouteDocument(
            11, "Alice remembered the blue bicycle after lunch.", 1, "session:alpha", 7,
            "chat:alpha", generation_id=3, sequence=9, span=(0, 47), event_time=739100,
            role="user", speaker="Alice"),
        RouteDocument(
            12, "Bob later repaired the bicycle in the garage.", 1, "session:beta", 8,
            "chat:beta", generation_id=4, sequence=2, event_time=739200,
            role="assistant", speaker="Bob"),
    )
    memory = OpenTextHorizonMemory(scope_id=1, session_id="current", ledger_path=path)
    assert memory.ingest_documents(documents).state == "APPLIED"
    before = memory.answer("What did Alice remember after lunch?")
    reopened = OpenTextHorizonMemory(scope_id=1, session_id="current", ledger_path=path)
    assert reopened._documents == documents
    assert all(reopened._sidecar.verify_attestation(item.fact_id) for item in documents)
    after = reopened.answer("What did Alice remember after lunch?")
    assert (after.state, after.answer_text, after.direct_answer, after.resolver_evidence) == (
        before.state, before.answer_text, before.direct_answer, before.resolver_evidence)


def test_open_text_reopens_a_legacy_v1_fact_without_route_metadata(tmp_path):
    path = tmp_path / "legacy-open-text-v1.jsonl"
    template = OpenTextHorizonMemory(scope_id=1)
    authority = template.authority
    text = "The legacy ledger remembers a blue bicycle."
    declaration = StructuredCausalDeclaration(
        1, "1", "legacy:source", "surface_document", text, (0, len(text)),
        5, 6, version=2, event_id="legacy:surface-document:1")
    lifecycle = SidecarLifecycle(
        5, None, authority.purpose, "open-text-host-ingest")
    durable = DurableAuthorizedSidecarMemory("1", path, (authority,))
    receipt = durable.ingest(
        DeclarativeSidecarAdapter(authority),
        CausalAdapterBatch(
            "legacy-bundle", text, "1",
            (SidecarFactDeclaration(declaration, lifecycle),)))
    assert receipt.state == "APPLIED"
    reopened = OpenTextHorizonMemory(scope_id=1, ledger_path=path)
    assert reopened._documents == (RouteDocument(
        1, text, 1, "s1", 2, "legacy:source", sequence=5, event_time=6),)


def test_multiple_messages_from_one_session_are_distinct_event_orbits():
    memory = OpenTextHorizonMemory(scope_id=1)
    documents = (
        _doc(1, "The user started a degree in Economics.", source="session:degree"),
        _doc(2, "The user later graduated in Business Administration.", source="session:degree"),
    )
    receipt = memory.ingest_documents(documents)
    result = memory.retrieve_evidence("What degree did the user graduate with?", max_results=2)
    assert receipt.state == "APPLIED" and memory.fact_count == 2
    assert any("Business Administration" in claim.text for claim in result.claims)
    turns = memory.retrieve_turns("What degree did the user graduate with?", max_results=2,
                                  exploration_reserve=2)
    assert any("Business Administration" in claim.text for claim in turns.claims)
    assert all(claim.source_id == "session:degree" for claim in turns.claims)


def test_context_intents_preserve_causal_insertion_order_not_lexicographic_identity_order():
    memory = OpenTextHorizonMemory(scope_id=1)
    documents = (
        _doc(1, "The second session established the first causal observation."),
        _doc(2, "The tenth session established the later causal observation."),
    )
    intents = (
        AnswerContextIntent("session:2:query-intent", "What happened?", (1,)),
        AnswerContextIntent("session:10:query-intent", "What happened?", (2,)),
    )
    memory.ingest_documents(documents, context_intents=intents)
    assert tuple(item.intent_id for item in memory._context_intents) == tuple(
        item.intent_id for item in intents)


def test_context_intents_survive_restart_with_exact_fiber_and_answer_proof(tmp_path):
    path = tmp_path / "intent-restart.jsonl"
    memory = OpenTextHorizonMemory(scope_id=1, session_id="current", ledger_path=path)
    documents = (
        RouteDocument(21, "The Big Sur trip lasted 3 days.", 1, "trip-session", 1,
                      "chat:21", sequence=1, role="user", speaker="Alice"),
        RouteDocument(22, "The Yosemite trip lasted 5 days.", 1, "trip-session", 1,
                      "chat:22", sequence=2, role="user", speaker="Alice"),
    )
    intents = (AnswerContextIntent(
        "turn:trips", "How long were the two trips?", (21, 22),
        turn_index=0, session_id="trip-session"),)
    assert memory.ingest_documents(documents, context_intents=intents).state == "APPLIED"
    before = memory.answer("How many days did the trips last in total?")
    record = json.loads(path.read_text(encoding="utf-8"))
    attached = [fact["route_metadata"]["observed_intents"] for fact in record["facts"]]
    assert all(rows == attached[0] for rows in attached)
    assert attached[0][0]["fact_ids"] == [21, 22]

    reopened = OpenTextHorizonMemory(scope_id=1, session_id="current", ledger_path=path)
    assert reopened._context_intents == intents
    after = reopened.answer("How many days did the trips last in total?")
    assert (after.state, after.answer_text, after.direct_answer, after.resolver_evidence) == (
        before.state, before.answer_text, before.direct_answer, before.resolver_evidence)


def test_incremental_context_intent_order_survives_restart(tmp_path):
    path = tmp_path / "intent-order.jsonl"
    memory = OpenTextHorizonMemory(scope_id=1, session_id="current", ledger_path=path)
    first = AnswerContextIntent(
        "session:20", "What happened first?", (31,), 0, "history:20")
    second = AnswerContextIntent(
        "session:3", "What happened later?", (32,), 1, "history:3")
    memory.ingest_documents((RouteDocument(
        31, "The first remembered event occurred.", 1, "history:20", 1, "chat:31"),),
        context_intents=(first,))
    memory.ingest_documents((RouteDocument(
        32, "The later remembered event occurred.", 1, "history:3", 1, "chat:32"),),
        context_intents=(second,))
    reopened = OpenTextHorizonMemory(scope_id=1, session_id="current", ledger_path=path)
    assert reopened._context_intents == (first, second)


def test_context_intent_rebinding_and_durable_corruption_fail_closed(tmp_path):
    path = tmp_path / "intent-corruption.jsonl"
    memory = OpenTextHorizonMemory(scope_id=1, ledger_path=path)
    intent = AnswerContextIntent("stable-intent", "What was remembered?", (41,))
    document = _doc(41, "The stable source remembers a blue bicycle.")
    assert memory.ingest_documents((document,), context_intents=(intent,)).state == "APPLIED"
    with pytest.raises(ValueError, match="cannot be rebound"):
        memory.ingest_documents((document,), context_intents=(AnswerContextIntent(
            "stable-intent", "A changed intent text", (41,)),))

    record = json.loads(path.read_text(encoding="utf-8"))
    record["facts"][0]["route_metadata"]["observed_intents"][0]["text"] = "forged"
    record.pop("record_sha256")
    from horizon_memory.durable_typed_sidecar import _canonical
    record["record_sha256"] = hashlib.sha256(_canonical(record)).hexdigest()
    path.write_bytes(_canonical(record) + b"\n")
    with pytest.raises(ValueError, match="replay failed"):
        OpenTextHorizonMemory(scope_id=1, ledger_path=path)
