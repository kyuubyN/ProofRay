# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from horizon_memory.flow_regulator import (
    CausalFlowRegulator, FlowPulse, ObservableChallenge, VerifiedSkillState,
)


def _challenge(level):
    return ObservableChallenge("q", 10, 20, level, level, level, level)


def _skill(level):
    return VerifiedSkillState("solo", "identity", level, level, level, 5, (99,))


def _pulses(*, final_coverage=1, final_error=.1, contradiction=False):
    return (FlowPulse(0, 11, .5, .3, .2, (1,)),
            FlowPulse(1, 12, final_coverage, final_error, .4, (1, 2), contradiction))


def test_matched_challenge_and_skill_enter_flow_and_close_solo():
    result = CausalFlowRegulator().regulate(_challenge(.7), _skill(.7), _pulses())
    assert result.state == "flow"
    assert result.action == "commit_solo"
    assert result.maximum_recruits == 0


def test_flow_continues_without_claim_when_slots_are_incomplete():
    result = CausalFlowRegulator().regulate(
        _challenge(.7), _skill(.7), _pulses(final_coverage=.8))
    assert result.state == "flow"
    assert result.action == "continue_solo"


def test_challenge_above_verified_skill_recruits_specialists():
    result = CausalFlowRegulator().regulate(_challenge(.9), _skill(.4), _pulses())
    assert result.state == "anxiety"
    assert result.maximum_recruits == 3


def test_excess_skill_delegates_to_cheaper_lineage():
    result = CausalFlowRegulator().regulate(_challenge(.2), _skill(.9), _pulses())
    assert result.state == "boredom"
    assert result.action == "delegate_cheaper"


def test_growing_prediction_error_breaks_apparent_flow():
    pulses = (FlowPulse(0, 11, .5, .1, .2, (1,)),
              FlowPulse(1, 12, .8, .2, .4, (1, 2)))
    result = CausalFlowRegulator().regulate(_challenge(.7), _skill(.7), pulses)
    assert result.state == "rupture"
    assert result.action == "recruit_or_abstain"


def test_contradiction_forces_silence_even_in_resonance():
    result = CausalFlowRegulator().regulate(
        _challenge(.7), _skill(.7), _pulses(contradiction=True))
    assert result.state == "rupture"
    assert result.action == "abstain"


def test_current_query_cannot_instantly_inflate_skill():
    skill = VerifiedSkillState("solo", "identity", .9, .9, .9, 10, (99,))
    with pytest.raises(ValueError):
        CausalFlowRegulator().regulate(_challenge(.9), skill, _pulses())
