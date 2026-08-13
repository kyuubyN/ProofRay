# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.event_compiler import SourceAuthority
from horizon_memory.observable_compiler import GaugeMarker, ObservableGaugeCatalog
from horizon_memory.observable_event_compiler import (
    EventSurfaceMarker, ObservableEventCompiler,
)
from horizon_memory.query_hypotheses import PredicateSchema


SCHEMAS = (
    PredicateSchema("buy", (("agent", ("Carla",)), ("patient", ("tablet",)))),
    PredicateSchema("deploy", (("agent", ("Bruno",)), ("patient", ("server",)))),
    PredicateSchema("visit", (("agent", ("Ana",)), ("location", ("Porto", "Recife")))),
    PredicateSchema("walk", (("agent", ("Ana",)),), (("distance", "miles"),)),
)
CATALOG = ObservableGaugeCatalog(tuple(sorted((
    GaugeMarker("predicate", "buy", "buy", 1), GaugeMarker("predicate", "buy", "bought", 2),
    GaugeMarker("predicate", "deploy", "deploy", 3), GaugeMarker("predicate", "deploy", "deployed", 4),
    GaugeMarker("predicate", "visit", "visited", 5), GaugeMarker("predicate", "walk", "walked", 6),
))))
COMPILER = ObservableEventCompiler(SCHEMAS, CATALOG, (
    EventSurfaceMarker("polarity", "negative", "not", 10),
))


def test_warm_path_compiles_active_passive_quantity_and_negation_without_model():
    cases = (
        ("Ana walked 3 miles.", "walk", {"agent": "Ana"}, "positive"),
        ("The server was deployed by Bruno.", "deploy", {"agent": "Bruno", "patient": "server"}, "positive"),
        ("Bruno deployed the server.", "deploy", {"agent": "Bruno", "patient": "server"}, "positive"),
        ("Carla did not buy the tablet.", "buy", {"agent": "Carla", "patient": "tablet"}, "negative"),
    )
    for fact_id, (text, predicate, roles, polarity) in enumerate(cases, 1):
        result = COMPILER.compile(SourceAuthority("s", fact_id, text))
        assert result.state == "resolved" and len(result.events) == 1
        event = result.events[0]
        assert event.predicate == predicate and dict(event.roles) == roles and event.polarity == polarity
    assert COMPILER.compile(SourceAuthority("s", 9, cases[0][0])).events[0].quantities[0].value == 3


def test_duplicate_microcitations_keep_distinct_authoritative_spans():
    result = COMPILER.compile(SourceAuthority("s", 20, "Ana visited Porto. Ana visited Porto."))
    assert result.state == "resolved" and len(result.events) == 2
    assert result.events[0].exact_span != result.events[1].exact_span
    assert result.events[0].parent_sha256 == result.events[1].parent_sha256


def test_unknown_entity_or_conflicting_predicate_abstains_atomically():
    unknown = COMPILER.compile(SourceAuthority("s", 30, "Someone deployed something."))
    assert unknown.state == "abstain" and not unknown.events
    conflict = COMPILER.compile(SourceAuthority("s", 31, "Bruno deployed the server and Ana visited Porto."))
    assert conflict.state == "abstain" and not conflict.events
