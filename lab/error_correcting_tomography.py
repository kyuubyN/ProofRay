"""Bounded-distance semantic decoder with conserved evidence groups for D30."""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations


@dataclass(frozen=True)
class SemanticCodeword:
    hypothesis_id: str
    charges: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not self.hypothesis_id or self.charges != tuple(sorted(self.charges)):
            raise ValueError("hypothesis id and canonically sorted charges are required")
        if len(dict(self.charges)) != len(self.charges) or any(not key or not value
                                                               for key, value in self.charges):
            raise ValueError("charges must be unique non-empty pairs")


@dataclass(frozen=True)
class ChargeObservation:
    field: str
    value: str
    evidence_group: str

    def __post_init__(self) -> None:
        if not self.field or not self.value or not self.evidence_group:
            raise ValueError("field, value and conserved evidence group are required")


@dataclass(frozen=True)
class CorrectingDecodeResult:
    state: str  # resolved | open | conflict
    hypothesis_id: str | None
    group_distance: int | None
    minimum_code_distance: int
    correctable_group_errors: int
    contenders: tuple[str, ...]
    reason: str


def _grouped(observations: tuple[ChargeObservation, ...]) -> dict[str, tuple[ChargeObservation, ...]]:
    fields = [item.field for item in observations]
    if len(fields) != len(set(fields)):
        raise ValueError("a semantic field may be observed only once")
    groups: dict[str, list[ChargeObservation]] = {}
    for item in observations:
        groups.setdefault(item.evidence_group, []).append(item)
    return {key: tuple(value) for key, value in groups.items()}


def _candidate_distance(codeword: SemanticCodeword,
                        groups: dict[str, tuple[ChargeObservation, ...]]) -> int:
    charges = dict(codeword.charges)
    return sum(any(charges.get(item.field) != item.value for item in observations)
               for observations in groups.values())


def _pair_distance(left: SemanticCodeword, right: SemanticCodeword,
                   groups: dict[str, tuple[ChargeObservation, ...]]) -> int:
    left_charges = dict(left.charges)
    right_charges = dict(right.charges)
    # Many fields measured from one causal source still contribute only one unit of separation.
    return sum(any(left_charges.get(item.field) != right_charges.get(item.field)
                   for item in observations) for observations in groups.values())


def decode_with_error_correction(
    codewords: tuple[SemanticCodeword, ...],
    observations: tuple[ChargeObservation, ...],
    *,
    requested_group_errors: int = 1,
) -> CorrectingDecodeResult:
    """Decode only inside a proven unique Hamming ball over independent evidence groups."""
    if len(codewords) < 2 or len({item.hypothesis_id for item in codewords}) != len(codewords):
        raise ValueError("at least two uniquely identified codewords are required")
    if not observations or requested_group_errors < 0:
        raise ValueError("observations and a non-negative error budget are required")
    fields = set(dict(codewords[0].charges))
    if any(set(dict(item.charges)) != fields for item in codewords):
        raise ValueError("all codewords must expose the same semantic fields")
    if any(item.field not in fields for item in observations):
        return CorrectingDecodeResult(
            "conflict", None, None, 0, 0, tuple(sorted(item.hypothesis_id for item in codewords)),
            "observation field is outside the codebook",
        )

    groups = _grouped(observations)
    pair_distances = tuple(_pair_distance(left, right, groups)
                           for left, right in combinations(codewords, 2))
    minimum_distance = min(pair_distances, default=0)
    correctable = max(0, (minimum_distance - 1) // 2)
    if minimum_distance < 2 * requested_group_errors + 1:
        return CorrectingDecodeResult(
            "open", None, None, minimum_distance, correctable,
            tuple(sorted(item.hypothesis_id for item in codewords)),
            "observed independent-group distance cannot support the requested correction radius",
        )

    distances = {item.hypothesis_id: _candidate_distance(item, groups) for item in codewords}
    best_distance = min(distances.values())
    best = tuple(sorted(key for key, value in distances.items() if value == best_distance))
    if best_distance > requested_group_errors:
        return CorrectingDecodeResult(
            "conflict", None, best_distance, minimum_distance, correctable, best,
            "observation lies outside every declared correction ball",
        )
    if len(best) != 1:
        return CorrectingDecodeResult(
            "open", None, best_distance, minimum_distance, correctable, best,
            "more than one codeword has the minimum conserved-group distance",
        )
    return CorrectingDecodeResult(
        "resolved", best[0], best_distance, minimum_distance, correctable, best,
        "unique codeword inside the proven correction radius",
    )
