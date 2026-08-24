# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Scorer-blind proof kernel for explanatory, multi-turn questions.

The kernel deliberately starts *after* retrieval.  Caller-visible turn queries define bounded
source fibers; exact source sentences can witness obligations inside those fibers.  Scores only
schedule candidates.  A result closes solely when every obligation has an exact witness, adjacent
turns are connected by witnessed charges, all complete environments agree, and the smallest proof
fits the declared budget.

This core kernel proves source-relative closure and provenance, not that an external document is
true and not that arbitrary English has been understood.  Its resolver stays opt-in until proof
coverage and answer quality pass an independent cohort.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import itertools
import json
import re
from typing import Iterable

from ._eop_claims import (
    AuthorizedClaim,
    ClaimSource,
    QuestionObligation,
    compile_question_obligations,
    extract_authorized_claims,
)
from ._eop_roles import claim_sketch, compatibility, obligation_sketch
from ._eop_operations import compile_polyphonic_synthesis
from .sigma_pba import (
    AuthorizedFact, ConjunctiveProgram, RelationalGoal, SealedSource, SigmaPBAExecutor,
)


RULE = "horizon.explanatory-obligation-proof.v1"
_GENERIC_BRIDGE = frozenset({
    "answer", "approach", "effect", "framework", "method", "model", "result",
    "system", "technique", "what", "which", "how", "why", "does", "did",
    "through", "using", "with", "from",
})
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_EXPLANATORY_RELATION = re.compile(
    r"^\s*(?:and\s+)?(?:how|why)\s+"
    r"(?:do|does|did|can|could|would|will|is|are|was|were)\s+"
    r"(?P<subject>[^\W_]+)\s+(?P<relation>[^\W_]+)", re.IGNORECASE)
_PROPOSITION_STOP = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "by", "can",
    "could", "did", "do", "does", "for", "from", "had", "has", "have", "in",
    "is", "it", "may", "might", "must", "no", "not", "of", "on", "or", "shall",
    "should", "than", "that", "the", "their", "there", "these", "they", "this",
    "those", "to", "was", "were", "which", "will", "with", "would",
})
_NUMBER_TOKEN = re.compile(r"^[+-]?(?:\d+(?:[.,]\d+)?|[.,]\d+)%?$")
_COMPARISON_CARRIER = re.compile(
    r"\b(?:compar(?:e[sd]?|ed)\s+(?:to|with)|versus|vs\.?|relative\s+to|"
    r"differ(?:s|ed)?\s+from|(?:higher|lower|faster|slower|larger|smaller|better|worse)\s+than|"
    r"outperform(?:s|ed)?)\b", re.IGNORECASE)
_CAUSAL_CARRIER = re.compile(
    r"\b(?:because|due\s+to|caus(?:e[sd]?|ing)|lead(?:s|ing)?\s+to|"
    r"result(?:s|ed|ing)?\s+(?:in|from)|enable[sd]?|allow(?:s|ed)?|"
    r"account(?:s|ed)?\s+for|thereby)\b", re.IGNORECASE)
_UNIT_WORDS = frozenset({
    "%", "percent", "percentage", "hz", "khz", "mhz", "ghz", "ms", "millisecond",
    "milliseconds", "second", "seconds", "minute", "minutes", "hour", "hours", "byte",
    "bytes", "kb", "mb", "gb", "mv", "volt", "volts", "watt", "watts", "db",
})
_QUANTITY_WITH_UNIT = re.compile(
    r"(?<!\w)(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>%|percent(?:age)?|hz|khz|mhz|ghz|"
    r"ms|milliseconds?|seconds?|minutes?|hours?|bytes?|kb|mb|gb|mv|volts?|watts?|db)(?!\w)",
    re.IGNORECASE)


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_text(value: str) -> str:
    return _sha_bytes(value.encode("utf-8"))


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True, order=True)
class ExplanatorySource:
    source_id: str
    text: str
    turn_index: int
    session_id: str
    source_role: str
    root_id: str
    sha256: str

    @classmethod
    def seal(cls, source_id: str, text: str, *, turn_index: int,
             session_id: str = "session", source_role: str = "document",
             root_id: str | None = None) -> "ExplanatorySource":
        if (not source_id or not text.strip() or turn_index < 0 or not session_id
                or not source_role):
            raise ValueError("EOP sources require identity, text, turn, session and role")
        return cls(source_id, text, turn_index, session_id, source_role,
                   root_id or source_id, _sha_text(text))

    def verify(self) -> bool:
        return bool(self.source_id and self.text.strip() and self.turn_index >= 0
                    and self.session_id and self.source_role and self.root_id
                    and self.sha256 == _sha_text(self.text))


@dataclass(frozen=True, order=True)
class ExplanatoryIntent:
    intent_id: str
    text: str
    turn_index: int
    source_ids: tuple[str, ...]
    sha256: str

    @classmethod
    def seal(cls, intent_id: str, text: str, *, turn_index: int,
             source_ids: Iterable[str]) -> "ExplanatoryIntent":
        frozen = tuple(sorted(set(str(item) for item in source_ids)))
        if not intent_id or not text.strip() or turn_index < 0 or not frozen:
            raise ValueError("EOP intents require identity, text, turn and source fiber")
        return cls(intent_id, text, turn_index, frozen, _sha_text(text))

    def verify(self, known_sources: frozenset[str]) -> bool:
        return bool(self.intent_id and self.text.strip() and self.turn_index >= 0
                    and self.source_ids and set(self.source_ids) <= known_sources
                    and self.sha256 == _sha_text(self.text))


