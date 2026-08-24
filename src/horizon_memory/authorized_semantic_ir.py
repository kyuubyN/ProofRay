# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D42 parser-neutral, span-authorized semantic IR and Sigma-PBA transport."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from typing import Iterable

from .sigma_pba import (
    AuthorizedFact, ConjunctiveProgram, RelationalGoal, SealedSource,
    SigmaPBAExecutor, SigmaPBAResult, is_variable,
)


_PREDICATE = re.compile(r"[a-z][a-z0-9_]*")
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.:-]+")
_RULE = "d42.authorized-semantic-ir.v1"
_NORMALIZERS = frozenset({"identity.v1", "casefold.v1", "integer.v1"})
_KINDS = frozenset({"entity", "literal", "number", "time", "variable"})
_POLARITIES = frozenset({"positive", "negative"})
_MODALITIES = frozenset({"asserted", "reported", "hypothetical", "uncertain"})


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _normalize(surface: str, rule: str) -> str:
    if rule == "identity.v1":
        return surface
    if rule == "casefold.v1":
        return surface.casefold()
    if rule == "integer.v1":
        if not re.fullmatch(r"[+-]?\d+(?:,\d{3})*", surface.strip()):
            raise ValueError("integer normalization requires a canonical integer surface")
        return str(int(surface.replace(",", "")))
    raise ValueError("unknown D42 normalization rule")


@dataclass(frozen=True, order=True)
class SemanticSource:
    source_id: str
    content: str
    sha256: str
    scope: str

    @classmethod
    def seal(cls, source_id: str, content: str, scope: str) -> "SemanticSource":
        if not source_id or not content or not scope or len(content) > 1_000_000:
            raise ValueError("bounded source identity, content and scope are required")
        return cls(source_id, content, hashlib.sha256(content.encode()).hexdigest(), scope)

    def verify(self) -> bool:
        return bool(self.source_id and self.content and self.scope and
                    hashlib.sha256(self.content.encode()).hexdigest() == self.sha256)

    def as_sigma_source(self) -> SealedSource:
        source = SealedSource.seal(self.source_id, self.content)
        if source.sha256 != self.sha256:
            raise ValueError("semantic source seal mismatch")
        return source


@dataclass(frozen=True, order=True)
class SemanticTerm:
    surface: str
    canonical: str
    kind: str
    source_span: tuple[int, int] | None
    normalization_rule: str = "identity.v1"

    @classmethod
    def anchored(cls, source: SemanticSource, start: int, end: int, *, kind: str,
                 normalization_rule: str = "identity.v1") -> "SemanticTerm":
        if not source.verify() or not (0 <= start < end <= len(source.content)):
            raise ValueError("term span is outside its sealed source")
        surface = source.content[start:end]
        return cls(surface, _normalize(surface, normalization_rule), kind,
                   (start, end), normalization_rule)

    @classmethod
    def variable(cls, name: str) -> "SemanticTerm":
        if not is_variable(name):
            raise ValueError("D42 variables must use the Sigma-PBA variable grammar")
        return cls("", name, "variable", None, "identity.v1")

    def verify(self, source: SemanticSource, *, allow_variable: bool) -> bool:
        if self.kind not in _KINDS or self.normalization_rule not in _NORMALIZERS:
            return False
        if self.kind == "variable":
            return bool(allow_variable and not self.surface and self.source_span is None and
                        is_variable(self.canonical))
        if self.source_span is None or not self.surface or not self.canonical:
            return False
        start, end = self.source_span
        try:
            normalized = _normalize(self.surface, self.normalization_rule)
        except ValueError:
            return False
        return (source.verify() and 0 <= start < end <= len(source.content) and
                source.content[start:end] == self.surface and normalized == self.canonical)


