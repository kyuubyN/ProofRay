# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""HGSA experimental core — causal multi-view addressing, never content authority."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, replace

from .routing import (Candidate, CandidateGenerator, CandidateList, CausalWeaveGenerator,
                      QueryEnvelope, RoutingIndex)


_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_NEGATION = frozenset(("no", "not", "never", "without", "neither", "nor", "nao", "nunca", "sem"))
_IRONY = frozenset(("sarcasm", "sarcastic", "irony", "ironic", "sarcasmo", "ironia",
                    "yeah right", "sei claro"))
_UNCERTAIN = frozenset(("maybe", "perhaps", "possibly", "talvez", "possivelmente"))
_HYPOTHETICAL = frozenset(("if", "suppose", "assuming", "se", "suponha"))
_STOP = frozenset((
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "for", "from", "how", "i",
    "in", "is", "it", "of", "on", "or", "that", "the", "this", "to", "was", "we", "what",
    "when", "where", "who", "with", "you", "e", "o", "a", "os", "as", "de", "da", "do",
    "das", "dos", "em", "no", "na", "nos", "nas", "para", "por", "que", "um", "uma",
    "como", "qual", "quando", "onde", "eu", "voce", "nos", "isso", "isto",
))
_TIME = re.compile(r"\b(?:\d{1,4}(?:[-/:]\d{1,2}){1,2}|\d+(?:[.,]\d+)?|\d{4})\b")
_IDENTIFIER = re.compile(r"`([^`]{1,80})`|\b([A-Z][A-Z0-9_-]{1,31})\b")
_QUOTED_ALIAS = re.compile(
    r"[\"'`](?P<left>[^\"'`]{1,80})[\"'`]\s*"
    r"(?:means|significa|quer\s+dizer|is\s+our\s+name\s+for|e\s+nosso\s+nome\s+para)\s*"
    r"[\"'`](?P<right>[^\"'`]{1,120})[\"'`]",
    re.IGNORECASE,
)
_SAY_ALIAS = re.compile(
    r"(?:when\s+i\s+say|quando\s+eu\s+disser)\s+[\"'`](?P<left>[^\"'`]{1,80})[\"'`]\s*"
    r"(?:i\s+mean|quero\s+dizer)\s+[\"'`](?P<right>[^\"'`]{1,120})[\"'`]",
    re.IGNORECASE,
)


