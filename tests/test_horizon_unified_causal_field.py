# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from horizon_memory.breathing_interference import ChannelExpectation
from horizon_memory.unified_causal_field import (
    HorizonUnifiedCausalField, ProofHyperedge,
)


def _edge(canonical, channels, fact_ids, amplitude, origin, hard=False):
    return ProofHyperedge("s", canonical, tuple(sorted(channels)), tuple(sorted(fact_ids)),
                          1, amplitude, origin, hard)


def test_every_mechanism_enters_one_amplitude_without_duplicate_votes():
    field = HorizonUnifiedCausalField()
    field.begin_breath("s", 1, (
        ChannelExpectation("visit", "role:location", 90),))
    field.inhale(_edge("deploy", ("goal:release", "role:agent"), (1,), 1.0, "music"))
    # The bridge reuses FactId 1: it shortens a path but cannot add authority.
    field.inhale(_edge("deploy", ("goal:release", "phase:delivery"), (1,), 1.0, "bridge"))
    field.inhale(_edge("deploy", ("phase:delivery", "role:patient"), (2,), 1.0,
                       "latent_mediator"))
    field.inhale(_edge("visit", ("goal:travel", "role:agent"), (3,), 1.0, "music"))
    field.inhale(_edge("visit", ("goal:travel", "phase:journey"), (4,), 1.0, "bridge"))
    exhale = field.exhale("s", 99)
    deploy = next(item for item in exhale.boundary.candidates if item.canonical == "deploy")
    assert deploy.positive_witness_count == 2 and deploy.amplitude == 2.0
    result = field.resolve("s")
    assert result.state == "resolved" and result.canonical == "deploy"
    assert result.evidence_fact_ids == (1, 2)
    assert field.staged_fact_count("s") == 0


def test_inverse_boundary_and_destructive_interference_are_the_same_signed_law():
    field = HorizonUnifiedCausalField()
    field.begin_breath("s", 1)
    field.inhale(_edge("deploy", ("goal:r", "role:a"), (1,), 1.0, "music"))
    field.inhale(_edge("deploy", ("goal:r", "role:p"), (2,), 1.0, "mediator"))
    field.inhale(_edge("deploy", ("goal:r", "phase:d"), (3,), -1.0,
                       "inverse_boundary", hard=True))
    field.exhale("s", 10)
    result = field.resolve("s")
    assert result.state == "abstain" and 3 in result.evidence_fact_ids


def test_open_breath_is_not_queryable_and_hyperedges_are_joint():
    field = HorizonUnifiedCausalField()
    field.begin_breath("s", 1)
    assert field.resolve("s").reason == "no exhaled boundary"
    with pytest.raises(ValueError):
        field.inhale(ProofHyperedge("s", "deploy", ("one-note",), (1,),
                                    1, 1.0, "invalid_marginal"))
