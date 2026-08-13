# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.breathing_interference import (
    BreathingLedger, ChannelExpectation, InterferenceWave,
)
from horizon_memory.materialized_interference import (
    MaterializedInterferenceField,
)


def test_query_scans_candidates_not_historical_waves_and_proof_stays_bounded():
    field = MaterializedInterferenceField(proof_width=3)
    for fact_id in range(10_000):
        field.inhale("s", InterferenceWave("deploy", 1.0, (fact_id,), "music"))
    field.inhale("s", InterferenceWave("visit", 1.0, (20_001,), "music"))
    field.inhale("s", InterferenceWave("visit", 1.0, (20_002,), "bridge"))
    boundary = field.exhale("s", 30_000)
    result = field.resolve("s")
    assert result.state == "resolved" and result.canonical == "deploy"
    assert result.scanned_candidates == 2 and len(result.evidence_fact_ids) == 3
    assert boundary.candidates[0].positive_witness_count == 10_000
    assert field.staged_fact_count("s") == 0


def test_duplicate_factid_does_not_inflate_materialized_amplitude():
    field = MaterializedInterferenceField()
    field.inhale("s", InterferenceWave("deploy", 1.0, (1,), "a"))
    field.inhale("s", InterferenceWave("deploy", 1.0, (1,), "duplicate-route"))
    field.inhale("s", InterferenceWave("deploy", 1.0, (2,), "b"))
    boundary = field.exhale("s", 10)
    candidate = boundary.candidates[0]
    assert candidate.positive_witness_count == 2 and candidate.amplitude == 2.0


def test_certified_silence_enters_only_on_exhale_and_blocks_snapshot():
    breath = BreathingLedger("s", 1, (
        ChannelExpectation("deploy", "role:patient", 50),))
    field = MaterializedInterferenceField()
    field.inhale("s", InterferenceWave("deploy", 2.0, (1,), "a"))
    field.inhale("s", InterferenceWave("deploy", 2.0, (2,), "b"))
    assert field.exhale("s", 80).candidates[0].hard_negative_witnesses == ()
    certificate = breath.exhale(90)
    field.inhale_certified_silence(certificate)
    field.exhale("s", 91)
    result = field.resolve("s")
    assert result.state == "abstain" and set(result.evidence_fact_ids) == {50, 90}


def test_old_correction_is_an_append_only_destructive_wave_in_the_next_generation():
    field = MaterializedInterferenceField(min_margin=0.5)
    for fact_id in (1, 2, 3):
        field.inhale("s", InterferenceWave("deploy", 1.0, (fact_id,), "music"))
    for fact_id in (4, 5):
        field.inhale("s", InterferenceWave("visit", 1.0, (fact_id,), "music"))
    first = field.exhale("s", 10)
    assert field.resolve("s").canonical == "deploy"
    with __import__("pytest").raises(ValueError):
        field.retract("s", 1)
    field.inhale("s", InterferenceWave("deploy", -1.0, (6,), "correction"))
    # Published generation remains immutable until the next exhale.
    assert field.resolve("s").canonical == "deploy"
    second = field.exhale("s", 11)
    assert second.generation == first.generation + 1
    assert field.resolve("s").state == "abstain"


def test_unpublished_fact_can_be_retracted_before_exhale():
    field = MaterializedInterferenceField()
    field.inhale("s", InterferenceWave("deploy", 1.0, (1,), "tentative"))
    assert field.staged_fact_count("s") == 1
    field.retract("s", 1)
    assert field.staged_fact_count("s") == 0
