# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from horizon_memory.breathing_interference import (
    BreathingLedger, ChannelExpectation, InterferenceWave, ObservedPulse,
    ProvenancedInterferenceField,
)


def test_silence_is_ignorance_until_exhale_certifies_completeness():
    ledger = BreathingLedger("s", 1, tuple(sorted((
        ChannelExpectation("deploy", "role:agent", 1),
        ChannelExpectation("deploy", "role:patient", 2),
    ))))
    ledger.inhale(ObservedPulse("role:agent", 10, 1))
    assert ledger.silence_before_exhale() == ()
    certificate = ledger.exhale(99)
    assert [(item.canonical, item.channel) for item in certificate.silence] == [
        ("deploy", "role:patient")]
    assert len(certificate.sha256) == 64 and ledger.exhale(99) == certificate
    with pytest.raises(ValueError):
        ledger.inhale(ObservedPulse("role:patient", 11, 2))


def test_certified_silence_is_a_hard_negative_not_a_weak_vote():
    ledger = BreathingLedger("s", 2, (
        ChannelExpectation("deploy", "role:patient", 2),))
    silence = ProvenancedInterferenceField.silence_waves(ledger.exhale(90))
    positive = (
        InterferenceWave("deploy", 100.0, (10,), "music-a"),
        InterferenceWave("deploy", 100.0, (11,), "music-b"),
    )
    result = ProvenancedInterferenceField().resolve(tuple(sorted(positive + silence)))
    assert result.state == "abstain" and result.candidates[0].excluded
    assert set(result.evidence_fact_ids) == {2, 90}


def test_independent_constructive_paths_reinforce_but_duplicate_factids_do_not():
    waves = tuple(sorted((
        InterferenceWave("deploy", 1.0, (10,), "music"),
        InterferenceWave("deploy", 1.0, (11,), "bridge"),
        InterferenceWave("visit", 9.0, (20,), "duplicated-path-a"),
        InterferenceWave("visit", 9.0, (20,), "duplicated-path-b"),
    )))
    result = ProvenancedInterferenceField(min_margin=0.5).resolve(waves)
    assert result.state == "resolved" and result.canonical == "deploy"
    assert result.evidence_fact_ids == (10, 11)


def test_destructive_interference_and_unseparated_constructive_modes_abstain():
    field = ProvenancedInterferenceField(min_margin=0.5)
    cancelled = tuple(sorted((
        InterferenceWave("deploy", 1.0, (1,), "path-a"),
        InterferenceWave("deploy", 1.0, (2,), "path-b"),
        InterferenceWave("deploy", -2.0, (3,), "contradiction"),
    )))
    assert field.resolve(cancelled).state == "abstain"
    tied = tuple(sorted((
        InterferenceWave("deploy", 1.0, (1,), "a"),
        InterferenceWave("deploy", 1.0, (2,), "b"),
        InterferenceWave("visit", 1.0, (3,), "c"),
        InterferenceWave("visit", 1.0, (4,), "d"),
    )))
    assert field.resolve(tied).reason == "constructive modes do not separate"
