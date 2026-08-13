# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.observable_compiler import (
    GaugeMarker, ObservableGaugeCatalog, ObservableQueryCompiler,
)
from horizon_memory.query_hypotheses import PredicateSchema


SCHEMAS = (
    PredicateSchema("deploy", (("agent", ("Bruno",)), ("patient", ("Atlas service",)))),
    PredicateSchema("visit", (("agent", ("Ana",)), ("location", ("Recife",)))),
)


def _catalog(*markers):
    return ObservableGaugeCatalog(tuple(sorted(markers)))


def test_observable_charges_compile_program_with_marker_provenance():
    compiler = ObservableQueryCompiler(SCHEMAS, _catalog(
        GaugeMarker("operator", "argmax", "most recent", 1),
        GaugeMarker("predicate", "visit", "stop in", 2),
        GaugeMarker("target_role", "location", "location", 3),
        GaugeMarker("clock", "event_time", "occurred", 4),
    ))
    result = compiler.compile(
        "Which location belongs to Ana's most recent stop in, based on when it occurred?", "s")
    assert result.state == "resolved" and result.program.operator == "argmax"
    assert result.program.predicate == "visit" and result.program.project == "role:location"
    assert result.program.constraints[0].value == "Ana"
    assert result.marker_fact_ids == (1, 2, 3, 4)


def test_conflicting_or_missing_charge_abstains_and_unsupported_is_explicit():
    conflict = ObservableQueryCompiler(SCHEMAS, _catalog(
        GaugeMarker("operator", "argmax", "latest", 1),
        GaugeMarker("operator", "argmin", "earliest", 2),
        GaugeMarker("predicate", "visit", "visit", 3),
    )).compile("Was the latest or earliest visit by Ana?", "s")
    assert conflict.state == "abstain" and conflict.program is None

    unsupported = ObservableQueryCompiler(SCHEMAS, _catalog(
        GaugeMarker("operator", "unsupported", "fictional poem", 9),
    )).compile("Write a fictional poem about Ana.", "s")
    assert unsupported.state == "unsupported" and unsupported.marker_fact_ids == (9,)


def test_entity_can_select_one_schema_but_never_resolve_the_operator():
    compiler = ObservableQueryCompiler(SCHEMAS, _catalog(
        GaugeMarker("operator", "exists", "can we confirm", 1),
    ))
    result = compiler.compile("Can we confirm an event involving Atlas service?", "s")
    assert result.state == "resolved" and result.program.predicate == "deploy"
    absent = compiler.compile("Tell me something involving Atlas service.", "s")
    assert absent.state == "abstain"


def test_semantic_intent_dominates_auxiliary_did_but_two_strong_intents_conflict():
    compiler = ObservableQueryCompiler(SCHEMAS, _catalog(
        GaugeMarker("operator", "exists", "did", 1),
        GaugeMarker("operator", "sum", "total", 2),
        GaugeMarker("operator", "unsupported", "why", 3),
        GaugeMarker("operator", "argmax", "latest", 4),
        GaugeMarker("predicate", "visit", "visit", 5),
        GaugeMarker("quantity_kind", "distance", "distance", 6),
        GaugeMarker("unit", "miles", "miles", 7),
    ))
    assert compiler.compile("Why did Ana visit?", "s").state == "unsupported"
    conflict = compiler.compile("What is the total for the latest visit by Ana?", "s")
    assert conflict.state == "abstain" and "operator" in conflict.reason
