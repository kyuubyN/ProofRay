# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from horizon_memory.semantic_tomography import (
    AdaptiveSyndromeDecoder, SemanticHypothesis, measurement_codebook,
    semantic_measurement_codebook,
    microcitation_ledger,
)


def _hypothesis(identifier, **charges):
    return SemanticHypothesis(identifier, tuple(sorted(charges.items())), f"program:{identifier}")


def test_microcitations_are_authoritative_and_duplicate_safe():
    ledger = microcitation_ledger("Ana left. Ana left. Bruno stayed!")
    assert [(item.citation_id, item.start, item.end, item.text) for item in ledger] == [
        ("s1", 0, 9, "Ana left."), ("s2", 10, 19, "Ana left."),
        ("s3", 20, 33, "Bruno stayed!"),
    ]
    assert ledger[0].sha256 == ledger[1].sha256 and ledger[0].start != ledger[1].start


def test_adaptive_tomography_chooses_information_and_resolves_unique_syndrome():
    decoder = AdaptiveSyndromeDecoder((
        _hypothesis("h1", operator="project", clock="event", role="agent"),
        _hypothesis("h2", operator="project", clock="event", role="patient"),
        _hypothesis("h3", operator="argmax", clock="event", role="location"),
        _hypothesis("h4", operator="argmax", clock="report", role="location"),
    ))
    first = decoder.next_measurement()
    assert first is not None and first.information_bits > 0
    decoder.observe(first, dict(_hypothesis("target", operator="argmax", clock="event",
                                            role="location").charges)[first.field])
    while decoder.result().state == "open":
        measurement = decoder.next_measurement()
        decoder.observe(measurement, dict(_hypothesis(
            "target", operator="argmax", clock="event", role="location").charges
        )[measurement.field])
    result = decoder.result()
    assert result.state == "resolved" and result.hypothesis_id == "h3"


def test_stale_measurement_and_indistinguishable_syndrome_fail_closed():
    decoder = AdaptiveSyndromeDecoder((
        _hypothesis("h1", operator="exists"), _hypothesis("h2", operator="project"),
        _hypothesis("h3", operator="project"),
    ))
    measurement = decoder.next_measurement()
    decoder.observe(measurement, "project")
    with pytest.raises(ValueError, match="stale"):
        decoder.observe(measurement, "exists")
    result = decoder.result()
    assert result.state == "abstain" and result.survivor_count == 2


def test_measurement_codebook_accepts_only_one_finite_symbol():
    decoder = AdaptiveSyndromeDecoder((
        _hypothesis("yes", polarity="positive"), _hypothesis("no", polarity="negative"),
    ))
    book = measurement_codebook("Carla did not buy the tablet.", decoder.next_measurement())
    assert book.outputs == ("CHOICE:A", "CHOICE:B")
    assert book.constraint_trigger == "CHOICE:" and book.constrained_tails == ("A", "B")
    assert book.resolve("CHOICE:A") in ("negative", "positive")
    with pytest.raises(ValueError, match="outside"):
        book.resolve("CHOICE:A\n")
    with pytest.raises(ValueError, match="glosses"):
        measurement_codebook("text", decoder.next_measurement(), {"invented": "not an option"})


def test_semantic_codebook_constrains_meaning_without_a_letter_gauge():
    decoder = AdaptiveSyndromeDecoder((
        _hypothesis("latest", operator="argmax"), _hypothesis("earliest", operator="argmin"),
    ))
    book = semantic_measurement_codebook(
        "Which happened most recently?", decoder.next_measurement(),
        {"argmax": "greatest time", "argmin": "smallest time"},
    )
    assert book.constrained_tails == ("argmax", "argmin")
    assert book.resolve("CHOICE:argmax") == "argmax"
    with pytest.raises(ValueError, match="outside"):
        book.resolve("argmax")