def canonical(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("semantic atoms must be strings")
    folded = unicodedata.normalize("NFKC", value).casefold().strip()
    return " ".join(_TOKEN.findall(folded))


def atoms(value: str) -> tuple[str, ...]:
    normalized = canonical(value)
    return tuple(normalized.split()) if normalized else ()


@dataclass(frozen=True)
class AliasEdge:
    scope_id: int
    left: str
    right: str
    valid_from: int
    valid_until: int | None
    source: str
    confidence: float = 1.0

    def __post_init__(self):
        if self.scope_id < 0 or not canonical(self.left) or not canonical(self.right) or not self.source:
            raise ValueError("invalid alias edge")
        if self.valid_from < 0 or (self.valid_until is not None and self.valid_until < self.valid_from):
            raise ValueError("invalid alias validity")
        if not 0 <= self.confidence <= 1:
            raise ValueError("invalid alias confidence")

    def active(self, timestamp: int) -> bool:
        return self.valid_from <= timestamp and (self.valid_until is None or timestamp <= self.valid_until)


@dataclass(frozen=True)
class SemanticCapsule:
    fact_id: int
    scope_id: int
    version: int
    entities: tuple[str, ...] = ()
    relations: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    polarity: str = "unknown"       # positive | negative | mixed | unknown
    modality: str = "asserted"      # asserted | uncertain | hypothetical | quoted
    pragmatic: tuple[str, ...] = ()  # literal | ironic | idiomatic | correction | ...
    time_atoms: tuple[str, ...] = ()
    source_atoms: tuple[str, ...] = ()
    context_atoms: tuple[str, ...] = ()
    confidence: float = 1.0

    def __post_init__(self):
        if self.fact_id < 0 or self.scope_id < 0 or self.version < 1:
            raise ValueError("invalid capsule identity")
        if self.polarity not in ("positive", "negative", "mixed", "unknown"):
            raise ValueError("invalid polarity")
        if self.modality not in ("asserted", "uncertain", "hypothetical", "quoted"):
            raise ValueError("invalid modality")
        if not 0 <= self.confidence <= 1:
            raise ValueError("invalid capsule confidence")
        for view in (self.entities, self.relations, self.aliases, self.pragmatic,
                     self.time_atoms, self.source_atoms, self.context_atoms):
            if any(not canonical(atom) for atom in view):
                raise ValueError("capsule views cannot contain empty atoms")

    def views(self) -> dict[str, frozenset[str]]:
        return {
            "entity": frozenset(canonical(item) for item in self.entities),
            "relation": frozenset(canonical(item) for item in self.relations),
            "alias": frozenset(canonical(item) for item in self.aliases),
            "pragmatic": frozenset(canonical(item) for item in self.pragmatic),
            "time": frozenset(canonical(item) for item in self.time_atoms),
            "source": frozenset(canonical(item) for item in self.source_atoms),
            "context": frozenset(canonical(item) for item in self.context_atoms),
        }


@dataclass(frozen=True)
class GaugeAddress:
    fact_id: int
    score: float
    matched_views: tuple[str, ...]
    polarity_state: str
    alias_hops: int


@dataclass(frozen=True)
class GaugeAddressResult:
    addresses: tuple[GaugeAddress, ...]
    ambiguous: bool
    planner_recommended: bool
    interpretations: tuple[str, ...]


@dataclass(frozen=True)
class CapsuleExtraction:
    """Auditable write-time projections; raw spans remain the only content authority."""

    capsules: tuple[SemanticCapsule, ...]
    aliases: tuple[AliasEdge, ...]


class CausalCapsuleExtractor:
    """Conservative, model-free extraction from events available at write time.

    Alias creation deliberately requires quoted/backticked endpoints.  This sacrifices recall to keep
    accidental equivalences out of the authoritative retrieval path.  Redefinition closes the previous
    edge at the preceding causal sequence instead of globally merging meanings.
    """

    def extract(self, documents: tuple) -> CapsuleExtraction:
        ordered = sorted(documents, key=lambda item: (
            item.sequence is None, item.sequence if item.sequence is not None else 2 ** 63 - 1,
            item.fact_id,
        ))
        capsules = []
        aliases: list[AliasEdge] = []
        active_alias: dict[tuple[int, str], int] = {}
        for position, document in enumerate(ordered):
            timestamp = document.sequence if document.sequence is not None else position
            text = document.text
            token_set = set(atoms(text))
            definitions = []
            if "?" not in text:
                for pattern in (_QUOTED_ALIAS, _SAY_ALIAS):
                    definitions.extend((match.group("left"), match.group("right"))
                                       for match in pattern.finditer(text))
            for left, right in definitions:
                key = (document.scope_id, canonical(left))
                previous = active_alias.get(key)
                if previous is not None:
                    old = aliases[previous]
                    aliases[previous] = replace(old, valid_until=max(old.valid_from, timestamp - 1))
                active_alias[key] = len(aliases)
                aliases.append(AliasEdge(document.scope_id, left, right, timestamp, None,
                                         document.source, 1.0))

            identifiers = {canonical(value) for match in _IDENTIFIER.finditer(text)
                           for value in match.groups() if value and canonical(value)}
            relation_atoms = tuple(sorted(token for token in token_set
                                          if token not in _STOP and len(token) > 1))
            polarity = "negative" if token_set & _NEGATION else "positive"
            modality = "asserted"
            if token_set & _UNCERTAIN:
                modality = "uncertain"
            elif token_set & _HYPOTHETICAL:
                modality = "hypothetical"
            pragmatic = []
            normalized = canonical(text)
            if "/s" in text.casefold() or any(marker in normalized for marker in _IRONY):
                pragmatic.append("ironic")
            if definitions:
                pragmatic.append("definition")
            capsules.append(SemanticCapsule(
                fact_id=document.fact_id,
                scope_id=document.scope_id,
                version=document.version,
                entities=tuple(sorted(identifiers)),
                relations=relation_atoms,
                aliases=tuple(sorted({canonical(value) for pair in definitions for value in pair})),
                polarity=polarity,
                modality=modality,
                pragmatic=tuple(pragmatic),
                time_atoms=tuple(sorted(set(_TIME.findall(text)))),
                source_atoms=(document.source,),
                context_atoms=tuple(filter(None, (document.session_id, document.role or ""))),
            ))
        return CapsuleExtraction(tuple(capsules), tuple(aliases))


class GaugeSyndromeIndex:
    """Read-only capsule/alias index. It proposes identities and cannot validate facts."""

    def __init__(self, capsules: tuple[SemanticCapsule, ...], aliases: tuple[AliasEdge, ...] = ()):
        by_id = {}
        for capsule in capsules:
            if capsule.fact_id in by_id:
                raise ValueError("duplicate capsule fact_id")
            by_id[capsule.fact_id] = capsule
        self.capsules = tuple(sorted(capsules, key=lambda item: item.fact_id))
        self.by_id = by_id
        self.aliases = tuple(aliases)

    def _expand(self, query: QueryEnvelope) -> tuple[set[str], int]:
        expanded = set(atoms(query.text))
        query_text = canonical(query.text)
        phrases = {query_text} | set(expanded)
        hops = 0
        # A bounded fixed point admits chained jargon while preventing unbounded graph walks.
        for _ in range(3):
            changed = False
            for edge in self.aliases:
                if edge.scope_id != query.scope_id or not edge.active(query.timestamp):
                    continue
                left, right = canonical(edge.left), canonical(edge.right)
                left_present = left in phrases or f" {left} " in f" {query_text} "
                right_present = right in phrases or f" {right} " in f" {query_text} "
                if left_present and right not in phrases:
                    phrases.add(right); expanded.update(atoms(right)); changed = True
                if right_present and left not in phrases:
                    phrases.add(left); expanded.update(atoms(left)); changed = True
            if not changed:
                break
            hops += 1
        return expanded | phrases, hops

    def address(self, query: QueryEnvelope, eligible_fact_ids: set[int] | None = None,
                limit: int = 32) -> GaugeAddressResult:
        if limit < 1:
            raise ValueError("limit must be positive")
        expanded, alias_hops = self._expand(query)
        query_tokens = set(atoms(query.text))
        negative = bool(query_tokens & _NEGATION)
        normalized_query = canonical(query.text)
        ironic = "/s" in query.text.casefold() or any(marker in normalized_query for marker in _IRONY)
        interpretations = ["literal"]
        if ironic:
            interpretations.append("ironic")
        addresses = []
        for capsule in self.capsules:
            if capsule.scope_id != query.scope_id:
                continue
            if eligible_fact_ids is not None and capsule.fact_id not in eligible_fact_ids:
                continue
            matched = []
            overlap_score = 0.0
            for name, view in capsule.views().items():
                view_tokens = set(view)
                for phrase in view:
                    view_tokens.update(atoms(phrase))
                overlap = expanded & view_tokens
                if overlap:
                    matched.append(name)
                    overlap_score += min(1.0, len(overlap) / max(1, len(view_tokens)))
            polarity_state = "compatible"
            if negative and capsule.polarity == "positive":
                polarity_state = "competing"
            elif not negative and capsule.polarity == "negative":
                polarity_state = "competing"
            pragmatic_match = ironic and "ironic" in capsule.views()["pragmatic"]
            if pragmatic_match and "pragmatic" not in matched:
                matched.append("pragmatic")
                overlap_score += 1.0
            if not matched:
                continue
            quorum_bonus = 0.35 * max(0, len(set(matched)) - 1)
            conflict_penalty = 0.4 if polarity_state == "competing" and not ironic else 0.0
            score = max(0.0, (overlap_score + quorum_bonus - conflict_penalty) * capsule.confidence)
            if score:
                addresses.append(GaugeAddress(capsule.fact_id, score,
                                              tuple(sorted(set(matched))), polarity_state,
                                              alias_hops))
        addresses.sort(key=lambda item: (-item.score, -len(item.matched_views), item.fact_id))
        selected = tuple(addresses[:limit])
        top_competing = (len(selected) > 1 and selected[0].polarity_state !=
                         selected[1].polarity_state and
                         abs(selected[0].score - selected[1].score) <= 0.15)
        ambiguous = ironic or top_competing
        planner = not selected or ambiguous or len(selected[0].matched_views) < 2
        return GaugeAddressResult(selected, ambiguous, planner, tuple(interpretations))


class GaugeSyndromeGenerator(CandidateGenerator):
    channel = "gauge_syndrome"

    def __init__(self, syndrome_index: GaugeSyndromeIndex):
        self.syndrome_index = syndrome_index

    def generate(self, query, index: RoutingIndex, limit, same_session=True):
        eligible = {doc.fact_id for doc in index.eligible(query, same_session)}
        result = self.syndrome_index.address(query, eligible, limit)
        namespace = "scope_session" if same_session else "scope_fallback"
        return CandidateList(tuple(Candidate(
            address.fact_id, address.score, self.channel, rank + 1, namespace
        ) for rank, address in enumerate(result.addresses)))


class GaugeWeaveGenerator(CandidateGenerator):
    """Two-stage candidate portfolio: semantic addresses plus causal lexical coverage."""

    channel = "gauge_weave"

    def __init__(self, syndrome_index: GaugeSyndromeIndex, rrf_constant: int = 60):
        if rrf_constant < 1:
            raise ValueError("rrf_constant must be positive")
        self.gauge = GaugeSyndromeGenerator(syndrome_index)
        self.weave = CausalWeaveGenerator()
        self.rrf_constant = rrf_constant

    def generate(self, query, index, limit, same_session=True):
        depth = max(32, limit)
        channels = (
            self.gauge.generate(query, index, depth, same_session),
            self.weave.generate(query, index, depth, same_session),
        )
        scores: dict[int, float] = {}
        namespaces: dict[int, str] = {}
        for candidates in channels:
            for candidate in candidates.candidates:
                scores[candidate.fact_id] = scores.get(candidate.fact_id, 0.0) + (
                    1.0 / (self.rrf_constant + candidate.rank))
                namespaces[candidate.fact_id] = candidate.namespace
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:limit]
        return CandidateList(tuple(Candidate(
            fact_id, score, self.channel, rank + 1, namespaces[fact_id]
        ) for rank, (fact_id, score) in enumerate(ranked)))