@dataclass(frozen=True, order=True)
class ObligationNode:
    obligation_id: str
    layer: str                 # lane | final
    intent_id: str
    turn_index: int
    surface: str
    authority_sha256: str
    source_ids: tuple[str, ...]
    predecessors: tuple[str, ...]
    operations: tuple[str, ...]
    polarity: str
    modality: str
    closure_mode: str = "witness"  # witness | reuse | join
    reuse_obligation_id: str = ""


@dataclass(frozen=True)
class ObligationGraph:
    nodes: tuple[ObligationNode, ...]
    digest: str


@dataclass(frozen=True, order=True)
class WitnessBinding:
    obligation_id: str
    claim_id: str
    source_id: str
    source_sha256: str
    source_span: tuple[int, int]
    turn_index: int
    session_id: str
    source_role: str
    semantic_roles: tuple[str, ...]
    predicates: tuple[str, ...]
    anchors: tuple[str, ...]
    polarity: str
    modality: str
    genealogy_root: str
    surface: str

    def verify(self, sources: dict[str, ExplanatorySource]) -> bool:
        source = sources.get(self.source_id)
        if source is None or not source.verify() or source.sha256 != self.source_sha256:
            return False
        start, end = self.source_span
        return (0 <= start < end <= len(source.text)
                and source.text[start:end] == self.surface
                and source.turn_index == self.turn_index
                and source.session_id == self.session_id
                and source.source_role == self.source_role
                and source.root_id == self.genealogy_root)


@dataclass(frozen=True, order=True)
class WitnessedBridge:
    left_obligation_id: str
    right_obligation_id: str
    left_claim_id: str
    right_claim_id: str
    charges: tuple[str, ...]


@dataclass(frozen=True, order=True)
class JoinClosure:
    obligation_id: str
    mode: str
    predecessor_ids: tuple[str, ...]
    predecessor_claim_ids: tuple[str, ...]
    reused_obligation_id: str = ""


@dataclass(frozen=True)
class ExplanatoryProofCertificate:
    question_sha256: str
    graph_digest: str
    binding_digest: str
    bridge_digest: str
    closure_digest: str
    answer_sha256: str
    config_digest: str
    state: str
    digest: str

    def compact(self) -> bytes:
        return _canonical({
            "rule": RULE, "q": self.question_sha256, "g": self.graph_digest,
            "w": self.binding_digest, "b": self.bridge_digest,
            "j": self.closure_digest, "a": self.answer_sha256, "c": self.config_digest,
            "s": self.state, "d": self.digest,
        })

    def reopen(self, *, question: str, intents: tuple[ExplanatoryIntent, ...],
               sources: tuple[ExplanatorySource, ...],
               config: "ExplanatoryProofConfig" = None) -> bool:
        if config is None:
            config = ExplanatoryProofConfig()
        rerun = solve_explanatory_obligations(
            question=question, intents=intents, sources=sources, config=config)
        return rerun.certificate == self and rerun.certificate.compact() == self.compact()


@dataclass(frozen=True)
class ExplanatoryProofResult:
    state: str                # resolved | contested | unsupported | abstain
    text: str
    graph: ObligationGraph
    bindings: tuple[WitnessBinding, ...]
    bridges: tuple[WitnessedBridge, ...]
    closures: tuple[JoinClosure, ...]
    alternatives: tuple[tuple[str, ...], ...]
    residual: tuple[str, ...]
    proof_bytes: int
    environments_examined: int
    certificate: ExplanatoryProofCertificate


@dataclass(frozen=True)
class ExplanatoryProofConfig:
    max_candidates_per_obligation: int = 4
    max_environments: int = 4096
    max_output_bytes: int = 24_576
    max_evidence_bytes: int = 65_536
    max_claims: int = 32
    max_bridge_hops: int = 1

    def __post_init__(self) -> None:
        if min(self.max_candidates_per_obligation, self.max_environments,
               self.max_output_bytes, self.max_evidence_bytes,
               self.max_claims, self.max_bridge_hops) < 1:
            raise ValueError("EOP budgets must be positive")


@dataclass(frozen=True)
class _Candidate:
    binding: WitnessBinding
    score: tuple[int, ...]
    value_signature: tuple[str, ...]
    binding_key: tuple[str, ...]
    factual_values: tuple[str, ...]


def _graph_digest(nodes: tuple[ObligationNode, ...]) -> str:
    return _sha_bytes(_canonical([node.__dict__ for node in nodes]))


def _operation_charges(surface: str) -> tuple[str, ...]:
    """Reuse D82's question-only multi-charge compiler without making charges authority."""
    plan = compile_polyphonic_synthesis(surface)
    charges = tuple(sorted({operation for node in plan.obligations
                            if node.role == "output" for operation in node.operations}))
    return charges or ("lookup",)


