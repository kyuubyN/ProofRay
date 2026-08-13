# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.causal_ellipsis_ledger import build_field_goal_ledger


def test_ledger_conserves_coordinated_event_count_and_quarter() -> None:
    source = "In the first quarter, Folk booted a 53-yard and a 22-yard field goal."
    ledger = build_field_goal_ledger(source)
    assert ledger.state == "closed"
    assert ledger.event_count == 2
    assert ledger.count(frozenset({1})) == 2
    assert ledger.verify(source)


def test_ledger_distinguishes_success_non_event_and_reference() -> None:
    source = (
        "In the first quarter, Folk made a 31-yard field goal. "
        "His second field goal set a career record. "
        "A 52-yard field goal attempt was blocked."
    )
    ledger = build_field_goal_ledger(source)
    assert ledger.state == "closed"
    assert [item.state for item in ledger.mentions] == ["event", "reference", "non_event"]
    assert ledger.event_count == 1


def test_unresolved_plural_or_ellipsis_fails_closed() -> None:
    for source in (
        "The teams exchanged field goals in the second quarter.",
        "Folk made a 31-yard field goal. He later added another from 42 yards.",
    ):
        ledger = build_field_goal_ledger(source)
        assert ledger.state == "incomplete"
        assert ledger.count() is None


def test_scoped_count_requires_clock_for_every_event() -> None:
    ledger = build_field_goal_ledger("Folk made a 31-yard field goal.")
    assert ledger.count() == 1
    assert ledger.count(frozenset({1})) is None


def test_source_tamper_invalidates_all_mentions() -> None:
    source = "Folk made a 31-yard field goal."
    ledger = build_field_goal_ledger(source)
    assert not ledger.verify(source.replace("31", "41"))
