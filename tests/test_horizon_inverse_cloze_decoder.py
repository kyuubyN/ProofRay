# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.inverse_cloze_decoder import InverseClozeSpanDecoder


def test_typed_candidates_are_literal_and_high_margin_can_resolve():
    decoder = InverseClozeSpanDecoder()
    document = "In September 1828, Frédéric visited Berlin with Feliks Jarocki."
    question = "Who did Frédéric visit Berlin with in September 1828?"
    candidates = decoder.candidates(question, document)
    assert candidates and all(document[item.source_span[0]:item.source_span[1]] == item.text
                              for item in candidates)
    assert any(item.text == "Feliks Jarocki" for item in candidates)


def test_unknown_question_type_abstains_and_corrupt_threshold_cannot_answer():
    decoder = InverseClozeSpanDecoder()
    result = decoder.decode("What happened?", ("Something happened.",),
                            threshold=.5, margin=.1)
    assert result.state == "unsupported"
    result = decoder.decode("When did it happen?", ("It happened in 1413.",),
                            threshold=2.0, margin=1.0)
    assert result.state == "abstain"
