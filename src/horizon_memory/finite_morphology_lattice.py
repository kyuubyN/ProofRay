# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""H-FMRL reference: finite, lossless morphosyntactic readings for H-PLT.

A reading is a licensed alternative, never a prediction or truth claim. Ambiguity is preserved
and every reading carries a replayable witness. Promoted verbatim from
`lab/finite_morphology_lattice.py` as a dependency of the Portuguese atomic-relations surface-role
bridge (`portuguese_atomic_relations.py`).
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Iterable

from .surface_atomic_kernel import SurfaceKernelToken


class MorphClass(str, Enum):
    UNKNOWN = "UNKNOWN"
    CONTENT = "CONTENT"
    NOMINAL = "NOMINAL"
    PREDICATE = "PREDICATE"
    AUX = "AUX"
    ADV = "ADV"
    DET = "DET"
    CLITIC = "CLITIC"
    REL = "REL"
    ADPOSITION = "ADPOSITION"
    COORDINATOR = "COORDINATOR"
    PUNCT = "PUNCT"
    MODIFIER = "MODIFIER"
    NUMERIC = "NUMERIC"
    SUBORDINATOR = "SUBORDINATOR"


@dataclass(frozen=True, order=True)
class MorphReading:
    token_index: int
    surface: str
    lemma: str
    morph_class: MorphClass
    features: tuple[str, ...]
    witness: str


@dataclass(frozen=True)
class TokenReadingLattice:
    token: SurfaceKernelToken
    readings: tuple[MorphReading, ...]


@dataclass(frozen=True)
class SuffixRule:
    suffix: str
    morph_class: MorphClass
    features: tuple[str, ...] = ()
    strip: int = 0

    def __post_init__(self) -> None:
        if not self.suffix or self.strip < 0:
            raise ValueError("invalid finite suffix rule")


@dataclass(frozen=True)
class FiniteMorphologySpec:
    language: str
    exact: tuple[tuple[str, tuple[MorphClass, ...]], ...]
    suffixes: tuple[SuffixRule, ...] = ()
    contractions: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def __post_init__(self) -> None:
        keys = [key for key, _ in self.exact]
        contraction_keys = [key for key, _ in self.contractions]
        if not self.language or keys != sorted(set(keys)):
            raise ValueError("exact morphology keys must be unique and sorted")
        if contraction_keys != sorted(set(contraction_keys)):
            raise ValueError("contraction keys must be unique and sorted")
        if any(not classes for _, classes in self.exact):
            raise ValueError("empty exact reading set")

    @property
    def resource_sha256(self) -> str:
        payload = {
            "language": self.language,
            "exact": [(key, [item.value for item in classes])
                      for key, classes in self.exact],
            "suffixes": [(rule.suffix, rule.morph_class.value,
                          list(rule.features), rule.strip)
                         for rule in self.suffixes],
            "contractions": list(self.contractions),
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class MorphologyLattice:
    language: str
    resource_sha256: str
    tokens: tuple[TokenReadingLattice, ...]
    complete: bool

    @property
    def reading_count(self) -> int:
        return sum(len(token.readings) for token in self.tokens)


def compile_finite_morphology(
        tokens: Iterable[SurfaceKernelToken], spec: FiniteMorphologySpec) -> MorphologyLattice:
    """Compile all licensed readings; unknown forms remain explicit alternatives."""
    exact = dict(spec.exact)
    contraction_map = dict(spec.contractions)
    lattice = []
    for token in tokens:
        surface = token.surface.casefold()
        readings: set[MorphReading] = set()
        if surface and all(character.isdigit() or character in ",." for character in surface):
            readings.add(MorphReading(token.index, token.surface, surface, MorphClass.NUMERIC, (),
                                      "unicode:numeric"))
        if surface and not any(character.isalnum() for character in surface):
            readings.add(MorphReading(token.index, token.surface, surface, MorphClass.PUNCT, (),
                                      f"unicode:punct:{surface}"))
        for morph_class in exact.get(surface, ()):
            readings.add(MorphReading(token.index, token.surface, surface, morph_class, (),
                                      f"exact:{surface}:{morph_class.value}"))
        for rule in spec.suffixes:
            if len(surface) > len(rule.suffix) + rule.strip and surface.endswith(rule.suffix):
                lemma = surface[:-rule.strip] if rule.strip else surface
                readings.add(MorphReading(
                    token.index, token.surface, lemma, rule.morph_class,
                    tuple(sorted(rule.features)), f"suffix:{rule.suffix}:{rule.morph_class.value}"))
        if surface in contraction_map:
            parts = contraction_map[surface]
            readings.add(MorphReading(
                token.index, token.surface, "+".join(parts), MorphClass.ADPOSITION,
                tuple(f"part={part}" for part in parts), f"contraction:{surface}"))
        if not readings:
            # Unknown is deliberately not coerced into a single part of speech.
            readings.update((
                MorphReading(token.index, token.surface, surface, MorphClass.UNKNOWN, (),
                             "open:unknown"),
                MorphReading(token.index, token.surface, surface, MorphClass.CONTENT, (),
                             "open:content"),
            ))
        lattice.append(TokenReadingLattice(token, tuple(sorted(readings))))
    return MorphologyLattice(spec.language, spec.resource_sha256, tuple(lattice), True)


__all__ = ["FiniteMorphologySpec", "MorphClass", "MorphologyLattice", "MorphReading",
           "SuffixRule", "TokenReadingLattice", "compile_finite_morphology"]
