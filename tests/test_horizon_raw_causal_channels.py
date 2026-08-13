# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.raw_causal_channels import (
    RawCausalDocument, RawCausalSyndromeIndex, observe_raw_text,
)


def test_raw_observables_preserve_charges_and_pragmatic_phase():
    value = observe_raw_text("Maya might not visit Porto on Monday with 3 friends.")
    assert value.polarity == "negative"
    assert value.modality == "modal"
    assert value.numbers == ("3",)
    assert value.temporal == ("monday",)
    assert "porto" in value.entities


def test_morphological_surface_projects_without_a_dictionary():
    index = RawCausalSyndromeIndex((
        RawCausalDocument(1, "Maya schedules the production deployment.", 0, 0),
        RawCausalDocument(2, "Liam bought a wooden table.", 0, 1),
    ))
    ranked = index.rank(index.components("Who deployed to production?"),
                        (1.0, 0.5, 0.5, 0.25, 0.5, 1.0))
    assert ranked[0].fact_id == 1
    assert ranked[0].sublexical > ranked[1].sublexical


def test_contradictory_declared_number_repels_but_absence_is_unknown():
    index = RawCausalSyndromeIndex((
        RawCausalDocument(1, "The team ordered 7 sensors.", 0, 0),
        RawCausalDocument(2, "The team ordered sensors.", 0, 1),
        RawCausalDocument(3, "The team ordered 9 sensors.", 0, 2),
    ))
    ranked = index.rank(index.components("Did the team order 7 sensors?"),
                        (1.0, 0.0, 0.0, 0.0, 0.0, 2.0))
    assert [item.fact_id for item in ranked] == [1, 2, 3]
    assert ranked[-1].contradiction == 1.0


def test_one_amplitude_contains_all_channels_not_independent_rank_votes():
    index = RawCausalSyndromeIndex((
        RawCausalDocument(1, "Nora visits Lisbon on Monday.", 0, 0),
        RawCausalDocument(2, "Nora considers a journey.", 0, 1),
    ))
    weights = (1.0, 0.5, 0.5, 0.25, 0.5, 1.0)
    result = index.rank(index.components("When did Nora visit Lisbon?"), weights)[0]
    expected = (weights[0] * result.lexical + weights[1] * result.sublexical +
                weights[2] * result.entity + weights[3] * result.relation +
                weights[4] * result.observable - weights[5] * result.contradiction)
    assert result.amplitude == expected
