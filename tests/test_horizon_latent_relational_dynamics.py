# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.latent_relational_dynamics import (
    LatentRelationalField, RelationalSeparation,
)
from horizon_memory.relational_music import RelationalPerformance


def _p(canonical, companions, fact_id, clock, scope="s"):
    return RelationalPerformance(scope, canonical, f"surface-{fact_id}",
                                 tuple(sorted(companions)), fact_id, clock)


def test_hidden_mediator_closes_multiple_independently_witnessed_intervals():
    field = LatentRelationalField(tuple(sorted((
        _p("deploy", ("goal:release", "phase:delivery"), 1, 1),
        _p("deploy", ("role:agent", "phase:delivery"), 2, 2),
        _p("deploy", ("role:patient", "phase:delivery"), 3, 3),
        _p("visit", ("goal:travel", "phase:journey"), 4, 1),
        _p("visit", ("role:agent", "phase:journey"), 5, 2),
    ))))
    companions = tuple(sorted(("goal:release", "role:agent", "role:patient")))
    result = field.listen("s", "yeeted", companions, 4)
    assert result.state == "resolved" and result.canonical == "deploy"
    assert result.candidates[0].mediators == ("phase:delivery",)
    assert result.evidence_fact_ids == (1, 2, 3)


def test_one_observation_cannot_invent_a_latent_mediator_from_clique_expansion():
    field = LatentRelationalField((_p(
        "deploy", ("goal:release", "phase:delivery", "role:agent", "role:patient"), 1, 1),))
    result = field.listen("s", "unknown", tuple(sorted((
        "goal:release", "role:agent", "role:patient"))), 2)
    assert result.state == "abstain"
    assert result.reason == "no independently witnessed latent closure"


def test_observed_separation_overrides_attraction_and_is_provenanced():
    performances = tuple(sorted((
        _p("deploy", ("goal:release", "phase:delivery"), 1, 1),
        _p("deploy", ("role:agent", "phase:delivery"), 2, 2),
        _p("deploy", ("role:patient", "phase:delivery"), 3, 3),
    )))
    boundary = RelationalSeparation(
        "s", "goal:release", "phase:delivery", fact_id=90, observed_at=5)
    field = LatentRelationalField(performances, (boundary,))
    companions = tuple(sorted(("goal:release", "role:agent", "role:patient")))
    assert field.listen("s", "unknown", companions, 4).state == "resolved"
    separated = field.listen("s", "unknown", companions, 6)
    assert separated.state == "abstain"
    assert separated.reason == "observed separation blocks latent closure"
    assert separated.separation_fact_ids == (90,)


def test_temporary_separation_repels_without_erasing_the_preserved_history():
    performances = tuple(sorted((
        _p("deploy", ("goal:release", "phase:delivery"), 1, 1),
        _p("deploy", ("role:agent", "phase:delivery"), 2, 2),
        _p("deploy", ("role:patient", "phase:delivery"), 3, 3),
    )))
    boundary = RelationalSeparation(
        "s", "goal:release", "phase:delivery", 91, 5, valid_until=8,
        reason="temporary_goal_exit")
    field = LatentRelationalField(performances, (boundary,))
    chord = tuple(sorted(("goal:release", "role:agent", "role:patient")))
    assert field.listen("s", "unknown", chord, 4).state == "resolved"
    assert field.listen("s", "unknown", chord, 6).state == "abstain"
    # The inverse boundary expires; it did not destructively delete the forward field.
    assert field.listen("s", "unknown", chord, 9).state == "resolved"


def test_future_and_other_scope_structure_are_invisible_and_competition_abstains():
    base = (
        _p("deploy", ("a", "m"), 1, 1), _p("deploy", ("b", "m"), 2, 2),
        _p("deploy", ("c", "m"), 3, 30),
        _p("visit", ("a", "n"), 4, 1), _p("visit", ("b", "n"), 5, 2),
        _p("visit", ("c", "n"), 6, 3),
        _p("buy", ("a", "x"), 7, 1, "other"),
        _p("buy", ("b", "x"), 8, 2, "other"),
        _p("buy", ("c", "x"), 9, 3, "other"),
    )
    field = LatentRelationalField(tuple(sorted(base)))
    query = ("a", "b", "c")
    early = field.listen("s", "unknown", query, 10)
    assert early.state == "resolved" and early.canonical == "visit"
    late = field.listen("s", "unknown", query, 40)
    assert late.state == "abstain"
    assert late.reason == "multiple latent structures explain the chord"
