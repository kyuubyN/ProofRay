import pytest

from lab.constellation_constraints import (
    ConstellationEdge,
    ProgramCandidate,
    program_delta,
    propagate_constellation_constraints,
)


def candidate(name, **charges):
    return ProgramCandidate(name, tuple(sorted(charges.items())))


def test_program_delta_is_structural_not_a_score():
    first = candidate("first", operator="count", scope="first", predicate="touchdown")
    second = candidate("second", operator="count", scope="second", predicate="touchdown")
    assert program_delta(first, second) == (("scope", "first", "second"),)


def test_recurrent_generator_removes_locally_plausible_spurious_programs():
    # World 1 alone is ambiguous: first->second might change scope or operator.  World 2 permits only
    # the scope change, so covariance removes the spurious operator-changing pair in world 1.
    domains = {
        "w1:first": (
            candidate("w1-f-count", operator="count", scope="first"),
            candidate("w1-f-sum", operator="sum", scope="first"),
        ),
        "w1:second": (
            candidate("w1-s-count", operator="count", scope="second"),
            candidate("w1-s-diff", operator="difference", scope="second"),
        ),
        "w2:first": (candidate("w2-f", operator="count", scope="first"),),
        "w2:second": (candidate("w2-s", operator="count", scope="second"),),
    }
    edges = (
        ConstellationEdge("w1:first", "w1:second", "first->second"),
        ConstellationEdge("w2:first", "w2:second", "first->second"),
    )
    result = propagate_constellation_constraints(domains, edges)
    assert result.state == "resolved"
    assert dict(result.question_domains)["w1:first"] == ("w1-f-count",)
    assert dict(result.question_domains)["w1:second"] == ("w1-s-count",)


def test_incompatible_worlds_fail_closed_instead_of_voting():
    domains = {
        "a": (candidate("a", operator="count", scope="first"),),
        "b": (candidate("b", operator="count", scope="second"),),
        "c": (candidate("c", operator="count", scope="first"),),
        "d": (candidate("d", operator="sum", scope="first"),),
    }
    edges = (
        ConstellationEdge("a", "b", "first->second"),
        ConstellationEdge("c", "d", "first->second"),
    )
    result = propagate_constellation_constraints(domains, edges)
    assert result.state == "conflict"
    assert "no covariant program delta" in result.reason


def test_unbroken_symmetry_stays_open():
    domains = {
        "a": (candidate("a1", operator="count"), candidate("a2", operator="sum")),
        "b": (candidate("b1", operator="count"), candidate("b2", operator="sum")),
    }
    result = propagate_constellation_constraints(
        domains, (ConstellationEdge("a", "b", "identity-like"),))
    assert result.state == "open"


def test_missing_endpoint_is_rejected():
    with pytest.raises(ValueError):
        propagate_constellation_constraints(
            {"a": (candidate("a", operator="count"),)},
            (ConstellationEdge("a", "missing", "x"),),
        )
