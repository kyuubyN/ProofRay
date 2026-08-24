# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from dataclasses import replace
import hashlib

import pytest

from horizon_memory import (
    AttestedCompletenessClaim, AttestedSidecarFact, AuthorizedAdapterBridge,
    AuthorizedSidecarMemory,
    CausalAdapterBatch, CausalSelector, CausalSourceEnvelope, DeterministicCausalCompiler,
    DeclarativeSidecarAdapter, JsonCausalMapping, JsonPointerCausalAdapter,
    SidecarAuthority, SidecarCompilation,
    SidecarCompletenessDeclaration, SidecarFactDeclaration, SidecarLifecycle,
    SidecarLimits, StructuredCausalDeclaration, TypedCausalProgram,
)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


AUTHORITY = SidecarAuthority(
    adapter_id="inventory-sidecar",
    rule_id="inventory-json-schema",
    rule_version=3,
    schema_sha256=_sha('{"subject":"string","state":"string"}'),
    allowed_scopes=("shop",),
    allowed_predicates=("state", "stock"),
    purpose="answer inventory state queries",
)


class Adapter:
    authority = AUTHORITY

    def compile_sidecar(self, batch):
        source = CausalSourceEnvelope.seal(batch.source_id, batch.content)
        facts = tuple(DeterministicCausalCompiler.compile(source, item)
                      for item in batch.declarations)
        return tuple(AttestedSidecarFact.seal(fact, self.authority) for fact in facts)


def _declaration(fid=1, predicate="state", value="ready", span=(12, 17)):
    return StructuredCausalDeclaration(
        fid, "shop", "printer", predicate, value, span, fid, fid,
        version=fid, event_id=f"event-{fid}")


def _batch(declarations=None, content='{"printer":"ready"}', source_id="inventory"):
    return CausalAdapterBatch(source_id, content, "shop",
                              tuple(declarations or (_declaration(),)))


def test_authorized_sidecar_binds_full_semantics_to_manifest_and_source():
    memory = AuthorizedSidecarMemory("shop", (AUTHORITY,))
    receipt = memory.ingest(Adapter(), _batch())
    result = memory.query(TypedCausalProgram(
        "LOOKUP", CausalSelector("printer", "state")))
    assert receipt.state == "APPLIED"
    assert receipt.authority_sha256 == AUTHORITY.authority_sha256
    assert (result.state, result.value) == ("resolved", "ready")
    assert memory.verify_attestation(1)


def test_manifest_is_canonical_and_schema_or_rule_change_changes_identity():
    reordered = SidecarAuthority(
        "inventory-sidecar", "inventory-json-schema", 3, AUTHORITY.schema_sha256,
        ("shop",), ("state", "stock"), "answer inventory state queries")
    assert reordered.authority_sha256 == AUTHORITY.authority_sha256
    assert replace(AUTHORITY, rule_version=4).authority_sha256 != AUTHORITY.authority_sha256
    assert replace(AUTHORITY, schema_sha256=_sha("different")).authority_sha256 != \
        AUTHORITY.authority_sha256


def test_unregistered_or_changed_authority_is_rejected_before_commit():
    changed = replace(AUTHORITY, rule_version=4)
    adapter = Adapter()
    adapter.authority = changed
    memory = AuthorizedSidecarMemory("shop", (AUTHORITY,))
    receipt = memory.ingest(adapter, _batch())
    assert receipt.state == "REJECTED_AUTHORITY"
    assert memory.fact_count == 0


def test_scope_and_predicate_capabilities_are_fail_closed():
    memory = AuthorizedSidecarMemory("shop", (AUTHORITY,))
    forbidden = _batch((_declaration(predicate="secret"),))
    assert memory.ingest(Adapter(), forbidden).state == "REJECTED_CAPABILITY"
    wrong_scope = CausalAdapterBatch("inventory", '{"printer":"ready"}', "other",
                                     (_declaration(),))
    assert memory.ingest(Adapter(), wrong_scope).state == "REJECTED_SCOPE"
    assert memory.fact_count == 0


def test_tampered_attestation_and_microcitation_reject_the_whole_batch():
    class Tampered(Adapter):
        def compile_sidecar(self, batch):
            good = super().compile_sidecar(batch)
            return (good[0], replace(good[1], attestation_sha256="0" * 64))

    declarations = (_declaration(), _declaration(2, value="online", span=(30, 36)))
    content = '{"printer":"ready","network":"online"}'
    memory = AuthorizedSidecarMemory("shop", (AUTHORITY,))
    assert memory.ingest(Tampered(), _batch(declarations, content)).state == \
        "REJECTED_ATTESTATION"
    assert memory.fact_count == 0

    bad_span = _batch((_declaration(value="false", span=(12, 17)),))
    assert memory.ingest(Adapter(), bad_span).state == "REJECTED_ADAPTER"
    assert memory.fact_count == 0


