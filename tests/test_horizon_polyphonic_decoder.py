# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.inverse_boundary import InverseBoundaryField
from horizon_memory.latent_relational_dynamics import (
    LatentRelationalField, RelationalSeparation,
)
from horizon_memory.polyphonic_decoder import PolyphonicGaugeDecoder
from horizon_memory.relational_music import (
    RelationalMusicField, RelationalPerformance,
)


def _p(canonical, companions, fact_id):
    return RelationalPerformance("s", canonical, f"x-{fact_id}",
                                 tuple(sorted(companions)), fact_id, fact_id)


def test_inverse_precedes_direct_and_latent_is_only_fallback():
    direct_performances = tuple(sorted((
        _p("buy", ("goal:p", "role:a", "role:b"), 1),
        _p("visit", ("goal:t", "role:a", "role:l"), 2),
    )))
    no_boundary = PolyphonicGaugeDecoder(
        InverseBoundaryField(()), RelationalMusicField(direct_performances),
        LatentRelationalField(direct_performances))
    direct = no_boundary.listen("s", "unknown", ("goal:p", "role:a", "role:b"), 9)
    assert direct.state == "resolved" and direct.channel == "direct_music"

    latent_performances = tuple(sorted((
        _p("deploy", ("goal:r", "phase:d"), 3),
        _p("deploy", ("role:a", "phase:d"), 4),
        _p("deploy", ("role:p", "phase:d"), 5),
    )))
    latent_decoder = PolyphonicGaugeDecoder(
        InverseBoundaryField(()), RelationalMusicField(latent_performances),
        LatentRelationalField(latent_performances))
    latent = latent_decoder.listen("s", "unknown", ("goal:r", "role:a", "role:p"), 9)
    assert latent.state == "resolved" and latent.channel == "latent_mediator"

    boundary = RelationalSeparation("s", "goal:r", "phase:d", 90, 6)
    blocked = PolyphonicGaugeDecoder(
        InverseBoundaryField((boundary,)), RelationalMusicField(latent_performances),
        LatentRelationalField(latent_performances, (boundary,)))
    result = blocked.listen("s", "unknown", ("goal:r", "role:a", "role:p"), 9)
    assert result.state == "abstain" and result.channel == "inverse_boundary"
    assert result.evidence_fact_ids == (90,)
