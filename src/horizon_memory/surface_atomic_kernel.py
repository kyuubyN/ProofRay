# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Language-neutral finite SVO reading kernel used by deterministic language packs."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


class AtomicDemand(Protocol):
    predicate: str
    answer_role: str
    answer_type: str
    known_role: str
    known_value: str


@dataclass(frozen=True, order=True)
class SurfaceKernelToken:
    index: int
    surface: str
    span: tuple[int, int]


@dataclass(frozen=True, order=True)
class SurfaceKernelReading:
    candidate: str
    span: tuple[int, int]
    predicate_span: tuple[int, int]
    known_span: tuple[int, int]
    rule: str


@dataclass(frozen=True)
class SurfaceSvoConfig:
    person_pronouns: frozenset[str]
    skip: frozenset[str]
    determiners: frozenset[str]
    object_pronouns: frozenset[str]
    ditransitive_predicates: frozenset[str]
    adjectival_suffixes: tuple[str, ...]
    fronted_what: frozenset[str]
    fronted_who: frozenset[str]
    fronted_where: frozenset[str]
    article_gap: frozenset[str]
    coordinators: frozenset[str]
    together_markers: frozenset[str]
    suppress_all_caps_person: bool = True
    adverb_suffix: str = "ly"
    clause_boundaries: frozenset[str] = frozenset({".", "?", "!", ";", ":"})
    # Closed-class spelled-out cardinal number words ("five", "hundred") for the quantity
    # head-shift below. Empty by default -- PT's own `select_svo_readings` caller passes nothing
    # here, so this stays a byte-for-byte no-op for it; only a caller that explicitly populates
    # this set opts into the shift.
    numeral_words: frozenset[str] = frozenset()
    # A separate, narrower set of numeral words allowed ONLY as the prefix of a hyphenated compound
    # ("one-tap", "45-pound" -- the digit case needs no entry here, digits are checked directly).
    # Kept apart from `numeral_words` because a word may be an unambiguous numeral only when
    # hyphen-prefixed and NOT as a bare standalone token (English "one" is also the indefinite
    # pronoun head in "the one that got away" -- unsafe to treat as skippable on its own, but
    # never ambiguous when it is the first part of a hyphenated compound like "one-tap").
    numeral_hyphen_prefixes: frozenset[str] = frozenset()
    # Passive voice: a closed, finite set of "be"-auxiliary forms. Empty by default -- no existing
    # caller (PT) populates this, so this stays a byte-for-byte no-op for every caller that doesn't
    # opt in. When non-empty, `select_svo_readings` checks whether the matched predicate token is
    # immediately preceded by a maximal run of these forms; if so, the clause is passive and the
    # ARG1/ARG2 search directions below are swapped accordingly (see the dedicated branch). This
    # is a grammatical fact about a closed word class, not a per-verb lexicon -- English has
    # exactly these forms of "be", full stop.
    passive_auxiliaries: frozenset[str] = frozenset()
    # The marker introducing a passive agent phrase ("by", as in "...was broken BY John"). Also a
    # closed, single-member class in English. Empty by default alongside `passive_auxiliaries`.
    passive_agent_markers: frozenset[str] = frozenset()