def test_same_fact_cannot_be_rebound_to_a_different_attestation():
    memory = AuthorizedSidecarMemory("shop", (AUTHORITY,))
    assert memory.ingest(Adapter(), _batch()).state == "APPLIED"
    second = _batch((_declaration(value="later", span=(12, 17)),),
                    '{"printer":"later"}', "inventory-2")
    assert memory.ingest(Adapter(), second).state == "REJECTED_ATTESTATION_COLLISION"
    assert memory.fact_count == 1


def test_identical_retry_is_idempotent_and_remains_attested():
    memory = AuthorizedSidecarMemory("shop", (AUTHORITY,))
    assert memory.ingest(Adapter(), _batch()).state == "APPLIED"
    assert memory.ingest(Adapter(), _batch()).state == "IDEMPOTENT"
    assert memory.fact_count == 1 and memory.verify_attestation(1)


def test_aggregation_requires_an_attested_exact_population_certificate():
    authority = replace(AUTHORITY, closed_world_predicates=("stock",))

    class CompleteAdapter(Adapter):
        def __init__(self):
            self.authority = authority

        def compile_sidecar(self, batch):
            source = CausalSourceEnvelope.seal(batch.source_id, batch.content)
            facts = tuple(AttestedSidecarFact.seal(
                DeterministicCausalCompiler.compile(source, item), self.authority)
                for item in batch.declarations)
            claim = AttestedCompletenessClaim.seal(
                self.authority, source, "warehouse", "stock",
                tuple(item.fact.fact_id for item in facts))
            return SidecarCompilation(facts, (claim,))

    content = 'items: apple, pear'
    declarations = (
        StructuredCausalDeclaration(1, "shop", "warehouse", "stock", "apple",
                                    (7, 12), 1, 1, event_id="apple"),
        StructuredCausalDeclaration(2, "shop", "warehouse", "stock", "pear",
                                    (14, 18), 1, 1, event_id="pear"),
    )
    memory = AuthorizedSidecarMemory("shop", (authority,))
    assert memory.ingest(CompleteAdapter(), _batch(declarations, content, "stock-1")).state == \
        "APPLIED"
    program = TypedCausalProgram(
        "COUNT_DISTINCT", CausalSelector("warehouse", "stock"), closed_world=True)
    assert memory.query(program).reason == "sidecar_completeness_certificate_required"
    certificate = memory.completeness_certificate("warehouse", "stock")
    assert certificate is not None
    result = memory.query_certified(program, certificate)
    assert (result.state, result.value, result.fact_ids) == ("resolved", "2", (1, 2))


def test_new_matching_fact_invalidates_old_completeness_certificate():
    authority = replace(AUTHORITY, closed_world_predicates=("stock",))

    class Snapshot(Adapter):
        def __init__(self, close=True):
            self.authority = authority
            self.close = close

        def compile_sidecar(self, batch):
            source = CausalSourceEnvelope.seal(batch.source_id, batch.content)
            facts = tuple(AttestedSidecarFact.seal(
                DeterministicCausalCompiler.compile(source, item), self.authority)
                for item in batch.declarations)
            claims = (AttestedCompletenessClaim.seal(
                self.authority, source, "warehouse", "stock",
                tuple(item.fact.fact_id for item in facts)),) if self.close else ()
            return SidecarCompilation(facts, claims)

    first = CausalAdapterBatch("s1", "apple", "shop", (
        StructuredCausalDeclaration(1, "shop", "warehouse", "stock", "apple",
                                    (0, 5), 1, 1, event_id="apple"),))
    second = CausalAdapterBatch("s2", "pear", "shop", (
        StructuredCausalDeclaration(2, "shop", "warehouse", "stock", "pear",
                                    (0, 4), 2, 2, event_id="pear"),))
    memory = AuthorizedSidecarMemory("shop", (authority,))
    assert memory.ingest(Snapshot(), first).state == "APPLIED"
    certificate = memory.completeness_certificate("warehouse", "stock")
    assert certificate is not None
    assert memory.ingest(Snapshot(close=False), second).state == "APPLIED"
    program = TypedCausalProgram("COUNT_DISTINCT", CausalSelector("warehouse", "stock"))
    result = memory.query_certified(program, certificate)
    assert (result.state, result.reason) == (
        "abstain", "invalid_or_stale_completeness_certificate")


