# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""D82 multi-charge synthesis with exact-span bindings and isolated proof worlds."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
import unicodedata

from .hssd_query_compiler import StructuralHSSDQueryCompiler


RULE = "d82.polyphonic-synthesis-calculus.v1"
_BOUNDARY = re.compile(
    r";\s*|,\s+(?=(?:and\s+)?(?:how|what|which|who|where|when|why)\b)|"
    r"\s+and\s+(?=(?:how|what|which|who|where|when|why)\b)", re.I)
_CONTEXT = re.compile(r"^(?:given|given that|considering|although|despite|whereas)\b", re.I)
_MARKERS = (
    ("compare", re.compile(
        r"\b(?:compare|comparison|compared|versus|vs\.?|differ|difference|relative to|"
        r"trade-?offs?|advantages?|limitations?)\b", re.I)),
    ("trace_evolution", re.compile(
        r"\b(?:evolution|evolve|progress(?:ion|ively)?|successive|over time|from .+ through|"
        r"intermediate stages?|at each stage|cumulative)\b", re.I)),
    ("optimize", re.compile(
        r"\b(?:optimal|optimize|optimise|design|balance|achieve both|maximi[sz]e|minimi[sz]e|"
        r"while maintaining|while preserving)\b", re.I)),
    ("explain", re.compile(
        r"\b(?:why|explain|mechanism|cause[sd]?|reason|reveal|enable[sd]?|account for|"
        r"theoretical(?:ly)? grounded)\b", re.I)),
    ("quantify", re.compile(
        r"\b(?:quantif(?:y|iable)|quantitativ\w*|numerical|how much|how many|magnitude|"
        r"performance indicators?|metrics?|threshold|rate|percentage|improvement)\b", re.I)),
    ("integrate", re.compile(
        r"\b(?:integrat(?:e|ed|ing|ion)|combin(?:e|ed|ing|ation)|collectively|jointly|"
        r"unified|synthesi[sz]e|complete evolution|complete progression)\b", re.I)),
)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


@dataclass(frozen=True)
class SynthesisObligation:
    obligation_id: str
    question_sha256: str
    span: tuple[int, int]
    surface: str
    role: str
    operations: tuple[str, ...]
    dependencies: tuple[str, ...]

    def verify(self, question: str) -> bool:
        start, end = self.span
        return (self.question_sha256 == _digest(question) and 0 <= start < end <= len(question)
                and question[start:end] == self.surface and self.role in ("context", "output")
                and self.operations == tuple(sorted(set(self.operations))))


@dataclass(frozen=True)
class PolyphonicSynthesisPlan:
    question: str
    question_sha256: str
    obligations: tuple[SynthesisObligation, ...]
    output_ids: tuple[str, ...]
    digest: str

    def verify(self) -> bool:
        identifiers = tuple(item.obligation_id for item in self.obligations)
        return (self.question_sha256 == _digest(self.question)
                and len(set(identifiers)) == len(identifiers)
                and all(item.verify(self.question) for item in self.obligations)
                and self.output_ids == tuple(item.obligation_id for item in self.obligations
                                             if item.role == "output")
                and self.digest == _plan_digest(self.question_sha256, self.obligations,
                                                self.output_ids))


@dataclass(frozen=True)
class SynthesisSource:
    source_id: str
    content: str
    sha256: str

    @classmethod
    def seal(cls, source_id: str, content: str) -> "SynthesisSource":
        if not source_id or not content:
            raise ValueError("D82 source requires identity and content")
        return cls(source_id, content, _digest(content))

    def verify(self) -> bool:
        return bool(self.source_id and self.content and self.sha256 == _digest(self.content))


@dataclass(frozen=True, order=True)
class SynthesisBinding:
    environment: str
    obligation_id: str
    binding_id: str
    source_id: str
    source_sha256: str
    source_span: tuple[int, int]
    surface: str
    fact_ids: tuple[int, ...]

    def verify(self, plan: PolyphonicSynthesisPlan,
               sources: dict[str, SynthesisSource]) -> bool:
        source = sources.get(self.source_id)
        start, end = self.source_span
        return (bool(self.environment and self.binding_id and self.obligation_id in plan.output_ids)
                and source is not None and source.verify() and source.sha256 == self.source_sha256
                and 0 <= start < end <= len(source.content)
                and source.content[start:end] == self.surface
                and self.fact_ids == tuple(sorted(set(self.fact_ids))) and bool(self.fact_ids))


@dataclass(frozen=True)
class PolyphonicSynthesisResult:
    state: str
    text: str
    bindings: tuple[SynthesisBinding, ...]
    complete_environments: tuple[str, ...]
    output_bytes: int
    reason: str
    digest: str


def _plan_digest(question_sha256: str, obligations: tuple[SynthesisObligation, ...],
                 output_ids: tuple[str, ...]) -> str:
    return _digest(repr((RULE, question_sha256, obligations, output_ids)))


def _result_digest(state: str, text: str, bindings: tuple[SynthesisBinding, ...],
                   environments: tuple[str, ...], output_bytes: int, reason: str) -> str:
    return _digest(repr((RULE, state, text, bindings, environments, output_bytes, reason)))


def _segments(question: str) -> tuple[tuple[int, int], ...]:
    boundaries = [0]
    for match in _BOUNDARY.finditer(question):
        boundaries.extend((match.start(), match.end()))
    boundaries.append(len(question))
    spans = []
    for left, right in zip(boundaries[::2], boundaries[1::2]):
        while left < right and question[left].isspace():
            left += 1
        while right > left and question[right - 1].isspace():
            right -= 1
        if left < right:
            spans.append((left, right))
    return tuple(spans) or ((0, len(question)),)