@dataclass(frozen=True, order=True)
class SemanticAtom:
    predicate: str
    predicate_surface: str
    predicate_span: tuple[int, int]
    arguments: tuple[SemanticTerm, ...]
    polarity: str
    modality: str
    analysis_id: str
    compiler_rule: str

    def __post_init__(self) -> None:
        if not _PREDICATE.fullmatch(self.predicate) or not self.predicate_surface or \
                not self.arguments or len(self.arguments) > 8:
            raise ValueError("atom needs a canonical predicate and 1-8 arguments")
        if self.polarity not in _POLARITIES or self.modality not in _MODALITIES:
            raise ValueError("atom polarity or modality is invalid")
        if not _IDENTIFIER.fullmatch(self.analysis_id) or not _IDENTIFIER.fullmatch(
                self.compiler_rule):
            raise ValueError("atom analysis and compiler rule must be canonical")

    def verify(self, source: SemanticSource) -> bool:
        start, end = self.predicate_span
        return (source.verify() and 0 <= start < end <= len(source.content) and
                source.content[start:end] == self.predicate_surface and
                all(item.verify(source, allow_variable=False) for item in self.arguments))

    @property
    def evidence_span(self) -> tuple[int, int]:
        spans = [self.predicate_span] + [item.source_span for item in self.arguments
                                        if item.source_span is not None]
        return min(item[0] for item in spans), max(item[1] for item in spans)

    def payload(self, source: SemanticSource, alternative_set: str,
                assumptions: tuple[str, ...]) -> dict:
        return {
            "source_id": source.source_id, "source_sha256": source.sha256,
            "scope": source.scope, "alternative_set": alternative_set,
            "analysis_id": self.analysis_id, "predicate": self.predicate,
            "predicate_surface": self.predicate_surface,
            "predicate_span": self.predicate_span,
            "arguments": [{
                "surface": item.surface, "canonical": item.canonical,
                "kind": item.kind, "source_span": item.source_span,
                "normalization_rule": item.normalization_rule,
            } for item in self.arguments],
            "polarity": self.polarity, "modality": self.modality,
            "compiler_rule": self.compiler_rule, "assumptions": assumptions,
            "evidence_span": self.evidence_span,
        }


@dataclass(frozen=True)
class SemanticAnalysis:
    analysis_id: str
    source_id: str
    alternative_set: str
    atoms: tuple[SemanticAtom, ...]
    assumptions: tuple[str, ...] = ()
    complete: bool = False

    def __post_init__(self) -> None:
        if not all(_IDENTIFIER.fullmatch(value) for value in (
                self.analysis_id, self.source_id, self.alternative_set)):
            raise ValueError("analysis identifiers must be canonical")
        if not self.atoms or len(self.atoms) > 10_000 or any(
                item.analysis_id != self.analysis_id for item in self.atoms):
            raise ValueError("analysis needs bounded atoms with matching identity")
        if self.assumptions != tuple(sorted(set(self.assumptions))) or any(
                not _IDENTIFIER.fullmatch(item) for item in self.assumptions):
            raise ValueError("analysis assumptions must be canonical")

    @property
    def environment_label(self) -> str:
        return f"analysis:{self.source_id}:{self.alternative_set}:{self.analysis_id}"


@dataclass(frozen=True, order=True)
class SemanticGoal:
    predicate: str
    predicate_surface: str
    predicate_span: tuple[int, int]
    arguments: tuple[SemanticTerm, ...]

    def __post_init__(self) -> None:
        if not _PREDICATE.fullmatch(self.predicate) or not self.predicate_surface or \
                not self.arguments or len(self.arguments) > 8:
            raise ValueError("semantic goal is invalid")

    def verify(self, source: SemanticSource) -> bool:
        start, end = self.predicate_span
        return (source.verify() and 0 <= start < end <= len(source.content) and
                source.content[start:end] == self.predicate_surface and
                all(item.verify(source, allow_variable=True) for item in self.arguments))


@dataclass(frozen=True)
class SemanticQuery:
    analysis_id: str
    source_id: str
    goals: tuple[SemanticGoal, ...]
    output_variables: tuple[str, ...]
    assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not _IDENTIFIER.fullmatch(self.analysis_id) or not _IDENTIFIER.fullmatch(self.source_id):
            raise ValueError("query identifiers must be canonical")
        if not self.goals or len(self.goals) > 32 or not self.output_variables:
            raise ValueError("query needs bounded goals and outputs")
        if self.assumptions != tuple(sorted(set(self.assumptions))):
            raise ValueError("query assumptions must be canonical")


@dataclass(frozen=True)
class D42Transport:
    scope: str
    sources: tuple[SealedSource, ...]
    facts: tuple[AuthorizedFact, ...]
    nogoods: tuple[frozenset[str], ...]
    allowed_rules: frozenset[str]
    ir_attestations: tuple[tuple[int, str], ...]

    def executor(self) -> SigmaPBAExecutor:
        return SigmaPBAExecutor(
            sources=self.sources, facts=self.facts, scope=self.scope,
            allowed_rules=self.allowed_rules, nogoods=self.nogoods)


@dataclass(frozen=True)
class D42QueryResult:
    state: str
    values: tuple[tuple[str, ...], ...]
    analysis_results: tuple[tuple[str, SigmaPBAResult], ...]
    reason: str