def compile_obligation_graph(question: str, intents: tuple[ExplanatoryIntent, ...],
                             sources: tuple[ExplanatorySource, ...]) -> ObligationGraph:
    known = frozenset(item.source_id for item in sources)
    if not question.strip() or not intents or not sources:
        raise ValueError("EOP requires question, intents and sources")
    if len(known) != len(sources) or any(not item.verify() for item in sources):
        raise ValueError("EOP sources must be unique and sealed")
    if len({item.intent_id for item in intents}) != len(intents) or any(
            not item.verify(known) for item in intents):
        raise ValueError("EOP intents must be unique and source-bounded")
    ordered_intents = tuple(sorted(intents, key=lambda item: (item.turn_index, item.intent_id)))
    if len({item.turn_index for item in ordered_intents}) != len(ordered_intents):
        raise ValueError("EOP v1 permits one visible intent per turn")
    if any(next(source for source in sources if source.source_id == source_id).turn_index
           != intent.turn_index for intent in intents for source_id in intent.source_ids):
        raise ValueError("intent source fiber crosses its declared turn")

    nodes: list[ObligationNode] = []
    previous: tuple[str, ...] = ()
    for intent in ordered_intents:
        compiled = compile_question_obligations(
            intent.text, authority_id=f"lane:{intent.intent_id}")
        current = []
        for index, obligation in enumerate(compiled):
            identifier = f"lane:{intent.turn_index}:{index}:{obligation.obligation_id}"
            nodes.append(ObligationNode(
                identifier, "lane", intent.intent_id, intent.turn_index,
                obligation.surface, obligation.authority_sha256, intent.source_ids,
                previous, _operation_charges(obligation.surface),
                obligation.polarity, obligation.modality))
            current.append(identifier)
        previous = tuple(current)
    all_sources = tuple(sorted(known))
    for index, obligation in enumerate(compile_question_obligations(
            question, authority_id="final-question")):
        normalized = " ".join(obligation.surface.casefold().split()).rstrip("?.!")
        reusable = next((node for node in reversed(nodes)
                         if " ".join(node.surface.casefold().split()).rstrip("?.!")
                         == normalized), None)
        closure_mode = "reuse" if reusable is not None else "join"
        nodes.append(ObligationNode(
            f"final:{index}:{obligation.obligation_id}", "final", "final-question",
            ordered_intents[-1].turn_index + 1, obligation.surface,
            obligation.authority_sha256, all_sources, previous,
            _operation_charges(obligation.surface),
            obligation.polarity, obligation.modality, closure_mode,
            reusable.obligation_id if reusable is not None else ""))
    frozen = tuple(nodes)
    return ObligationGraph(frozen, _graph_digest(frozen))


def _local_obligation(node: ObligationNode) -> QuestionObligation:
    # The node itself already binds the original query digest.  Role compilation needs only the
    # exact local surface, so it receives a locally re-openable authority record.
    from .raw_causal_channels import observe_raw_text
    channels = observe_raw_text(node.surface, question=True)
    return QuestionObligation(
        node.obligation_id, _sha_text(node.surface), (0, len(node.surface)), node.surface,
        frozenset(channels.lexical), frozenset(channels.entities) |
        frozenset(channels.numbers), frozenset(channels.relations),
        channels.polarity, channels.modality)


def _value_signature(claim: AuthorizedClaim, semantic_roles: tuple[str, ...],
                     predicates: tuple[str, ...]) -> tuple[str, ...]:
    """Complete factual signature; no prefix/token truncation is permitted."""
    return (
        f"polarity:{claim.polarity}", f"modality:{claim.modality}",
        *(f"anchor:{item}" for item in sorted(claim.anchors)),
        *(f"predicate:{item}" for item in predicates),
        *(f"role:{item}" for item in semantic_roles),
        *(f"lexical:{item}" for item in sorted(claim.lexical)),
    )


def _requires_relational_witness(node: ObligationNode) -> bool:
    lowered = node.surface.casefold().lstrip()
    return (lowered.startswith(("how ", "why ")) or "compare" in node.operations
            or "explain" in node.operations or "explain_cause" in node.operations)


def _relation_lemma(token: str) -> str:
    value = token.casefold()
    if len(value) > 4 and value.endswith("ies"):
        return value[:-3] + "y"
    if len(value) > 4 and value.endswith("izes"):
        return value[:-1]
    if len(value) > 4 and value.endswith("es"):
        base = value[:-2]
        if base.endswith(("s", "x", "z", "ch", "sh")):
            return base
        return value[:-1]
    if len(value) > 3 and value.endswith("s"):
        return value[:-1]
    if len(value) > 5 and value.endswith("ing"):
        return value[:-3].rstrip(value[-4:-3])
    if len(value) > 4 and value.endswith("ed"):
        return value[:-2]
    return value


def _operation_relation_witness(node: ObligationNode, claim: AuthorizedClaim) -> bool:
    """Require the query's subject->verbal-relation->object order in the exact claim span."""
    match = _EXPLANATORY_RELATION.search(node.surface)
    if match is None:
        return False
    subject = match.group("subject").casefold()
    relation = _relation_lemma(match.group("relation"))
    query_words = tuple(item.casefold() for item in _WORD.findall(node.surface))
    claim_words = tuple(item.casefold() for item in _WORD.findall(claim.surface))
    relation_positions = tuple(index for index, token in enumerate(claim_words)
                               if _relation_lemma(token) == relation)
    subject_positions = tuple(index for index, token in enumerate(claim_words)
                              if token == subject)
    if not relation_positions or not subject_positions:
        return False
    query_objects = tuple(token for token in query_words
                          if token in node.surface.casefold()
                          and token != subject and token not in {
                              "how", "why", "do", "does", "did", "can", "could", "would",
                              "will", "is", "are", "was", "were", match.group("relation").casefold(),
                              "with", "than", "to", "the", "a", "an"})
    for subject_index in subject_positions:
        for relation_index in relation_positions:
            if subject_index >= relation_index:
                continue
            if not query_objects or any(
                    token in claim_words[relation_index + 1:] for token in query_objects):
                return True
    return False