def test_ttl_is_fail_closed_and_requires_an_explicit_evaluation_time():
    class Expiring(Adapter):
        def compile_sidecar(self, batch):
            source = CausalSourceEnvelope.seal(batch.source_id, batch.content)
            fact = DeterministicCausalCompiler.compile(source, batch.declarations[0])
            lifecycle = SidecarLifecycle(
                10, 20, self.authority.purpose, "consent:ticket-42")
            return (AttestedSidecarFact.seal(fact, self.authority, lifecycle),)

    declaration = StructuredCausalDeclaration(
        1, "shop", "printer", "state", "ready", (0, 5), 10, 10,
        event_id="printer-state")
    memory = AuthorizedSidecarMemory("shop", (AUTHORITY,))
    assert memory.ingest(Expiring(), _batch((declaration,), "ready", "ttl")).state == "APPLIED"
    program = TypedCausalProgram("LOOKUP", CausalSelector("printer", "state"))
    assert memory.query(program).reason == "sidecar_evaluation_time_required"
    assert memory.query(program, as_of=9).reason == "sidecar_fact_not_yet_valid"
    assert memory.query(program, as_of=15).value == "ready"
    assert memory.query(program, as_of=20).reason == "sidecar_fact_expired"


def test_update_lineage_is_explicit_and_invalidation_never_resurrects_old_state():
    class LifecycleAdapter(Adapter):
        def __init__(self, supersedes=()):
            self.supersedes = supersedes

        def compile_sidecar(self, batch):
            source = CausalSourceEnvelope.seal(batch.source_id, batch.content)
            fact = DeterministicCausalCompiler.compile(source, batch.declarations[0])
            lifecycle = SidecarLifecycle(
                fact.observed_at, None, self.authority.purpose,
                "policy:inventory", self.supersedes)
            return (AttestedSidecarFact.seal(fact, self.authority, lifecycle),)

    first = CausalAdapterBatch("u1", "ready", "shop", (
        StructuredCausalDeclaration(1, "shop", "printer", "state", "ready",
                                    (0, 5), 1, 1, version=1,
                                    event_id="printer-state"),))
    second = CausalAdapterBatch("u2", "online", "shop", (
        StructuredCausalDeclaration(2, "shop", "printer", "state", "online",
                                    (0, 6), 2, 2, version=2,
                                    event_id="printer-state"),))
    retired = CausalAdapterBatch("u3", "retired", "shop", (
        StructuredCausalDeclaration(3, "shop", "printer", "state", "retired",
                                    (0, 7), 3, 3, version=3, asserted=False,
                                    event_id="printer-state"),))
    memory = AuthorizedSidecarMemory("shop", (AUTHORITY,))
    assert memory.ingest(LifecycleAdapter(), first).state == "APPLIED"
    assert memory.ingest(LifecycleAdapter(), second).state == "REJECTED_UPDATE"
    assert memory.ingest(LifecycleAdapter((1,)), second).state == "APPLIED"
    program = TypedCausalProgram("LOOKUP", CausalSelector("printer", "state"))
    assert memory.query(program).value == "online"
    assert memory.ingest(LifecycleAdapter((2,)), retired).state == "APPLIED"
    result = memory.query(program)
    assert (result.state, result.reason) == ("abstain", "uncertain_fact")


def test_lifecycle_purpose_cannot_escape_manifest_authority():
    source = CausalSourceEnvelope.seal("s", "ready")
    fact = DeterministicCausalCompiler.compile(source, StructuredCausalDeclaration(
        1, "shop", "printer", "state", "ready", (0, 5), 1, 1))
    try:
        AttestedSidecarFact.seal(
            fact, AUTHORITY, SidecarLifecycle(1, None, "marketing", "consent:x"))
    except ValueError as error:
        assert "outside its authority" in str(error)
    else:
        raise AssertionError("purpose escalation must fail closed")


def test_built_in_declarative_adapter_needs_no_custom_adapter_code():
    authority = replace(AUTHORITY, closed_world_predicates=("stock",))
    lifecycle = SidecarLifecycle(1, None, authority.purpose, "policy:shop")
    declarations = (
        SidecarFactDeclaration(StructuredCausalDeclaration(
            1, "shop", "warehouse", "stock", "apple", (0, 5), 1, 1,
            event_id="apple"), lifecycle),
        SidecarCompletenessDeclaration("warehouse", "stock", (1,)),
    )
    memory = AuthorizedSidecarMemory("shop", (authority,))
    receipt = memory.ingest(
        DeclarativeSidecarAdapter(authority),
        CausalAdapterBatch("inventory", "apple", "shop", declarations))
    certificate = memory.completeness_certificate("warehouse", "stock")
    result = memory.query_certified(TypedCausalProgram(
        "COUNT_DISTINCT", CausalSelector("warehouse", "stock")), certificate)
    assert receipt.state == "APPLIED"
    assert (result.state, result.value) == ("resolved", "1")


