# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.conserved_slot_decoder import ConservedSlotSpanDecoder


def test_unique_year_and_person_slots_resolve_literally():
    decoder = ConservedSlotSpanDecoder()
    document = "The American Revolutionary War ended in 1783. Another event happened in 1800."
    result = decoder.decode("In what year did the American Revolutionary War end?", document,
                            minimum_coverage=.7, minimum_sentence_margin=.1)
    assert (result.state, result.value) == ("resolved", "1783")
    assert document[result.source_span[0]:result.source_span[1]] == result.value
    document = "Hugh Grant starred as Chopin in Impromptu."
    result = decoder.decode("Who starred as Chopin in Impromptu?", document,
                            minimum_coverage=.7, minimum_sentence_margin=0)
    assert (result.state, result.value) == ("resolved", "Hugh Grant")


def test_ambiguous_slots_and_unsupported_questions_fail_closed():
    decoder = ConservedSlotSpanDecoder()
    result = decoder.decode("When did it happen?", "It happened in 1900 and 1901.",
                            minimum_coverage=0, minimum_sentence_margin=0)
    assert result.state == "abstain"
    assert decoder.decode("What happened?", "Something happened.").state == "unsupported"
