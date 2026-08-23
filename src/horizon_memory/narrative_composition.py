# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic multi-fact narrative composition -- opt-in, never wired into any default path.

**Not wired into `OpenTextHorizonMemory`, `answer_atomic_relation_en/pt`, `TypedCausalExecutor`, or
any other production entry point.** A caller must import and call these functions explicitly,
already holding the `TypedCausalFact`s it wants composed and already knowing they share one real
`(subject, predicate)` fiber (e.g. via `TypedCausalExecutor.fibers`, or any other source of
correctly-linked typed facts).

**What this module solves, and what it deliberately does not.** Turning a question + already-
attested typed facts into one coherent multi-sentence narrative splits into two separable
problems: (1) which facts describe the *same* real-world thing despite different surface wording
(entity/fiber linking), and (2) given facts that ARE correctly linked, how to order and compose
them into one faithful, non-duplicating narrative. This module solves only (2). Problem (1)
remains the open, hard problem this project already documents at length (`supersession_collapse`'s
own recorded false-positive/false-negative history; D146/D147). This module never invents a
fiber/subject match -- it only classifies and composes facts a caller has already linked.

**Composition is pure span arithmetic, never generation.** `realize_fact` slices a
`TypedCausalFact`'s own literal, attested `source_span`: it locates the fact's already-known
`.subject` verbatim inside its own span and treats everything after it as the clause. Nothing here
synthesizes fresh sentence content -- every word in a rendered narrative was already written by the
source. Joining is restricted to symmetric literal-span concatenation (a closed connector word
between two already-attested clauses), never substitution into another clause's structure: Lebanoff
et al. (2019), "Analyzing Sentence Fusion in Abstractive Summarization" (arXiv:1910.00203, Table 3),
measured "Balanced Concat" joins at 82.55% faithful versus "Replacement" joins at ~53% -- this module
must never grow a Replacement-style join, and this boundary is deliberate.

**Four relations are structurally justified, each read directly off `TypedCausalFact`'s own
fields, never inferred from text:**
- CAUSE: `causes` names a real, already-existing graph edge.
- CONTRAST: two facts share a `(subject, predicate)` fiber but report different content -- the
  same conflict `supersession_collapse.py`'s own orbit machinery already detects, ordered by clock
  when available.
- SEQUENCE: same subject, different fiber, no causal edge, but a distinct `event_time` on both
  facts -- the classical DICE/SDRT Narration rule `Narration(a,b) -> e_a < e_b` (Asher &
  Lascarides; refined by Altshuler & Varasdi 2015), applied to a clock field already present.
- JOINT: same subject, different fiber, no ordering signal -- a plain list-conjunction.

Anything that doesn't structurally match one of these four returns NONE; the caller renders those
facts as separate sentences, never a forced relation.

**Ordering an arbitrary collection of facts (not just one pair)** builds a graph, never a trained
RS-tree parser: an edge exists only where the pairwise rule above finds one, so every edge in the
plan is individually provable. Directed edges (CAUSE/CONTRAST/SEQUENCE) are topologically sorted;
a genuine cycle (possible only across facts drawn from different fibers/executors, where two
independent signals disagree about order) is reported `contested` rather than resolved by an
arbitrary tie-break -- the same "abstain over guess" contract Sigma-PBA/H-DEM already enforce
everywhere else in this project, applied here to composition instead of lookup.