def _comparison_relation_witness(node: ObligationNode, claim: AuthorizedClaim) -> bool:
    """Bind an explicit directional comparison without forcing a simple SVO query parse."""
    if _COMPARISON_CARRIER.search(claim.surface) is None:
        return False
    obligation = _local_obligation(node)
    normalize_anchor = lambda value: value.casefold().removesuffix("'s").removesuffix("’s")
    obligation_anchors = {normalize_anchor(item) for item in obligation.anchors}
    claim_anchors = {normalize_anchor(item) for item in claim.anchors}
    shared_anchors = tuple(sorted(obligation_anchors & claim_anchors))
    if obligation_anchors and not shared_anchors:
        return False
    query_words = tuple(item.casefold() for item in _WORD.findall(node.surface))
    claim_words = tuple(item.casefold() for item in _WORD.findall(claim.surface))
    query_units = set(query_words) & _UNIT_WORDS
    claim_units = set(claim_words) & _UNIT_WORDS
    if query_units and not query_units & claim_units:
        return False
    # When two named operands survive in both spans their order is a non-compensable role.
    ordered_query_anchors = tuple(item for item in query_words if item in shared_anchors)
    ordered_claim_anchors = tuple(item for item in claim_words if item in shared_anchors)
    distinct_query = tuple(dict.fromkeys(ordered_query_anchors))
    distinct_claim = tuple(dict.fromkeys(ordered_claim_anchors))
    if len(distinct_query) >= 2 and distinct_claim[:len(distinct_query)] != distinct_query:
        return False
    osketch, csketch = obligation_sketch(obligation), claim_sketch(claim)
    lexical = (set(osketch.lexical) & set(csketch.lexical)) - {
        "compare", "compared", "comparison", "with", "than", "relative", "versus",
    }
    return bool(shared_anchors and (osketch.predicates & csketch.predicates or len(lexical) >= 2))


def _causal_relation_witness(node: ObligationNode, claim: AuthorizedClaim) -> bool:
    """Conservative explicit cause->effect span binding for complex explanatory queries."""
    if (_CAUSAL_CARRIER.search(claim.surface) is None or claim.polarity == "negative"
            or claim.modality == "modal"):
        return False
    obligation = _local_obligation(node)
    osketch, csketch = obligation_sketch(obligation), claim_sketch(claim)
    shared_anchors = obligation.anchors & claim.anchors
    query_words = tuple(item.casefold() for item in _WORD.findall(node.surface))
    claim_words = tuple(item.casefold() for item in _WORD.findall(claim.surface))
    common = tuple(dict.fromkeys(item for item in query_words if item in shared_anchors))
    claim_common = tuple(dict.fromkeys(item for item in claim_words if item in shared_anchors))
    if len(common) >= 2 and claim_common[:len(common)] != common:
        return False
    match = compatibility(osketch, csketch)
    lexical = len(set(osketch.lexical) & set(csketch.lexical))
    return bool(shared_anchors or (
        match.role_obligation and match.directed >= 1 and lexical >= 3))


def _paired_homogeneous_comparison_witness(
        node: ObligationNode, claim: AuthorizedClaim) -> bool:
    """Two ordered operands and two homogeneous values inside one authorized span."""
    quantities = tuple(_QUANTITY_WITH_UNIT.finditer(claim.surface))
    if len(quantities) != 2:
        return False
    normalize_unit = lambda value: {
        "percent": "%", "percentage": "%", "millisecond": "ms",
        "milliseconds": "ms", "second": "s", "seconds": "s",
        "minute": "min", "minutes": "min", "hour": "h", "hours": "h",
        "byte": "bytes", "volt": "v", "volts": "v", "watt": "w", "watts": "w",
    }.get(value.casefold(), value.casefold())
    units = tuple(normalize_unit(item.group("unit")) for item in quantities)
    if units[0] != units[1]:
        return False
    obligation = _local_obligation(node)
    normalize_anchor = lambda value: value.casefold().removesuffix("'s").removesuffix("’s")
    shared = ({normalize_anchor(item) for item in obligation.anchors}
              & {normalize_anchor(item) for item in claim.anchors})
    if len(shared) < 2:
        return False
    query_words = tuple(item.casefold() for item in _WORD.findall(node.surface))
    claim_words = tuple(item.casefold() for item in _WORD.findall(claim.surface))
    query_operands = tuple(dict.fromkeys(item for item in query_words if item in shared))
    claim_operands = tuple(dict.fromkeys(item for item in claim_words if item in shared))
    if len(query_operands) < 2 or claim_operands[:len(query_operands)] != query_operands:
        return False
    query_units = set(query_words) & _UNIT_WORDS
    if query_units and not any(normalize_unit(item) == units[0] for item in query_units):
        return False
    osketch, csketch = obligation_sketch(obligation), claim_sketch(claim)
    metric_overlap = (set(osketch.lexical) & set(csketch.lexical)) - shared - {
        "compare", "compared", "comparison", "versus", "with", "than", "and",
    }
    return bool(osketch.predicates & csketch.predicates or metric_overlap)


def _predicate_carrier(token: str, predicates: tuple[str, ...]) -> str | None:
    lemma = _relation_lemma(token)
    matches = tuple(predicate for predicate in predicates
                    if lemma.startswith(predicate) or predicate.startswith(lemma))
    return max(matches, key=len) if matches else None


