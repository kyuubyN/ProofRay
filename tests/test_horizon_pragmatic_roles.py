# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.pragmatic_roles import observe_pragmatic_roles


def test_identity_socket_transports_identity_to_declared_surface():
    assert "identity" in observe_pragmatic_roles("What is Caroline's identity?", question=True)
    assert "identity" in observe_pragmatic_roles("The transgender stories inspired me")


def test_career_socket_connects_query_to_working_and_counseling():
    assert "occupation" in observe_pragmatic_roles("What career path did she pursue?", question=True)
    assert "occupation" in observe_pragmatic_roles("I am working in mental health counseling")


def test_opinion_emotion_and_advice_remain_distinct_sockets():
    assert "attitude" in observe_pragmatic_roles("What does Melanie think about it?", question=True)
    assert "attitude" in observe_pragmatic_roles("That is amazing and lovely")
    assert "emotion" in observe_pragmatic_roles("How did she feel?", question=True)
    assert "emotion" in observe_pragmatic_roles("I felt tiny and in awe")
    assert "counsel" in observe_pragmatic_roles("What advice did Gina give?", question=True)
    assert "counsel" in observe_pragmatic_roles("Make sure it stands out and don't forget customers")


def test_absence_of_socket_is_unknown_not_negative():
    assert observe_pragmatic_roles("The violet object is nearby") == ()