**`current_value_fact` corrects a real gap found against real data**: a 2026-08-22 oracle-fiber
probe against `lab/dataset_chat/domains_lh_{en,pt}` (48 real long-horizon revision scenarios, using
each scenario's own `grounding_facts` with a fixed placeholder subject standing in for a
not-yet-built entity-linking stage) found the topologically-last fact in a revision chain is not
always the one that states the value -- some sources append a trailing confirmation/reaction
clause with no value content after the fact that already gives it (e.g. "...so it's $30, but now
Parker agreed." ends on "Parker agreed.", not the $30). Walking the chain from most-recent to
least-recent and returning the first fact whose clause carries a real anchor (a number or a proper
noun, via the same `raw_causal_channels.observe_raw_text` channel `proof_dossier.py`'s
`specificity_bonus`/`anchor_bonus` already use for the identical purpose) fixed 2 of 3 diagnosed
cases on that same probe (14/24 -> 16/24 EN correct-current-value identification), with the one
residual case being a genuinely different, already-documented issue (a differently-worded
restatement of the same value, not a value-less trailing clause).

**Honest scope, not yet cleared**: no frozen holdout has been opened for this module; the
oracle-fiber probe above is diagnostic, not a promotion gate. It stays research-namespace-only
(`horizon_memory.research`), not the stable top-level namespace, matching the same distinction
already drawn for the Portuguese atomic-relations pack (stable-but-unconfirmed vs
holdout-confirmed). Entity/fiber linking from raw, unstructured text remains unsolved; this module
does not attempt it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from itertools import combinations

from .raw_causal_channels import observe_raw_text
from .typed_causal_program import TypedCausalFact

# ---------------------------------------------------------------------------
# Realized clauses and same-subject conjunction (narrowest case: 2+ already-verified facts
# sharing one subject, coordinated with subject ellipsis).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RealizedFact:
    """One already-realized, already-verified clause. `predicate_text` is everything after the
    subject (verb phrase + object), with no trailing sentence punctuation and no leading/trailing
    whitespace -- the caller is responsible for that normalization."""
    subject: str
    predicate_text: str
    fact_id: int
    source_span: tuple[int, int]

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise ValueError("RealizedFact.subject must be non-empty")
        if not self.predicate_text.strip():
            raise ValueError("RealizedFact.predicate_text must be non-empty")


@dataclass(frozen=True)
class AggregatedNarrative:
    text: str
    fact_ids: tuple[int, ...]
    source_spans: tuple[tuple[int, int], ...]
    rule: str


def aggregate_same_subject_facts(
    facts: tuple[RealizedFact, ...], *, conjunction: str = "and",
) -> AggregatedNarrative | None:
    """Coordinate 2+ facts sharing one subject into a single sentence, eliding the repeated
    subject in every clause after the first. Returns `None` (never guesses) when fewer than 2
    facts are given, or the facts do not all share the same subject (case-insensitive)."""
    if len(facts) < 2:
        return None
    subject = facts[0].subject
    if any(fact.subject.casefold() != subject.casefold() for fact in facts[1:]):
        return None
    predicates = [fact.predicate_text.strip().rstrip(".") for fact in facts]
    if len(predicates) == 2:
        joined = f"{predicates[0]} {conjunction} {predicates[1]}"
    else:
        joined = ", ".join(predicates[:-1]) + f", {conjunction} {predicates[-1]}"
    text = f"{subject} {joined}."
    return AggregatedNarrative(
        text=text,
        fact_ids=tuple(fact.fact_id for fact in facts),
        source_spans=tuple(fact.source_span for fact in facts),
        rule="same_subject_conjunction_reduction",
    )


# ---------------------------------------------------------------------------
# Realizing a TypedCausalFact into a RealizedFact -- pure span arithmetic, never generation.
# ---------------------------------------------------------------------------

_LEADING_SKIP = re.compile(r"^(?:the|a|an|and|but|so|then)\s+", re.IGNORECASE)
_MIN_PREDICATE_LENGTH = 2


def realize_fact(fact: TypedCausalFact, source_text: str) -> RealizedFact | None:
    """Slice `fact`'s own literal source span into a `RealizedFact`, never inventing text.

    Returns `None` (fail closed) whenever the subject cannot be found verbatim in the fact's own
    span, or nothing usable remains after it.
    """
    start, end = fact.source_span
    if not (0 <= start < end <= len(source_text)):
        return None
    clause_text = source_text[start:end]

    match = re.search(r"\b" + re.escape(fact.subject) + r"\b", clause_text, re.IGNORECASE)
    if match is None:
        return None

    remainder = clause_text[match.end():]
    remainder = _LEADING_SKIP.sub("", remainder.lstrip(" ,;:"))
    predicate_text = remainder.strip()
    if len(predicate_text) < _MIN_PREDICATE_LENGTH:
        return None

    return RealizedFact(
        subject=fact.subject, predicate_text=predicate_text,
        fact_id=fact.fact_id, source_span=fact.source_span,
    )


# ---------------------------------------------------------------------------
# Pairwise discourse relation classification.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DiscourseFact:
    """A `RealizedFact` plus the minimal typed metadata needed to classify its relation to
    another `DiscourseFact`. Every field either already exists on `TypedCausalFact` (`fiber_key`
    from (subject, predicate), `clock` from (version, event_time, observed_at), `causes`) or is
    directly derivable from it."""
    realized: RealizedFact
    fiber_key: tuple[str, str] | None = None
    clock: tuple[int, int, int] | None = None
    causes: frozenset[int] = field(default_factory=frozenset)


class DiscourseRelation(str, Enum):
    CAUSE = "cause"
    CONTRAST = "contrast"
    SEQUENCE = "sequence"
    JOINT = "joint"
    NONE = "none"


@dataclass(frozen=True)
class ClassifiedPair:
    relation: DiscourseRelation
    # Canonical order for rendering (cause-before-effect, older-before-newer). Only meaningful
    # when relation != NONE; for NONE the order is simply the input order, unused by any renderer.
    ordered: tuple[DiscourseFact, DiscourseFact]


def classify_relation(a: DiscourseFact, b: DiscourseFact) -> ClassifiedPair:
    a_id, b_id = a.realized.fact_id, b.realized.fact_id
    if a_id in b.causes:
        return ClassifiedPair(DiscourseRelation.CAUSE, (a, b))
    if b_id in a.causes:
        return ClassifiedPair(DiscourseRelation.CAUSE, (b, a))

    if a.fiber_key is not None and a.fiber_key == b.fiber_key:
        same_content = (a.realized.predicate_text.strip().casefold() ==
                        b.realized.predicate_text.strip().casefold())
        if same_content:
            return ClassifiedPair(DiscourseRelation.NONE, (a, b))
        if a.clock is not None and b.clock is not None and a.clock != b.clock:
            ordered = (a, b) if a.clock < b.clock else (b, a)
            return ClassifiedPair(DiscourseRelation.CONTRAST, ordered)
        return ClassifiedPair(DiscourseRelation.CONTRAST, (a, b))

    if a.realized.subject.casefold() == b.realized.subject.casefold():
        if a.clock is not None and b.clock is not None:
            a_event_time, b_event_time = a.clock[1], b.clock[1]
            if a_event_time != b_event_time:
                ordered = (a, b) if a_event_time < b_event_time else (b, a)
                return ClassifiedPair(DiscourseRelation.SEQUENCE, ordered)
        return ClassifiedPair(DiscourseRelation.JOINT, (a, b))

    return ClassifiedPair(DiscourseRelation.NONE, (a, b))


_RULE_NAME = {
    DiscourseRelation.CAUSE: "cause_result_connective",
    DiscourseRelation.SEQUENCE: "sequence_temporal_order",
    DiscourseRelation.CONTRAST: "contrast_supersession",
}


def connector_style(relation: DiscourseRelation, *, language: str = "en") -> tuple[str, str, bool]:
    """The (leading_word, connector_word, include_second_subject) style for one relation/language.

    `include_second_subject` is True only for CAUSE, the one relation that does not guarantee both
    facts share a subject -- every other relation elides the repeated subject in its second clause.
    """
    pt = language == "pt"
    if relation == DiscourseRelation.CAUSE:
        return "", ("portanto" if pt else "so"), True
    if relation == DiscourseRelation.SEQUENCE:
        return ("Primeiro, " if pt else "First, "), ("depois" if pt else "then"), False
    if relation == DiscourseRelation.CONTRAST:
        return ("Antes, " if pt else "Previously, "), ("mas agora" if pt else "but now"), False
    if relation == DiscourseRelation.JOINT:
        return "", ("e" if pt else "and"), False
    raise ValueError(f"no connector style for {relation!r}")


def render_pair(pair: ClassifiedPair, *, language: str = "en") -> AggregatedNarrative | None:
    """Render a classified pair into one coordinated sentence. Returns `None` for NONE."""
    if pair.relation == DiscourseRelation.NONE:
        return None
    first, second = pair.ordered

    if pair.relation == DiscourseRelation.JOINT:
        return aggregate_same_subject_facts(
            (first.realized, second.realized), conjunction="e" if language == "pt" else "and")

    first_text = first.realized.predicate_text.strip().rstrip(".")
    second_text = second.realized.predicate_text.strip().rstrip(".")
    lead_word, connector, include_second_subject = connector_style(pair.relation, language=language)
    second_clause = (f"{second.realized.subject} {second_text}" if include_second_subject
                     else second_text)
    text = f"{lead_word}{first.realized.subject} {first_text}, {connector} {second_clause}."

    return AggregatedNarrative(
        text=text,
        fact_ids=(first.realized.fact_id, second.realized.fact_id),
        source_spans=(first.realized.source_span, second.realized.source_span),
        rule=_RULE_NAME[pair.relation],
    )


def build_discourse_facts(
    facts: tuple[TypedCausalFact, ...], source_text: str,
) -> tuple[DiscourseFact, ...]:
    """Wire real `TypedCausalFact` objects into `DiscourseFact`s this module can classify.

    A fact whose clause cannot be realized (`realize_fact` returns `None` -- e.g. its subject
    isn't found verbatim in its own source span) is silently skipped, never fabricated.
    """
    result = []
    for fact in facts:
        realized = realize_fact(fact, source_text)
        if realized is None:
            continue
        result.append(DiscourseFact(
            realized=realized,
            fiber_key=(fact.subject, fact.predicate),
            clock=(fact.version, fact.event_time, fact.observed_at),
            causes=frozenset(fact.causes),
        ))
    return tuple(result)


# ---------------------------------------------------------------------------
# Generalizing from one pair to an arbitrary collection: a provable graph, topological order
# within each connected component, and non-duplicating fused rendering.
# ---------------------------------------------------------------------------

_DIRECTED = frozenset({DiscourseRelation.CAUSE, DiscourseRelation.CONTRAST,
                       DiscourseRelation.SEQUENCE})
_VALUE_REVISION = frozenset({DiscourseRelation.CONTRAST, DiscourseRelation.SEQUENCE})


@dataclass(frozen=True)
class NarrativeComponent:
    facts: tuple[DiscourseFact, ...]  # in final render order; unordered/contested if `contested`
    edges: tuple[tuple[DiscourseFact, DiscourseFact, DiscourseRelation], ...]
    contested: bool = False


@dataclass(frozen=True)
class NarrativePlan:
    components: tuple[NarrativeComponent, ...]  # in original input order


def plan_narrative(facts: tuple[DiscourseFact, ...]) -> NarrativePlan:
    """Classify every pair and group facts into connected components, each internally ordered.

    Never invents a relation: an edge exists only where `classify_relation` itself finds one.
    """
    directed_edges: dict[tuple[int, int], DiscourseRelation] = {}
    undirected_edges: set[frozenset[int]] = set()
    adjacency: dict[int, set[int]] = {index: set() for index in range(len(facts))}

    for i, j in combinations(range(len(facts)), 2):
        pair = classify_relation(facts[i], facts[j])
        if pair.relation == DiscourseRelation.NONE:
            continue
        first_index = i if pair.ordered[0] is facts[i] else j
        second_index = j if first_index == i else i
        adjacency[i].add(j)
        adjacency[j].add(i)
        if pair.relation in _DIRECTED:
            directed_edges[(first_index, second_index)] = pair.relation
        else:
            undirected_edges.add(frozenset((i, j)))

    visited: set[int] = set()
    components: list[NarrativeComponent] = []
    for start in range(len(facts)):
        if start in visited:
            continue
        stack, member_indices = [start], set()
        while stack:
            node = stack.pop()
            if node in member_indices:
                continue
            member_indices.add(node)
            stack.extend(adjacency[node] - member_indices)
        visited |= member_indices

        component_edges = [
            (facts[a], facts[b], relation) for (a, b), relation in directed_edges.items()
            if a in member_indices and b in member_indices
        ]
        for edge in undirected_edges:
            a, b = tuple(edge)
            if a in member_indices:
                component_edges.append((facts[a], facts[b], DiscourseRelation.JOINT))

        ordered_indices = sorted(member_indices)
        ordered_facts, contested = _topological_order(
            ordered_indices, {(a, b) for a, b in directed_edges if a in member_indices})
        components.append(NarrativeComponent(
            facts=tuple(facts[index] for index in ordered_facts),
            edges=tuple(component_edges),
            contested=contested,
        ))

    # `components` is already in original input order: the outer loop visits candidate start
    # indices in increasing order and only opens a new component for an index not already
    # claimed, so each component is appended in order of its own smallest member index.
    return NarrativePlan(components=tuple(components))


def _topological_order(
    indices: list[int], directed_pairs: set[tuple[int, int]],
) -> tuple[list[int], bool]:
    """Kahn's algorithm restricted to `indices`; returns (order, contested)."""
    in_degree = {index: 0 for index in indices}
    successors: dict[int, list[int]] = {index: [] for index in indices}
    for a, b in directed_pairs:
        successors[a].append(b)
        in_degree[b] += 1

    ready = sorted(index for index in indices if in_degree[index] == 0)
    order: list[int] = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for successor in sorted(successors[node]):
            in_degree[successor] -= 1
            if in_degree[successor] == 0:
                ready.append(successor)
                ready.sort()
    if len(order) != len(indices):
        return sorted(indices), True  # a real cycle: report contested, never guess an order
    return order, False


def current_value_fact(component: NarrativeComponent) -> DiscourseFact | None:
    """Identify which fact in a same-fiber revision chain actually states the CURRENT value.

    The topologically-last fact in a CONTRAST/SEQUENCE chain is not always the one that states
    the value: sources sometimes append a trailing confirmation/reaction clause with no value
    content after the fact that already gives it (e.g. "...so it's $30, but now Parker agreed."
    ends on "Parker agreed.", not the $30). Walking the component's own already-computed order
    from most-recent to least-recent and returning the first fact whose predicate text carries a
    real anchor (a number or a proper noun, via `raw_causal_channels.observe_raw_text`) fixes
    this without inventing anything.

    Falls back to the literal last fact when nothing in the chain carries any anchor at all.
    Returns `None` for a component with no value-revision signal at all (a pure JOINT listing has
    no single "current value"; a pure CAUSE chain explains an effect, it does not supersede a
    prior value; a `contested` component has no safe order to read a "last" from).
    """
    if component.contested or len(component.facts) < 2:
        return None
    if not any(relation in _VALUE_REVISION for _, _, relation in component.edges):
        return None
    for fact in reversed(component.facts):
        channels = observe_raw_text(fact.realized.predicate_text)
        if channels.entities or channels.numbers:
            return fact
    return component.facts[-1]


@dataclass(frozen=True)
class RenderedNarrative:
    text: str
    fact_ids: tuple[int, ...]
    source_spans: tuple[tuple[int, int], ...]


def render_narrative(plan: NarrativePlan, *, language: str = "en") -> RenderedNarrative:
    """Render every component in original order; concatenate into one narrative.

    A `contested` component, or a topologically-adjacent pair with no direct edge between them,
    renders as separate sentences rather than fabricating a connector.
    """
    sentences: list[str] = []
    fact_ids: list[int] = []
    source_spans: list[tuple[int, int]] = []

    for component in plan.components:
        for narrative in _render_component(component, language=language):
            sentences.append(narrative.text)
            fact_ids.extend(narrative.fact_ids)
            source_spans.extend(narrative.source_spans)

    return RenderedNarrative(
        text=" ".join(sentences), fact_ids=tuple(fact_ids), source_spans=tuple(source_spans))


def _standalone(fact: DiscourseFact, *, rule: str) -> AggregatedNarrative:
    text = f"{fact.realized.subject} {fact.realized.predicate_text.strip().rstrip('.')}."
    return AggregatedNarrative(
        text=text, fact_ids=(fact.realized.fact_id,), source_spans=(fact.realized.source_span,),
        rule=rule,
    )


def _render_component(component: NarrativeComponent, *, language: str) -> tuple[AggregatedNarrative, ...]:
    if len(component.facts) == 1:
        return (_standalone(component.facts[0], rule="standalone_realization"),)

    if component.contested:
        return tuple(
            _standalone(fact, rule="contested_order_standalone_fallback")
            for fact in component.facts
        )

    direct_relations: dict[frozenset[int], DiscourseRelation] = {
        frozenset((a.realized.fact_id, b.realized.fact_id)): relation
        for a, b, relation in component.edges
    }
    all_joint_same_subject = (
        all(relation == DiscourseRelation.JOINT for _, _, relation in component.edges) and
        len({fact.realized.subject.casefold() for fact in component.facts}) == 1
    )
    if all_joint_same_subject:
        aggregated = aggregate_same_subject_facts(tuple(fact.realized for fact in component.facts))
        if aggregated is not None:
            return (aggregated,)
        return tuple(_standalone(fact, rule="standalone_realization") for fact in component.facts)

    return _render_directed_chain(component.facts, direct_relations, language=language)


def _render_directed_chain(
    facts: tuple[DiscourseFact, ...],
    direct_relations: dict[frozenset[int], DiscourseRelation],
    *, language: str,
) -> tuple[AggregatedNarrative, ...]:
    """Fuse each maximal run of directly-related, topologically-adjacent facts into ONE sentence
    -- every fact's text appears exactly once. A run boundary (no direct edge between two
    adjacent facts) starts a new, separate sentence.
    """
    narratives: list[AggregatedNarrative] = []
    index = 0
    total = len(facts)
    while index < total:
        run = [facts[index]]
        cursor = index
        while cursor + 1 < total:
            key = frozenset((facts[cursor].realized.fact_id, facts[cursor + 1].realized.fact_id))
            if key not in direct_relations:
                break
            run.append(facts[cursor + 1])
            cursor += 1

        if len(run) == 1:
            narratives.append(_standalone(run[0], rule="no_direct_edge_standalone"))
        else:
            first_key = frozenset((run[0].realized.fact_id, run[1].realized.fact_id))
            lead_word, _, _ = connector_style(direct_relations[first_key], language=language)
            first_fact = run[0]
            first_text = first_fact.realized.predicate_text.strip().rstrip(".")
            text = f"{lead_word}{first_fact.realized.subject} {first_text}"
            fact_ids = [first_fact.realized.fact_id]
            spans = [first_fact.realized.source_span]
            for position in range(1, len(run)):
                step_key = frozenset(
                    (run[position - 1].realized.fact_id, run[position].realized.fact_id))
                relation = direct_relations[step_key]
                _, connector, include_second_subject = connector_style(relation, language=language)
                clause_fact = run[position]
                clause_text = clause_fact.realized.predicate_text.strip().rstrip(".")
                clause = (f"{clause_fact.realized.subject} {clause_text}"
                         if include_second_subject else clause_text)
                text += f", {connector} {clause}"
                fact_ids.append(clause_fact.realized.fact_id)
                spans.append(clause_fact.realized.source_span)
            text += "."
            narratives.append(AggregatedNarrative(
                text=text, fact_ids=tuple(fact_ids), source_spans=tuple(spans),
                rule="directed_chain_composition",
            ))
        index = cursor + 1
    return tuple(narratives)