def test_existing_json_adapter_enters_strict_sidecar_through_reusable_bridge():
    authority = replace(AUTHORITY, adapter_id="json-pointer-v1")
    mapping = JsonCausalMapping(
        1, "/state/value", "engine", "state", 1, 1, "/state/unit",
        event_id="engine-state")
    batch = CausalAdapterBatch(
        "json-row", '{"state":{"value":7,"unit":"code"}}', "shop", (mapping,))
    memory = AuthorizedSidecarMemory("shop", (authority,))
    bridge = AuthorizedAdapterBridge(authority, JsonPointerCausalAdapter())
    assert memory.ingest(bridge, batch).state == "APPLIED"
    result = memory.query(TypedCausalProgram(
        "LOOKUP", CausalSelector("engine", "state")))
    assert (result.state, result.value, result.unit) == ("resolved", "7", "code")


def test_certified_empty_population_resolves_to_zero_without_a_fake_fact():
    authority = replace(AUTHORITY, closed_world_predicates=("stock",))
    batch = CausalAdapterBatch("empty-inventory", "[]", "shop", (
        SidecarCompletenessDeclaration("warehouse", "stock", ()),))
    memory = AuthorizedSidecarMemory("shop", (authority,))
    receipt = memory.ingest(DeclarativeSidecarAdapter(authority), batch)
    certificate = memory.completeness_certificate("warehouse", "stock")
    result = memory.query_certified(TypedCausalProgram(
        "COUNT_DISTINCT", CausalSelector("warehouse", "stock")), certificate)
    assert receipt.state == "APPLIED" and memory.fact_count == 0
    assert (result.state, result.value, result.reason) == (
        "resolved", "0", "certified_empty_distinct_count")


def test_one_memory_boundary_cannot_mix_unrelated_purposes():
    with pytest.raises(ValueError, match="exactly one purpose"):
        AuthorizedSidecarMemory("shop", (
            AUTHORITY, replace(AUTHORITY, adapter_id="other", purpose="marketing")))


def test_malformed_adapter_output_and_resource_exhaustion_fail_closed():
    class MutableOutput:
        authority = AUTHORITY

        def compile_sidecar(self, batch):
            return []

    memory = AuthorizedSidecarMemory(
        "shop", (AUTHORITY,), limits=SidecarLimits(max_source_bytes=5))
    malformed = memory.ingest(MutableOutput(), _batch())
    oversized = memory.ingest(Adapter(), _batch(content="ready!"))
    assert malformed.state == "REJECTED_RESOURCE_LIMIT"  # Source is larger than this test cap.
    assert oversized.state == "REJECTED_RESOURCE_LIMIT"
    regular = AuthorizedSidecarMemory("shop", (AUTHORITY,))
    assert regular.ingest(MutableOutput(), _batch()).state == "REJECTED_ADAPTER"
    assert regular.fact_count == 0


def test_negative_evaluation_clock_is_rejected_not_interpreted():
    memory = AuthorizedSidecarMemory("shop", (AUTHORITY,))
    assert memory.ingest(Adapter(), _batch()).state == "APPLIED"
    result = memory.query(TypedCausalProgram(
        "LOOKUP", CausalSelector("printer", "state")), as_of=-1)
    assert (result.state, result.reason) == (
        "unsupported", "invalid_sidecar_evaluation_time")


def test_irrelevant_fiber_replication_cannot_change_active_fiber_answer_or_proof_ids():
    memory = AuthorizedSidecarMemory("shop", (AUTHORITY,))
    assert memory.ingest(Adapter(), _batch()).state == "APPLIED"
    program = TypedCausalProgram("LOOKUP", CausalSelector("printer", "state"))
    before = memory.query(program)
    for index in range(64):
        value = f"state-{index}"
        declaration = StructuredCausalDeclaration(
            1000 + index, "shop", f"unrelated-{index}", "state", value,
            (0, len(value)), 1000 + index, 1000 + index,
            event_id=f"unrelated-event-{index}")
        batch = CausalAdapterBatch(
            f"unrelated-source-{index}", value, "shop", (declaration,))
        assert memory.ingest(Adapter(), batch).state == "APPLIED"
    after = memory.query(program)
    assert (before.state, before.value, before.fact_ids) == (
        after.state, after.value, after.fact_ids) == ("resolved", "ready", (1,))
