# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.inverse_boundary import InverseBoundaryField
from horizon_memory.latent_relational_dynamics import RelationalSeparation


def test_inverse_boundary_emits_only_causal_scoped_incident_nogoods():
    boundaries = tuple(sorted((
        RelationalSeparation("s", "goal:a", "phase:x", 1, 3, reason="redefinition"),
        RelationalSeparation("s", "goal:b", "phase:y", 2, 8, reason="goal_exit"),
        RelationalSeparation("other", "goal:a", "phase:z", 3, 1, reason="scope_boundary"),
    )))
    field = InverseBoundaryField(boundaries)
    certificate = field.emit("s", 5, ("goal:a", "role:agent"))
    assert certificate.evidence_fact_ids == (1,)
    assert certificate.forbids("phase:x", "goal:a")
    assert not certificate.forbids("goal:b", "phase:y")


def test_expired_inverse_boundary_stops_emitting_but_remains_auditable_history():
    boundary = RelationalSeparation(
        "s", "goal:a", "phase:x", 4, 3, valid_until=5, reason="temporary_negation")
    field = InverseBoundaryField((boundary,))
    assert field.emit("s", 5, ("goal:a",)).evidence_fact_ids == (4,)
    assert not field.emit("s", 6, ("goal:a",)).excluded_edges