def select_svo_readings(tokens: tuple[SurfaceKernelToken, ...], demand: AtomicDemand, *,
                        lemma: Callable[[SurfaceKernelToken], str],
                        predicate_forms: Callable[[str], frozenset[str]],
                        config: SurfaceSvoConfig,
                        candidate_allowed: Callable[[SurfaceKernelToken, AtomicDemand], bool] | None = None,
                        ) -> tuple[SurfaceKernelReading, ...]:
    """Select finite active-order role readings; ambiguity remains a set, never a score."""
    wanted = predicate_forms(demand.predicate)
    predicates = [token for token in tokens if predicate_forms(lemma(token)) & wanted]
    known = [token for token in tokens if lemma(token) == demand.known_value]

    def usable(token: SurfaceKernelToken) -> bool:
        value = lemma(token)
        if candidate_allowed is not None and not candidate_allowed(token, demand):
            return False
        if value in config.skip or value == demand.known_value or predicate_forms(value) & wanted:
            return False
        if config.adverb_suffix and len(value) > 4 and value.endswith(config.adverb_suffix):
            return False
        if demand.answer_type == "who":
            capitalized = token.surface[:1].isupper()
            if config.suppress_all_caps_person:
                capitalized = capitalized and not token.surface.isupper()
            return capitalized or value in config.person_pronouns
        return True

    def modifier_like(value: str) -> bool:
        return value in config.together_markers or any(
            len(value) > len(suffix) + 2 and value.endswith(suffix)
            for suffix in config.adjectival_suffixes)

    def is_numeral(value: str) -> bool:
        if value in config.numeral_words:
            return True
        stripped = value.replace(",", "").replace(".", "")
        if stripped and stripped.isdigit():
            return True
        prefix, sep, _rest = value.partition("-")
        return bool(sep) and (
            prefix in config.numeral_hyphen_prefixes or
            (bool(prefix) and prefix.isdigit()))

    def phrase_head(sequence: list[SurfaceKernelToken]) -> SurfaceKernelToken | None:
        if not sequence:
            return None
        active = sequence
        first = lemma(active[0])
        # Quantity head-shift: "five headshots"/"1,000 points" -- the true head sits AFTER a
        # (possibly multi-token, e.g. "one hundred") cardinal-number span, never on the number
        # itself. Opt-in via `config.numeral_words` (empty for every existing caller, so this loop
        # never executes unless a caller explicitly populates it) -- a digit-only token still needs
        # that same non-empty set to activate the shift, so a caller that never asks for numeral
        # handling keeps its exact prior byte-for-byte behavior. Only steps across strictly
        # adjacent original-token positions, so a filtered-out token in between (e.g. a comma) does
        # not bridge two otherwise-unrelated numbers together.
        if config.numeral_words:
            while (len(active) > 1 and is_numeral(first) and
                   active[1].index == active[0].index + 1):
                active = active[1:]
                first = lemma(active[0])
        if (first in config.object_pronouns and len(active) > 1 and
                wanted & config.ditransitive_predicates):
            following = lemma(active[1])
            between = [lemma(token) for token in tokens
                       if active[0].index < token.index < active[1].index]
            if (not modifier_like(following) and
                    all(value in config.article_gap for value in between)):
                active = active[1:]
                first = lemma(active[0])
        if first in config.determiners and len(active) > 1:
            following = lemma(active[1])
            if active[1].index == active[0].index + 1 and not modifier_like(following):
                return active[1]
        return active[0]

    by_index = {token.index: token for token in tokens}

    def passive_auxiliary_run_start(predicate: SurfaceKernelToken) -> int | None:
        """Index of the first token in the maximal run of passive-auxiliary tokens
        immediately preceding `predicate` ("was being broken" -> the "was" index), or None if
        the token directly before `predicate` isn't one (active voice, or no config opt-in)."""
        if not config.passive_auxiliaries:
            return None
        idx = predicate.index - 1
        start = None
        while idx in by_index and lemma(by_index[idx]) in config.passive_auxiliaries:
            start = idx
            idx -= 1
        return start

    readings = []
    for predicate in predicates:
        clause_left = max(
            (token.index for token in tokens
             if token.index < predicate.index and lemma(token) in config.clause_boundaries),
            default=-1)
        clause_right = min(
            (token.index for token in tokens
             if token.index > predicate.index and lemma(token) in config.clause_boundaries),
            default=len(tokens))
        passive_aux_start = passive_auxiliary_run_start(predicate)
        for known_token in known:
            if not clause_left < known_token.index < clause_right:
                continue
            candidate = None
            rule = ""
            if passive_aux_start is not None and demand.answer_role == "ARG1":
                # The agent of a passive clause, if expressed at all, is introduced by "by" and
                # sits AFTER the predicate -- never left of it the way an active-voice subject
                # would. No "by"-phrase means no expressed agent: abstain (candidate stays None)
                # rather than fall back to the active-voice rule, which would answer with
                # whatever happens to sit left of the auxiliary -- not the semantic agent.
                markers = [token for token in tokens
                           if predicate.index < token.index < clause_right and
                           lemma(token) in config.passive_agent_markers]
                if markers:
                    after_marker = [token for token in tokens
                                    if markers[0].index < token.index < clause_right and
                                    usable(token)]
                    if after_marker:
                        candidate, rule = phrase_head(after_marker), "passive_by_phrase_agent"
            elif passive_aux_start is not None and demand.answer_role == "ARG2":
                # The surface subject of a passive clause is the semantic patient -- it sits
                # left of the auxiliary run, in exactly the position an active-voice subject
                # would occupy relative to the verb.
                before_aux = [token for token in tokens
                              if clause_left < token.index < passive_aux_start and usable(token)]
                if before_aux:
                    candidate, rule = before_aux[-1], "passive_surface_subject_patient"
            elif demand.answer_role == "ARG1":
                before = [token for token in tokens
                          if clause_left < token.index < predicate.index and usable(token)]
                if before:
                    candidate, rule = before[-1], "nearest_left_subject"
            elif demand.answer_role == "ARG2":
                fronted_lemmas = (config.fronted_what if demand.answer_type == "what" else
                                   config.fronted_who if demand.answer_type == "who" else
                                   config.fronted_where)
                fronted = [token for token in tokens if token.index < known_token.index and
                           clause_left < token.index < clause_right and
                           lemma(token) in fronted_lemmas]
                if fronted:
                    wh = fronted[-1]
                    complements = [token for token in tokens
                                   if wh.index < token.index < known_token.index and usable(token)]
                    candidate = phrase_head(complements) or wh
                    rule = ("fronted_interrogative_nominal" if complements else
                            "fronted_interrogative_object")
                else:
                    coordinated_predicates = {
                        token.index + 1 for token in tokens
                        if token.index == predicate.index + 1 and
                        lemma(token) in config.coordinators}
                    # In V-S-O languages the known subject can itself follow the
                    # predicate.  The object must then start after that binding,
                    # not merely after the verb.  For ordinary S-V-O this reduces
                    # to the previous predicate boundary.
                    object_right = max(predicate.index, known_token.index)
                    after = [token for token in tokens
                             if object_right < token.index < clause_right and
                             usable(token) and
                             token.index not in coordinated_predicates]
                    if after:
                        candidate, rule = phrase_head(after), "nearest_right_object"
            if candidate is not None:
                readings.append(SurfaceKernelReading(
                    lemma(candidate), candidate.span, predicate.span, known_token.span, rule))
    return tuple(sorted(set(readings)))


__all__ = ["AtomicDemand", "SurfaceKernelReading", "SurfaceKernelToken", "SurfaceSvoConfig",
           "select_svo_readings"]