def _proposition_identity(surface: str, predicates: tuple[str, ...]) \
        -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Conservative subject->predicate->object identity plus typed numeric slots.

    General anchors are topics, not values.  A value is comparable only when an explicit number
    is attached to the same directional proposition and the same local unit/slot label.
    """
    words = tuple(item.casefold() for item in _WORD.findall(surface))
    propositions: list[tuple[str, str, str, int]] = []
    for index, token in enumerate(words):
        predicate = _predicate_carrier(token, predicates)
        if predicate is None:
            continue
        left = next((words[position] for position in range(index - 1, -1, -1)
                     if words[position] not in _PROPOSITION_STOP
                     and _predicate_carrier(words[position], predicates) is None
                     and not _NUMBER_TOKEN.fullmatch(words[position])), None)
        right = next((words[position] for position in range(index + 1, len(words))
                      if words[position] not in _PROPOSITION_STOP
                      and _predicate_carrier(words[position], predicates) is None
                      and not _NUMBER_TOKEN.fullmatch(words[position])), None)
        if left is not None and right is not None:
            propositions.append((left, predicate, right, index))
    keys = tuple(sorted({f"subject:{left}|predicate:{predicate}|object:{right}"
                         for left, predicate, right, _index in propositions}))
    values = set()
    for left, predicate, right, predicate_index in propositions:
        key = f"subject:{left}|predicate:{predicate}|object:{right}"
        for number_index, token in enumerate(words):
            if not _NUMBER_TOKEN.fullmatch(token):
                continue
            # A bounded local attachment avoids treating unrelated document numbers as the
            # value of a merely topical predicate.
            if abs(number_index - predicate_index) > 8:
                continue
            metric_scope = tuple(words[position] for position in range(
                number_index + 1, min(len(words), number_index + 7))
                if words[position] not in _PROPOSITION_STOP
                and not _NUMBER_TOKEN.fullmatch(words[position])
                and words[position] not in {left, right}
                and _COMPARISON_CARRIER.fullmatch(words[position]) is None)[:4]
            if not metric_scope:
                continue
            # Preserve the complete local metric/object scope.  Generic heads such as
            # ``improvement`` or ``rate`` alone cannot make measurements of different objects
            # contradictory (e.g. power-utilization improvement vs predictive-accuracy
            # improvement).
            slot = ">".join(metric_scope)
            values.add(f"{key}|slot:{slot}|value:{token.replace(',', '.')}")
    return keys, tuple(sorted(values))


def _candidate(node: ObligationNode, claim: AuthorizedClaim,
               source: ExplanatorySource) -> _Candidate | None:
    obligation = _local_obligation(node)
    osketch, csketch = obligation_sketch(obligation), claim_sketch(claim)
    match = compatibility(osketch, csketch)
    lexical = len(obligation.lexical & claim.lexical)
    shared_anchors = len(obligation.anchors & claim.anchors)
    if obligation.polarity == "negative" and claim.polarity != "negative":
        return None
    if obligation.modality == "asserted" and claim.modality == "modal":
        return None
    if "explain" in node.operations and obligation.polarity != "negative" \
            and claim.polarity == "negative":
        return None
    # Exact named anchors plus a relation carrier make direction non-compensable.  Typed slots
    # alone cannot turn a subject/object swap into a witness.
    if shared_anchors and osketch.predicates & csketch.predicates \
            and osketch.directed and csketch.directed and match.directed == 0:
        return None
    # Explanatory/comparative operations require a directionally compatible relational witness.
    # Topic, entity, lexical or anchor overlap can propose a candidate but can never close HOW,
    # WHY or COMPARE.
    relation_route = 0
    if _requires_relational_witness(node):
        exact_relation = _operation_relation_witness(node, claim)
        explicit_comparison = (
            "compare" in node.operations and _comparison_relation_witness(node, claim))
        explicit_cause = (
            "explain" in node.operations and _causal_relation_witness(node, claim))
        paired_comparison = (
            "compare" in node.operations
            and _paired_homogeneous_comparison_witness(node, claim))
        if not (exact_relation or explicit_comparison or explicit_cause or paired_comparison):
            return None
        # Monotonic cascade: new operation-specific arms fill an unclosed obligation but can
        # never displace a witness accepted by an older, stronger route.
        relation_route = (4 if exact_relation else 3 if explicit_comparison
                          else 2 if explicit_cause else 1)
    if not (match.role_obligation or match.predicates or shared_anchors or lexical >= 2):
        return None
    semantic_roles = tuple(sorted(csketch.directed))
    predicates = tuple(sorted(csketch.predicates))
    binding = WitnessBinding(
        node.obligation_id, claim.claim_id, source.source_id, source.sha256, claim.span,
        source.turn_index, source.session_id, source.source_role,
        semantic_roles, predicates,
        tuple(sorted(claim.anchors)), claim.polarity, claim.modality, source.root_id,
        claim.surface)
    score = (relation_route, match.directed, match.role_obligation, match.predicates,
             match.typed_directed, shared_anchors, lexical,
             int(claim.modality == "asserted"), -len(claim.surface.encode("utf-8")))
    common_predicates = tuple(sorted(osketch.predicates & csketch.predicates))
    # Conflict identity must denote the same directional proposition.  Query-overlap predicates,
    # roles and arbitrary proper-name anchors are insufficient: they routinely identify a topic,
    # not one fact or value slot.
    binding_key, factual_values = _proposition_identity(claim.surface, common_predicates)
    return _Candidate(
        binding, score, _value_signature(claim, semantic_roles, predicates),
        binding_key, factual_values)


def _candidate_universe(graph: ObligationGraph, claims: tuple[AuthorizedClaim, ...],
                        sources: tuple[ExplanatorySource, ...]) \
        -> dict[str, tuple[_Candidate, ...]]:
    """All structurally admissible candidates, with genealogy collapsed before comparison."""
    source_map = {item.source_id: item for item in sources}
    result = {}
    for node in graph.nodes:
        if node.closure_mode != "witness":
            result[node.obligation_id] = ()
            continue
        raw = []
        for claim in claims:
            if claim.source_id not in node.source_ids:
                continue
            candidate = _candidate(node, claim, source_map[claim.source_id])
            if candidate is not None:
                raw.append(candidate)
        # Descendants of one genealogical root carrying the same complete fact count once.
        by_orbit_fact: dict[tuple[str, tuple[str, ...]], _Candidate] = {}
        for candidate in raw:
            key = (candidate.binding.genealogy_root, candidate.value_signature)
            prior = by_orbit_fact.get(key)
            if prior is None or (candidate.score, candidate.binding.source_id,
                                 candidate.binding.source_span) > (
                                     prior.score, prior.binding.source_id,
                                     prior.binding.source_span):
                by_orbit_fact[key] = candidate
        ordered = sorted(by_orbit_fact.values(), key=lambda item: (
            item.score, item.binding.source_id, item.binding.source_span), reverse=True)
        result[node.obligation_id] = tuple(ordered)
    return result


def _pool_contradictions(universe: dict[str, tuple[_Candidate, ...]]) \
        -> dict[str, tuple[tuple[str, ...], ...]]:
    """Detect conflicts over the complete post-genealogy universe, before any top-N cap."""
    conflicts = {}
    for obligation_id, candidates in universe.items():
        signatures = set()
        strongest_tier = candidates[0].score[:-1] if candidates else ()
        for left_index, left in enumerate(candidates):
            for right in candidates[left_index + 1:]:
                # Global means every admissible alternative before top-N, not every weak topical
                # proposal.  At least one side must satisfy the obligation's strongest structural
                # tier; weak-vs-weak collisions cannot veto a stronger witness.
                if (left.score[:-1] != strongest_tier
                        and right.score[:-1] != strongest_tier):
                    continue
                shared_propositions = set(left.binding_key) & set(right.binding_key)
                if not shared_propositions:
                    continue
                polarity_conflict = left.binding.polarity != right.binding.polarity
                left_slots: dict[tuple[str, str], set[str]] = {}
                right_slots: dict[tuple[str, str], set[str]] = {}
                for offered, destination in (
                        (left.factual_values, left_slots),
                        (right.factual_values, right_slots)):
                    for value in offered:
                        proposition, slot_and_value = value.rsplit("|slot:", 1)
                        slot, scalar = slot_and_value.rsplit("|value:", 1)
                        if proposition in shared_propositions:
                            destination.setdefault((proposition, slot), set()).add(scalar)
                value_conflict = any(
                    left_slots[key].isdisjoint(right_slots[key])
                    for key in left_slots.keys() & right_slots.keys())
                if polarity_conflict or value_conflict:
                    signatures.update((left.value_signature, right.value_signature))
        if signatures:
            conflicts[obligation_id] = tuple(sorted(signatures))
    return conflicts


def _candidates(graph: ObligationGraph, claims: tuple[AuthorizedClaim, ...],
                sources: tuple[ExplanatorySource, ...], config: ExplanatoryProofConfig) \
        -> dict[str, tuple[_Candidate, ...]]:
    universe = _candidate_universe(graph, claims, sources)
    result = {}
    for node in graph.nodes:
        offered = list(universe[node.obligation_id])
        if offered:
            # Keep the strongest structural tier.  Weaker topical matches cannot manufacture
            # an alternative environment merely by existing in a large corpus.
            tier = offered[0].score[:-1]
            offered = [item for item in offered if item.score[:-1] == tier]
        result[node.obligation_id] = tuple(offered[:config.max_candidates_per_obligation])
    return result


def _bridge(left: WitnessBinding, right: WitnessBinding, *, max_hops: int) \
        -> WitnessedBridge | None:
    if (left.session_id != right.session_id
            or not 0 <= right.turn_index - left.turn_index <= max_hops):
        return None
    anchors = set(left.anchors) & set(right.anchors)
    predicates = set(left.predicates) & set(right.predicates)
    directed = set(left.semantic_roles) & set(right.semantic_roles)
    lexical = {token.strip(".,;:!?()[]{}") for token in left.surface.casefold().split()
               if len(token) >= 5 and token not in _GENERIC_BRIDGE} & {
                   token.strip(".,;:!?()[]{}") for token in right.surface.casefold().split()
                   if len(token) >= 5 and token not in _GENERIC_BRIDGE}
    charges = tuple(sorted(
        {f"anchor:{item}" for item in anchors}
        | {f"predicate:{item}" for item in predicates}
        | {f"role:{item}" for item in directed}
        | {f"lexical:{item}" for item in lexical}))
    if not charges:
        return None
    return WitnessedBridge(left.obligation_id, right.obligation_id,
                           left.claim_id, right.claim_id, charges)


def _environment_bridges(bindings: tuple[WitnessBinding, ...], graph: ObligationGraph, *,
                         max_hops: int) \
        -> tuple[WitnessedBridge, ...] | None:
    by_obligation = {item.obligation_id: item for item in bindings}
    bridges = []
    for node in graph.nodes:
        if node.closure_mode != "witness":
            continue
        right = by_obligation[node.obligation_id]
        for predecessor_id in node.predecessors:
            if predecessor_id not in by_obligation:
                continue
            left = by_obligation[predecessor_id]
            bridge = _bridge(left, right, max_hops=max_hops)
            if bridge is None:
                return None
            # The certificate records exactly the pair used for this declared DAG edge.
            bridges.append(bridge)
    return tuple(sorted(set(bridges)))


def _environment_closures(bindings: tuple[WitnessBinding, ...], graph: ObligationGraph) \
        -> tuple[JoinClosure, ...] | None:
    by_obligation = {item.obligation_id: item for item in bindings}
    closures = []
    for node in graph.nodes:
        if node.closure_mode == "witness":
            continue
        if node.closure_mode == "reuse":
            binding = by_obligation.get(node.reuse_obligation_id)
            if binding is None:
                return None
            closures.append(JoinClosure(
                node.obligation_id, "reuse", (node.reuse_obligation_id,),
                (binding.claim_id,), node.reuse_obligation_id))
            continue
        predecessor_bindings = tuple(by_obligation.get(item) for item in node.predecessors)
        if not predecessor_bindings or any(item is None for item in predecessor_bindings):
            return None
        closures.append(JoinClosure(
            node.obligation_id, "join", node.predecessors,
            tuple(item.claim_id for item in predecessor_bindings if item is not None)))
    return tuple(closures)


def _proof_text(bindings: tuple[WitnessBinding, ...]) -> str:
    rows = []
    seen = set()
    for item in sorted(bindings, key=lambda row: (
            row.turn_index, row.source_id, row.source_span, row.claim_id)):
        key = (item.genealogy_root, item.surface)
        if key in seen:
            continue
        seen.add(key)
        rows.append(item.surface)
    return "\n".join(rows)


def _answer_signature(environment: tuple[_Candidate, ...]) -> tuple[str, ...]:
    return tuple("|".join(item.value_signature) for item in environment)


def _sigma_environments(
        graph: ObligationGraph, offered: dict[str, tuple[_Candidate, ...]],
        sources: tuple[ExplanatorySource, ...], config: ExplanatoryProofConfig) \
        -> tuple[str, tuple[tuple[_Candidate, ...], ...], int, str]:
    """Execute the obligation/bridge CSP through the existing Sigma-PBA kernel.

    Candidate scores have already selected a finite structural tier.  Sigma receives only sealed
    candidate and witnessed-bridge relations; it never sees scores.  Its output variables name one
    claim per obligation, and its provenance polynomial keeps alternative proof paths separate.
    """
    nodes = tuple(node for node in graph.nodes if node.closure_mode == "witness")
    sigma_sources = tuple(SealedSource.seal(item.source_id, item.text) for item in sources)
    source_map = {item.source_id: item for item in sigma_sources}
    candidates_by_node = tuple(offered[node.obligation_id] for node in nodes)
    facts = []
    fact_id = 1
    rule = "eop.witness.v1"
    for index, candidates in enumerate(candidates_by_node):
        for candidate in candidates:
            binding = candidate.binding
            facts.append(AuthorizedFact.seal(
                fact_id=fact_id, predicate=f"candidate_{index}",
                arguments=(binding.claim_id,), scope="eop",
                source=source_map[binding.source_id], source_span=binding.source_span,
                compiler_rule=rule))
            fact_id += 1

    node_index = {node.obligation_id: index for index, node in enumerate(nodes)}
    bridge_goals = []
    bridge_index = 0
    for right_index, node in enumerate(nodes):
        for predecessor_id in node.predecessors:
            if predecessor_id not in node_index:
                continue
            left_index = node_index[predecessor_id]
            predicate = f"bridge_{bridge_index}"
            for left, right in itertools.product(
                    candidates_by_node[left_index], candidates_by_node[right_index]):
                bridge = _bridge(
                    left.binding, right.binding, max_hops=config.max_bridge_hops)
                if bridge is None:
                    continue
                # The authorized bridge fact is cited by the exact right-hand witness it joins;
                # its arguments bind the exact left/right claim pair used in the certificate.
                binding = right.binding
                facts.append(AuthorizedFact.seal(
                    fact_id=fact_id, predicate=predicate,
                    arguments=(left.binding.claim_id, right.binding.claim_id), scope="eop",
                    source=source_map[binding.source_id], source_span=binding.source_span,
                    compiler_rule=rule))
                fact_id += 1
            bridge_goals.append(RelationalGoal(
                predicate, (f"?C{left_index}", f"?C{right_index}")))
            bridge_index += 1

    variables = tuple(f"?C{index}" for index in range(len(nodes)))
    goals = tuple(RelationalGoal(f"candidate_{index}", (variable,))
                  for index, variable in enumerate(variables)) + tuple(bridge_goals)
    program = ConjunctiveProgram(goals, variables)
    executor = SigmaPBAExecutor(
        sources=sigma_sources, facts=tuple(facts), scope="eop", allowed_rules=frozenset({rule}))
    result = executor.execute(
        program, max_hops=len(goals), max_environments=config.max_environments,
        max_evidence_bytes=config.max_evidence_bytes)
    if not executor.reopen(
            program, result, max_hops=len(goals), max_environments=config.max_environments,
            max_evidence_bytes=config.max_evidence_bytes):
        return "abstain", (), result.environments_created, "sigma_reopening_failed"
    candidate_maps = tuple({item.binding.claim_id: item for item in pool}
                           for pool in candidates_by_node)
    environments = tuple(tuple(candidate_maps[index][claim_id]
                               for index, claim_id in enumerate(output.values))
                         for output in result.outputs)
    return result.state, environments, result.environments_created, result.reason


def _certificate(*, question: str, graph: ObligationGraph,
                 bindings: tuple[WitnessBinding, ...], bridges: tuple[WitnessedBridge, ...],
                 closures: tuple[JoinClosure, ...], text: str, state: str,
                 config: ExplanatoryProofConfig) \
        -> ExplanatoryProofCertificate:
    binding_digest = _sha_bytes(_canonical([item.__dict__ for item in bindings]))
    bridge_digest = _sha_bytes(_canonical([item.__dict__ for item in bridges]))
    closure_digest = _sha_bytes(_canonical([item.__dict__ for item in closures]))
    config_digest = _sha_bytes(_canonical(config.__dict__))
    payload = {"rule": RULE, "q": _sha_text(question), "g": graph.digest,
              "w": binding_digest, "b": bridge_digest, "j": closure_digest,
              "a": _sha_text(text),
               "c": config_digest, "s": state}
    digest = _sha_bytes(_canonical(payload))
    return ExplanatoryProofCertificate(
        payload["q"], graph.digest, binding_digest, bridge_digest, closure_digest,
        payload["a"], config_digest, state, digest)


def _empty_result(*, state: str, question: str, graph: ObligationGraph,
                  residual: tuple[str, ...], environments: int = 0,
                  alternatives: tuple[tuple[str, ...], ...] = (),
                  config: ExplanatoryProofConfig = ExplanatoryProofConfig()) \
        -> ExplanatoryProofResult:
    certificate = _certificate(
        question=question, graph=graph, bindings=(), bridges=(), closures=(),
        text="", state=state,
        config=config)
    return ExplanatoryProofResult(
        state, "", graph, (), (), (), alternatives, residual, 0, environments, certificate)


def solve_explanatory_obligations(*, question: str,
                                  intents: tuple[ExplanatoryIntent, ...],
                                  sources: tuple[ExplanatorySource, ...],
                                  config: ExplanatoryProofConfig = ExplanatoryProofConfig()) \
        -> ExplanatoryProofResult:
    if not isinstance(config, ExplanatoryProofConfig):
        raise TypeError("config must be ExplanatoryProofConfig")
    try:
        graph = compile_obligation_graph(question, intents, sources)
    except ValueError as exc:
        empty = ObligationGraph((), _sha_text("invalid-eop-graph"))
        return _empty_result(state="unsupported", question=question, graph=empty,
                             residual=(str(exc),), config=config)
    claim_sources = tuple(ClaimSource.seal(item.source_id, item.text) for item in sources)
    try:
        claims = extract_authorized_claims(claim_sources)
    except ValueError as exc:
        return _empty_result(state="unsupported", question=question, graph=graph,
                             residual=(str(exc),), config=config)
    universe = _candidate_universe(graph, claims, sources)
    pool_conflicts = _pool_contradictions(universe)
    if pool_conflicts:
        alternatives = tuple(sorted({signature for values in pool_conflicts.values()
                                     for signature in values}))
        return _empty_result(
            state="contested", question=question, graph=graph,
            residual=tuple(f"pool_contradiction:{item}"
                           for item in sorted(pool_conflicts)),
            alternatives=alternatives, config=config)
    offered = _candidates(graph, claims, sources, config)
    missing = tuple(node.obligation_id for node in graph.nodes
                    if node.closure_mode == "witness" and not offered[node.obligation_id])
    if missing:
        return _empty_result(state="unsupported", question=question, graph=graph,
                             residual=tuple(f"missing_witness:{item}" for item in missing),
                             config=config)

    sigma_state, sigma_environments, examined, sigma_reason = _sigma_environments(
        graph, offered, sources, config)
    if sigma_state == "abstain" and not sigma_environments:
        reason = ("environment_budget_exhausted" if "budget" in sigma_reason
                  else "no_complete_bridged_environment")
        return _empty_result(state="abstain", question=question, graph=graph,
                             residual=(reason,), environments=examined, config=config)
    complete = []
    for environment in sigma_environments:
        bindings = tuple(item.binding for item in environment)
        bridges = _environment_bridges(
            bindings, graph, max_hops=config.max_bridge_hops)
        if bridges is None:
            continue
        closures = _environment_closures(bindings, graph)
        if closures is None:
            continue
        text = _proof_text(bindings)
        unique_claims = {(item.source_id, item.source_span) for item in bindings}
        if len(unique_claims) > config.max_claims:
            continue
        size = len(text.encode("utf-8"))
        if size > config.max_output_bytes:
            continue
        if not all(item.verify({source.source_id: source for source in sources})
                   for item in bindings):
            continue
        complete.append((_answer_signature(environment), size, len(unique_claims),
                         text, bindings, bridges, closures))
    if not complete:
        return _empty_result(state="abstain", question=question, graph=graph,
                             residual=("no_complete_bridged_environment",),
                             environments=examined, config=config)
    signatures = tuple(sorted(set(item[0] for item in complete)))
    if len(signatures) != 1:
        return _empty_result(state="contested", question=question, graph=graph,
                             residual=("complete_environments_disagree",),
                             environments=examined, alternatives=signatures,
                             config=config)
    winner = min(complete, key=lambda item: (item[1], item[2], item[3], item[4]))
    _signature, size, _claims, text, bindings, bridges, closures = winner
    bindings = tuple(sorted(bindings))
    bridges = tuple(sorted(bridges))
    closures = tuple(sorted(closures))
    certificate = _certificate(
        question=question, graph=graph, bindings=bindings, bridges=bridges,
        closures=closures, text=text, state="resolved", config=config)
    return ExplanatoryProofResult(
        "resolved", text, graph, bindings, bridges, closures, signatures, (), size,
        examined, certificate)


__all__ = [
    "ExplanatoryIntent", "ExplanatoryProofCertificate", "ExplanatoryProofConfig",
    "ExplanatoryProofResult", "ExplanatorySource", "JoinClosure", "ObligationGraph",
    "ObligationNode",
    "RULE", "WitnessBinding", "WitnessedBridge", "compile_obligation_graph",
    "solve_explanatory_obligations",
]
