# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from horizon_memory.relational_music import (
    RelationalMusicField, RelationalPerformance,
)


def _performance(scope, canonical, surface, companions, fact_id, observed_at, pragmatic="literal"):
    return RelationalPerformance(scope, canonical, surface, tuple(sorted(companions)),
                                 fact_id, observed_at, pragmatic)


def test_unseen_jargon_is_resolved_by_the_joint_chord_not_its_spelling():
    field = RelationalMusicField(tuple(sorted((
        _performance("work", "deploy", "launched",
                     ("goal:release", "role:agent", "role:patient", "system:atlas"), 1, 1),
        _performance("work", "deploy", "put into production",
                     ("goal:release", "role:agent", "role:patient", "system:atlas"), 2, 2),
        _performance("work", "visit", "stopped in",
                     ("goal:travel", "role:agent", "role:location", "place:recife"), 3, 2),
    ))))
    result = field.listen("work", "yeeted", tuple(sorted((
        "goal:release", "role:agent", "role:patient", "system:atlas"))), 3)
    assert result.state == "resolved" and result.canonical == "deploy"
    assert result.evidence_fact_ids == (1, 2)
    # The unseen surface itself is not compared with a known spelling.
    renamed = field.listen("work", "completely unrelated glyphs", tuple(sorted((
        "goal:release", "role:agent", "role:patient", "system:atlas"))), 3)
    assert renamed.canonical == result.canonical


def test_frequency_cannot_override_an_ambiguous_or_incomplete_melody():
    observations = [
        _performance("s", "buy", f"buy-{index}",
                     ("goal:procurement", "role:agent", "role:patient"), index, index)
        for index in range(1, 20)
    ]
    observations.append(_performance(
        "s", "donate", "gave away", ("goal:charity", "role:agent", "role:patient"), 30, 1))
    field = RelationalMusicField(tuple(sorted(observations)))
    result = field.listen("s", "moved", ("role:agent", "role:patient"), 40)
    assert result.state == "abstain"
    assert result.reason == "two relational melodies remain compatible"


def test_scope_clock_and_irony_are_hard_causal_boundaries():
    field = RelationalMusicField(tuple(sorted((
        _performance("alpha", "deploy", "shipped",
                     ("goal:release", "role:agent", "role:patient"), 1, 5),
        _performance("alpha", "donate", "gave",
                     ("goal:release", "role:agent", "role:patient"), 2, 20),
        _performance("beta", "visit", "landed",
                     ("goal:release", "role:agent", "role:patient"), 3, 1),
    ))))
    companions = ("goal:release", "role:agent", "role:patient")
    early = field.listen("alpha", "sent it", companions, 10)
    assert early.state == "resolved" and early.canonical == "deploy"
    late = field.listen("alpha", "sent it", companions, 25)
    assert late.state == "abstain"
    ironic = field.listen("alpha", "great deployment", companions, 10, pragmatic="ironic")
    assert ironic.state == "abstain" and "competing interpretations" in ironic.reason


def test_relational_contract_rejects_single_notes_and_label_leakage():
    with pytest.raises(ValueError):
        _performance("s", "deploy", "ship", ("goal:release",), 1, 1)
    with pytest.raises(ValueError):
        _performance("s", "deploy", "ship", ("goal:release", "predicate:deploy"), 1, 1)
