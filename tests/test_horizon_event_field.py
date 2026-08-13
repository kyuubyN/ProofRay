# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from decimal import Decimal

import pytest

from horizon_memory.event_field import (
    Constraint, EventRecord, IncrementalEventField, Quantity, QueryProgram,
)


_SHA = "1" * 64


def event(number, predicate="activity", *, actor="alex", event_time=None, value=None):
    quantities = (() if value is None else
                  (Quantity("duration", Decimal(value), "days"),))
    return EventRecord(
        f"e{number}", "session-a", predicate, (("actor", actor),), number, _SHA,
        (number, number + 1), event_time=event_time, quantities=quantities,
    )


def test_generic_count_is_closed_world_and_invalidated_by_new_ingest():
    field = IncrementalEventField()
    field.ingest(event(1, "invented-predicate", actor="a"))
    field.ingest(event(2, "invented-predicate", actor="b"))
    program = QueryProgram(
        "count_distinct", "session-a", "invented-predicate",
        distinct_by="role:actor", require_complete=True,
    )
    assert field.execute(program).reason == "missing_completeness_certificate"
    field.certify_complete("session-a", "invented-predicate")
    assert field.execute(program).value == 2
    field.ingest(event(3, "invented-predicate", actor="c"))
    assert field.execute(program).state == "abstain"


def test_constraint_order_is_canonical_and_role_direction_is_conserved():
    a = tuple(sorted((Constraint("role:actor", "ana"), Constraint("modality", "asserted"))))
    b = tuple(sorted(reversed(a)))
    left = QueryProgram("project", "s", "gave", a, project="role:recipient")
    right = QueryProgram("project", "s", "gave", b, project="role:recipient")
    assert left.canonical_sha256() == right.canonical_sha256()
    with pytest.raises(ValueError):
        QueryProgram("project", "s", "gave", tuple(reversed(a)), project="role:recipient")


def test_incremental_index_scans_only_selected_fiber():
    field = IncrementalEventField()
    for number in range(100):
        field.ingest(event(number, f"predicate-{number}"))
    result = field.execute(QueryProgram(
        "project", "session-a", "predicate-42", project="role:actor"))
    assert result.value == "alex"
    assert result.scanned_events == 1
    assert field.event_count == 100


def test_event_id_constraint_uses_direct_index_inside_large_fiber():
    field = IncrementalEventField()
    for number in range(100):
        field.ingest(event(number, "same-predicate"))
    result = field.execute(QueryProgram(
        "project", "session-a", "same-predicate", (Constraint("event_id", "e42"),),
        project="role:actor"))
    assert result.value == "alex" and result.scanned_events == 1


def test_sum_and_clock_difference_are_domain_independent():
    field = IncrementalEventField()
    field.ingest(event(1, "trip", event_time=100, value="2"))
    field.ingest(event(2, "trip", event_time=109, value="3"))
    field.certify_complete("session-a", "trip")
    total = field.execute(QueryProgram(
        "sum", "session-a", "trip", quantity_kind="duration", unit="days",
        require_complete=True))
    assert total.value == Decimal(5)
    left = QueryProgram("project", "session-a", "trip",
                        (Constraint("event_id", "e1"),), project="event_time")
    right = QueryProgram("project", "session-a", "trip",
                         (Constraint("event_id", "e2"),), project="event_time")
    difference = field.execute(QueryProgram(
        "diff", "session-a", "comparison", unit="days", left=left, right=right))
    assert difference.value == Decimal(9)
    assert difference.fact_ids == (1, 2)


def test_negative_existence_requires_completeness_but_positive_witness_does_not():
    field = IncrementalEventField()
    program = QueryProgram("exists", "session-a", "purchase")
    assert field.execute(program).reason == "missing_negative_completeness_certificate"
    field.certify_complete("session-a", "purchase")
    assert field.execute(program).value is False
    field.ingest(event(1, "purchase"))
    assert field.execute(program).value is True


def test_no_silent_unit_or_provenance_loss():
    field = IncrementalEventField()
    field.ingest(EventRecord(
        "e1", "s", "walk", (("actor", "ana"),), 1, _SHA, (0, 2),
        quantities=(Quantity("distance", Decimal("3"), "miles"),),
    ))
    field.certify_complete("s", "walk")
    result = field.execute(QueryProgram(
        "sum", "s", "walk", quantity_kind="distance", unit="kilometers",
        require_complete=True))
    assert result.state == "abstain"
    assert result.reason == "missing_or_inexact_quantity"
    assert "quantity:distance=3:miles" in field._events["e1"].charges()
