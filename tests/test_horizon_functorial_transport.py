# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.functorial_transport import (
    FunctorialTransportLedger, GaugeConnection,
)
from horizon_memory.event_field import (
    Constraint, EventRecord, IncrementalEventField, QueryProgram,
)


def edge(source, target, fact, *, scope="team", start=None, end=None):
    return GaugeConnection(scope, source, target, fact, start, end)


def test_composition_equals_direct_transport():
    composed = FunctorialTransportLedger((edge("ship it", "release", 1),
                                          edge("release", "deploy", 2)))
    direct = FunctorialTransportLedger((edge("ship it", "deploy", 3),))
    a = composed.resolve("team", "ship it")
    b = direct.resolve("team", "ship it")
    assert a.canonical == b.canonical == "deploy"
    assert a.path == ("ship it", "release", "deploy")
    assert a.evidence_fact_ids == (1, 2)


def test_jargon_is_scoped_and_redefinition_is_temporal():
    ledger = FunctorialTransportLedger((
        edge("hot", "popular", 1, scope="music", start=0, end=9),
        edge("hot", "high-temperature", 2, scope="lab", start=0, end=20),
        edge("hot", "urgent", 3, scope="music", start=10, end=20),
    ))
    assert ledger.resolve("music", "hot", 5).canonical == "popular"
    assert ledger.resolve("music", "hot", 15).canonical == "urgent"
    assert ledger.resolve("lab", "hot", 5).canonical == "high-temperature"
    assert ledger.resolve("unknown", "hot", 5).state == "identity"


def test_incompatible_paths_create_holonomy_defect_and_fail_closed():
    ledger = FunctorialTransportLedger((edge("bank", "financial-bank", 1),
                                        edge("bank", "river-bank", 2)))
    result = ledger.resolve("team", "bank")
    assert result.state == "conflict" and result.canonical is None
    assert result.alternatives == ("financial-bank", "river-bank")
    assert ledger.holonomy_defect("team", "bank") == 1


def test_cycles_do_not_loop_or_grant_a_canonical_endpoint():
    ledger = FunctorialTransportLedger((edge("a", "b", 1), edge("b", "a", 2)))
    result = ledger.resolve("team", "a")
    assert result.state == "conflict" and result.canonical is None


def test_unqualified_clock_cannot_use_time_bounded_alias():
    ledger = FunctorialTransportLedger((edge("x", "old-x", 1, start=0, end=9),))
    assert ledger.resolve("team", "x").state == "identity"


def test_event_and_query_share_transport_without_erasing_surface_proof():
    ledger = FunctorialTransportLedger((edge("shipped", "deploy", 77),
                                        edge("went live", "deploy", 78)))
    raw = EventRecord("e1", "team", "shipped", (("actor", "ana"),), 10,
                      "a" * 64, (0, 7), event_time=5)
    transported = ledger.transport_event(raw)
    assert transported.predicate == "deploy"
    assert transported.surface_predicate == "shipped"
    assert transported.transport_fact_ids == (77,)
    assert "surface_predicate:shipped" in transported.charges()
    field = IncrementalEventField()
    field.ingest(transported)
    query = ledger.transport_program(QueryProgram(
        "project", "team", "went live", (Constraint("role:actor", "ana"),),
        project="event_time"))
    result = field.execute(query)
    assert result.value == 5 and result.fact_ids == (10,)


def test_conflicting_transport_cannot_enter_event_field():
    ledger = FunctorialTransportLedger((edge("bank", "financial-bank", 1),
                                        edge("bank", "river-bank", 2)))
    raw = EventRecord("e1", "team", "bank", (("topic", "x"),), 10,
                      "a" * 64, (0, 4))
    try:
        ledger.transport_event(raw)
    except ValueError as error:
        assert "failed closed" in str(error)
    else:
        raise AssertionError("ambiguous gauge transport was accepted")