def _operations(surface: str, hssd: StructuralHSSDQueryCompiler) -> tuple[str, ...]:
    values = {name for name, pattern in _MARKERS if pattern.search(surface)}
    plan = hssd.compile(surface)
    if plan.state == "compiled" and plan.operation not in ("unsupported", "lookup"):
        values.add(plan.operation)
    if not values:
        values.add("lookup")
    return tuple(sorted(values))


def compile_polyphonic_synthesis(question: str) -> PolyphonicSynthesisPlan:
    if not isinstance(question, str) or not question.strip() or len(question) > 4096:
        raise ValueError("D82 question must be non-empty bounded text")
    digest = _digest(question)
    hssd = StructuralHSSDQueryCompiler()
    obligations = []
    contexts = []
    spans = _segments(question)
    for index, (start, end) in enumerate(spans):
        surface = question[start:end]
        role = "context" if _CONTEXT.search(surface.strip()) else "output"
        identifier = f"q:{index}"
        dependencies = tuple(contexts) if role == "output" else ()
        obligations.append(SynthesisObligation(
            identifier, digest, (start, end), surface, role,
            _operations(surface, hssd), dependencies))
        if role == "context":
            contexts.append(identifier)
    if not any(item.role == "output" for item in obligations):
        first = obligations[0]
        obligations[0] = SynthesisObligation(
            first.obligation_id, first.question_sha256, first.span, first.surface,
            "output", first.operations, ())
    frozen = tuple(obligations)
    output_ids = tuple(item.obligation_id for item in frozen if item.role == "output")
    plan = PolyphonicSynthesisPlan(question, digest, frozen, output_ids,
                                   _plan_digest(digest, frozen, output_ids))
    if not plan.verify():
        raise ValueError("D82 compiled plan failed exact-span verification")
    return plan


def _render(plan: PolyphonicSynthesisPlan,
            bindings: tuple[SynthesisBinding, ...]) -> str:
    by_obligation: dict[str, list[SynthesisBinding]] = {}
    for item in bindings:
        by_obligation.setdefault(item.obligation_id, []).append(item)
    lines = []
    obligations = {item.obligation_id: item for item in plan.obligations}
    for obligation_id in plan.output_ids:
        operation = "+".join(obligations[obligation_id].operations)
        lines.append(f"[{operation}]")
        for binding in sorted(by_obligation[obligation_id]):
            facts = ",".join(str(value) for value in binding.fact_ids)
            lines.append(f"- {binding.surface} [source={binding.source_id};facts={facts}]")
    return "\n".join(lines)


def synthesize_polyphonic(plan: PolyphonicSynthesisPlan,
                          sources: tuple[SynthesisSource, ...],
                          bindings: tuple[SynthesisBinding, ...], *,
                          max_bytes: int = 24_576) -> PolyphonicSynthesisResult:
    if not plan.verify() or max_bytes < 256:
        raise ValueError("D82 requires a verified plan and bounded positive budget")
    source_map = {item.source_id: item for item in sources}
    if len(source_map) != len(sources) or any(not item.verify() for item in sources):
        raise ValueError("D82 sources must be unique and verified")
    if len(set(bindings)) != len(bindings):
        raise ValueError("D82 bindings must be unique")
    bindings = tuple(sorted(bindings))
    if any(not item.verify(plan, source_map) for item in bindings):
        raise ValueError("D82 rejected unauthorized binding")

    by_environment: dict[str, list[SynthesisBinding]] = {}
    for binding in bindings:
        by_environment.setdefault(binding.environment, []).append(binding)
    complete = []
    payloads = {}
    for environment, rows in sorted(by_environment.items()):
        covered = {item.obligation_id for item in rows}
        if set(plan.output_ids) <= covered:
            frozen = tuple(sorted(rows))
            complete.append(environment)
            payloads[environment] = tuple(
                (_canonical(item.surface), item.obligation_id)
                for item in frozen)

    complete_environments = tuple(complete)
    if not complete_environments:
        reason = "no single proof environment closes every output obligation"
        return PolyphonicSynthesisResult(
            "incomplete", "", (), (), 0, reason,
            _result_digest("incomplete", "", (), (), 0, reason))
    distinct = {payloads[environment] for environment in complete_environments}
    if len(distinct) != 1:
        reason = "complete proof environments yield distinct synthesis payloads"
        return PolyphonicSynthesisResult(
            "contested", "", (), complete_environments, 0, reason,
            _result_digest("contested", "", (), complete_environments, 0, reason))

    chosen_environment = complete_environments[0]
    chosen = tuple(sorted(by_environment[chosen_environment]))
    text = _render(plan, chosen)
    output_bytes = len(text.encode("utf-8"))
    if output_bytes > max_bytes:
        reason = "verified synthesis exceeds output budget"
        return PolyphonicSynthesisResult(
            "incomplete", "", (), complete_environments, 0, reason,
            _result_digest("incomplete", "", (), complete_environments, 0, reason))
    reason = "all noncompensable obligations closed in one authoritative environment"
    return PolyphonicSynthesisResult(
        "resolved", text, chosen, complete_environments, output_bytes, reason,
        _result_digest("resolved", text, chosen, complete_environments, output_bytes, reason))


__all__ = [
    "PolyphonicSynthesisPlan", "PolyphonicSynthesisResult", "SynthesisBinding",
    "SynthesisObligation", "SynthesisSource", "compile_polyphonic_synthesis",
    "synthesize_polyphonic",
]