class AuthorizedSemanticIR:
    """Validate D42 declarations and transport them without semantic inference."""

    @staticmethod
    def _attestation(payload: dict) -> str:
        return hashlib.sha256(
            b"horizon-d42-semantic-ir-v1\x00" + _canonical_json(payload).encode()).hexdigest()

    @classmethod
    def transport(cls, sources: tuple[SemanticSource, ...],
                  analyses: tuple[SemanticAnalysis, ...]) -> D42Transport:
        if not sources or not analyses or len(analyses) > 256:
            raise ValueError("D42 transport needs bounded sources and analyses")
        canonical_sources = tuple(sorted(sources, key=lambda item: item.source_id))
        if len({item.source_id for item in canonical_sources}) != len(canonical_sources) or \
                not all(item.verify() for item in canonical_sources):
            raise ValueError("semantic sources must be unique and sealed")
        scopes = {item.scope for item in canonical_sources}
        if len(scopes) != 1:
            raise ValueError("D42 transport cannot mix scopes")
        by_source = {item.source_id: item for item in canonical_sources}

        rows = []
        alternatives: dict[tuple[str, str], set[str]] = {}
        seen = set()
        for analysis in analyses:
            source = by_source.get(analysis.source_id)
            if source is None:
                raise ValueError("analysis references an unknown source")
            key = (analysis.source_id, analysis.alternative_set, analysis.analysis_id)
            if key in seen:
                raise ValueError("analysis identity collision")
            seen.add(key)
            alternatives.setdefault(key[:2], set()).add(analysis.environment_label)
            assumptions = tuple(sorted(set(analysis.assumptions + (
                analysis.environment_label,))))
            atom_payloads = []
            for atom in analysis.atoms:
                if not atom.verify(source):
                    raise ValueError("atom failed exact span or term verification")
                payload = atom.payload(source, analysis.alternative_set, assumptions)
                canonical = _canonical_json(payload)
                if canonical in atom_payloads:
                    raise ValueError("duplicate atom inside one analysis")
                atom_payloads.append(canonical)
                rows.append((canonical, source, atom, assumptions, payload))

        rows.sort(key=lambda item: item[0])
        facts = []
        attestations = []
        for fact_id, (_, source, atom, assumptions, payload) in enumerate(rows, 1):
            attestation = cls._attestation(payload)
            # D40 binds this IR attestation through the versioned orbit field.
            orbit = f"d42:{attestation}"
            fact = AuthorizedFact.seal(
                fact_id=fact_id, predicate=atom.predicate,
                arguments=tuple(item.canonical for item in atom.arguments),
                scope=source.scope, source=source.as_sigma_source(),
                source_span=atom.evidence_span, compiler_rule=_RULE,
                orbit=orbit, assumptions=assumptions)
            facts.append(fact)
            attestations.append((fact_id, attestation))

        nogoods = []
        for labels in alternatives.values():
            ordered = sorted(labels)
            nogoods.extend(frozenset((left, right))
                           for index, left in enumerate(ordered)
                           for right in ordered[index + 1:])
        return D42Transport(
            next(iter(scopes)), tuple(item.as_sigma_source() for item in canonical_sources),
            tuple(facts), tuple(sorted(set(nogoods), key=lambda item: tuple(sorted(item)))),
            frozenset({_RULE}), tuple(attestations))

    @staticmethod
    def compile_query(source: SemanticSource, query: SemanticQuery) -> ConjunctiveProgram:
        if not source.verify() or query.source_id != source.source_id or any(
                not goal.verify(source) for goal in query.goals):
            raise ValueError("query failed exact source or goal verification")
        program = ConjunctiveProgram(tuple(
            RelationalGoal(goal.predicate, tuple(item.canonical for item in goal.arguments))
            for goal in query.goals), query.output_variables)
        return program

    @classmethod
    def execute_queries(cls, transport: D42Transport, source: SemanticSource,
                        queries: tuple[SemanticQuery, ...], **budgets) -> D42QueryResult:
        if not queries or len(queries) > 64 or len({item.analysis_id for item in queries}) != len(queries):
            raise ValueError("query alternatives must be bounded and identity-unique")
        executor = transport.executor()
        results = tuple((query.analysis_id, executor.execute(
            cls.compile_query(source, query), **budgets))
            for query in sorted(queries, key=lambda item: item.analysis_id))
        if any(result.state == "contested" for _, result in results):
            values = tuple(sorted({item.values for _, result in results
                                   for item in result.outputs}))
            return D42QueryResult("contested", values, results,
                                  "one query analysis has contested complete environments")
        if any(result.state != "resolved" for _, result in results):
            return D42QueryResult("abstain", (), results,
                                  "at least one query analysis did not close")
        values = tuple(sorted({item.values for _, result in results for item in result.outputs}))
        if len(values) != 1:
            return D42QueryResult("contested", values, results,
                                  "complete query analyses disagree")
        return D42QueryResult("resolved", values, results,
                              "all complete query analyses agree")
