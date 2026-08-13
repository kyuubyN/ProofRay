import itertools

import pytest

from lab.error_correcting_tomography import (
    ChargeObservation,
    SemanticCodeword,
    decode_with_error_correction,
)


def word(name, **charges):
    return SemanticCodeword(name, tuple(sorted(charges.items())))


CODEBOOK = (
    word("count", operator="count", role="event", scope="closed"),
    word("sum", operator="sum", role="quantity", scope="all"),
)


def observations(operator="count", role="event", scope="closed", *, same_group=False):
    return tuple(
        ChargeObservation(field, value, "one-sensor" if same_group else f"sensor:{field}")
        for field, value in (("operator", operator), ("role", role), ("scope", scope))
    )


def test_one_independent_error_is_corrected_at_distance_three():
    result = decode_with_error_correction(
        CODEBOOK, observations(role="quantity"), requested_group_errors=1)
    assert result.state == "resolved"
    assert result.hypothesis_id == "count"
    assert result.group_distance == 1
    assert result.minimum_code_distance == 3


def test_every_single_group_error_is_corrected_exhaustively():
    truth = dict(CODEBOOK[0].charges)
    other = dict(CODEBOOK[1].charges)
    for wrong_field in truth:
        observed = tuple(ChargeObservation(
            field, other[field] if field == wrong_field else truth[field], f"sensor:{field}")
            for field in sorted(truth))
        result = decode_with_error_correction(CODEBOOK, observed, requested_group_errors=1)
        assert result.state == "resolved"
        assert result.hypothesis_id == "count"


def test_correlated_repetition_does_not_manufacture_distance():
    result = decode_with_error_correction(
        CODEBOOK, observations(same_group=True), requested_group_errors=1)
    assert result.state == "open"
    assert result.minimum_code_distance == 1
    assert result.correctable_group_errors == 0


def test_insufficient_number_of_independent_measurements_stays_open():
    result = decode_with_error_correction(
        CODEBOOK,
        (ChargeObservation("operator", "count", "direct"),),
        requested_group_errors=1,
    )
    assert result.state == "open"


def test_outside_all_correction_balls_is_conflict():
    result = decode_with_error_correction(
        CODEBOOK, observations(operator="unknown", role="unknown", scope="unknown"),
        requested_group_errors=1)
    assert result.state == "conflict"


def test_unknown_field_fails_closed():
    result = decode_with_error_correction(
        CODEBOOK,
        (ChargeObservation("unknown", "x", "sensor"),),
        requested_group_errors=0,
    )
    assert result.state == "conflict"


def test_duplicate_field_cannot_be_counted_twice():
    with pytest.raises(ValueError):
        decode_with_error_correction(CODEBOOK, (
            ChargeObservation("operator", "count", "a"),
            ChargeObservation("operator", "count", "b"),
        ))


def test_two_errors_are_not_silently_corrected_by_radius_one():
    for wrong_fields in itertools.combinations(("operator", "role", "scope"), 2):
        truth = dict(CODEBOOK[0].charges)
        other = dict(CODEBOOK[1].charges)
        observed = tuple(ChargeObservation(
            field, other[field] if field in wrong_fields else truth[field], f"sensor:{field}")
            for field in sorted(truth))
        result = decode_with_error_correction(CODEBOOK, observed, requested_group_errors=1)
        assert result.hypothesis_id != "count"
