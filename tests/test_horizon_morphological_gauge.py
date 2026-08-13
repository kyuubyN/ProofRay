# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.morphological_gauge import observe_gauge_lexical

def test_silent_e_transport_is_covariant_for_addressing():
    assert observe_gauge_lexical("bake") & observe_gauge_lexical("baked")
    assert observe_gauge_lexical("archive") & observe_gauge_lexical("archived")

def test_unrelated_verbs_do_not_share_the_declared_orbit():
    assert not (observe_gauge_lexical("bake") & observe_gauge_lexical("repair"))

def test_numeric_address_is_not_treated_as_a_doubled_consonant():
    assert "000" in observe_gauge_lexical("record-000")
