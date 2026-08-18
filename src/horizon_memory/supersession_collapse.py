# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Hard supersession collapse -- opt-in exclusion of superseded value restatements.

**Not wired into `SemanticRouter`, `HorizonVerifier`, `EvidencePack.budgeted_items()`, or any
default routing/ranking path.** This is a standalone function a caller applies explicitly, after
routing/verification produced a real, verified `tuple[EvidenceItem, ...]`, when they know their
input may contain a fact restated multiple times with each restatement superseding the last (a
launch date revised 10->15->20->25, a decision reversed, a status updated) and want the stale
values actively excluded rather than left for a downstream renderer to include alongside the
current one.

**Resolution** reuses `typed_causal_program.TypedCausalExecutor` UNMODIFIED (holdout-validated,
649/649, in the original Q-HDRE V51-V55 line) -- this module never edits that executor's
resolution logic, only feeds it. Detection uses only signals `raw_causal_channels.observe_raw_text`
already computes (entities/numbers as "anchors", lexical tokens, polarity, modality) -- it is
NOT a general subject/predicate/value extractor. Every prior attempt at a general open-text
extractor failed on real text in this project's research history (see `research_vault/` D81:
0/536 real MemGym-DR; D101/H-WRR: 7.02% real closure after 2,400/2,400 on generated text).

**Measured scope and limits (research pass, `lab/dataset_chat/domains_lh_{pt,en,zh}`,
72 value-revision scenarios, `lab/dataset_chat/run_supersession_collapse_pilot.py`)** -- report
honestly, do not oversell:
- Passes a pre-registered decision rule (>=15pp distractor-token-containment reduction, <=5pp
  lax-containment reduction, no fill_fraction collapse) only on clean-text Portuguese at a
  1024-byte budget (distractor containment 0.764->0.562, lax containment 0.601->0.573).
- Fails the same rule under noise (PT), on English (cuts real content along with stale values),
  and never fires at all on Chinese (CJK text has no letter-casing, so the entity/anchor signal
  this module depends on is structurally blind to it).
- A generality check against ORDINARY (non-revision) `dataset_chat` scenarios
  (`lab/dataset_chat/check_supersession_false_positives.py`, 1290 scenario/variant pairs) found a
  **9.46% false-positive exclusion rate** -- e.g. a scenario mentioning two different services'
  two different port numbers was wrongly collapsed as if it were one revised value. This is why
  this module is never called by default: it should only be applied to input a caller has reason
  to believe is single-fact-revision-shaped, not to arbitrary multi-fact evidence.

Hard rule: this module never accepts a `distractors`/`gold_answer`-shaped parameter. A caller
measuring against known-correct answers must do so outside this module's call boundary.
"""
from __future__ import annotations

from dataclasses import dataclass

from .evidence import EvidenceItem
from .raw_causal_channels import observe_raw_text
from .typed_causal_program import (
    CausalSelector, TypedCausalExecutor, TypedCausalFact, TypedCausalProgram,
)

__all__ = ["DEFAULT_RELEVANCE_FLOOR", "SupersessionReport", "collapse_evidence_items"]

_SCOPE = "supersession"
# The items this module receives have already passed through real routing/relevance scoring
# (ClaimGenerator + SemanticRouter) upstream -- this floor is a cheap sanity backstop (any
# positive lexical overlap with the question), not a second hand-tuned relevance filter.
DEFAULT_RELEVANCE_FLOOR = 0.0


def _jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    union = left | right
    if not union:
        return 0.0
    return len(left & right) / len(union)


@dataclass(frozen=True)
class SupersessionReport:
    groups_detected: int
    resolved_groups: int
    abstained_by_reason: dict[str, int]
    superseded_keys: frozenset[tuple[int, tuple[int, int] | None]]


def collapse_evidence_items(items: tuple[EvidenceItem, ...], question: str, *,
                            relevance_floor: float = DEFAULT_RELEVANCE_FLOOR) \
        -> tuple[tuple[EvidenceItem, ...], SupersessionReport]:
    """Excludes `EvidenceItem`s that are superseded restatements of a value another, later item
    in the pool already carries. Fails closed: any group that doesn't cleanly resolve to a
    single current value excludes nothing. `items` should already be real, verified evidence
    (`EvidenceItem.parent_sha256`/`.content_span` populated, as `HorizonVerifier.verify()`
    already produces) -- items missing either are left untouched, never considered for exclusion.
    """
    question_lexical = frozenset(observe_raw_text(question).lexical)
    candidates: list[tuple[EvidenceItem, frozenset[str], object]] = []
    for item in items:
        if not item.content or item.parent_sha256 is None or item.content_span is None:
            continue
        channels = observe_raw_text(item.content)
        anchors = frozenset(channels.entities) | frozenset(channels.numbers)
        if not anchors:
            continue
        lexical = frozenset(channels.lexical)
        if _jaccard(lexical, question_lexical) <= relevance_floor:
            continue
        candidates.append((item, anchors, channels))

    if len(candidates) < 2:
        return items, SupersessionReport(0, 0, {}, frozenset())

    shared_anchors: frozenset[str] | None = None
    for _item, anchors, _channels in candidates:
        shared_anchors = anchors if shared_anchors is None else shared_anchors & anchors
    value_bearing = [(item, anchors - (shared_anchors or frozenset()), channels)
                     for item, anchors, channels in candidates]
    value_bearing = [row for row in value_bearing if row[1]]

    groups_detected = 1
    if len(value_bearing) < 2:
        return items, SupersessionReport(groups_detected, 0, {"insufficient_members": 1},
                                         frozenset())

    facts = []
    item_by_fact_id: dict[int, EvidenceItem] = {}
    for index, (item, value, channels) in enumerate(value_bearing):
        turn = item.fact_id
        fact = TypedCausalFact(
            fact_id=index, scope=_SCOPE, subject="tracked_fact", predicate="value",
            value=" ".join(sorted(value)), observed_at=turn, event_time=turn, version=1,
            polarity=-1 if channels.polarity == "negative" else 1,
            asserted=(channels.modality == "asserted"), event_id=f"turn:{turn}",
            source_id=f"{item.source}:{item.fact_id}:{item.content_span}",
            source_sha256=item.parent_sha256, source_span=item.content_span,
        )
        facts.append(fact)
        item_by_fact_id[fact.fact_id] = item
    facts = tuple(sorted(facts, key=lambda fact: fact.fact_id))

    result = TypedCausalExecutor(facts, _SCOPE).execute(
        TypedCausalProgram("LOOKUP", CausalSelector("tracked_fact", "value")))
    if result.state != "resolved":
        return items, SupersessionReport(groups_detected, 0, {result.reason: 1}, frozenset())

    winner = item_by_fact_id[result.fact_ids[0]]
    winner_key = (winner.fact_id, winner.content_span)
    winner_anchors = next(anchors for item, anchors, _ in value_bearing
                          if (item.fact_id, item.content_span) == winner_key)
    superseded_keys = frozenset(
        (item.fact_id, item.content_span) for item, anchors, _ in value_bearing
        if (item.fact_id, item.content_span) != winner_key and anchors != winner_anchors)

    kept = tuple(item for item in items
                if (item.fact_id, item.content_span) not in superseded_keys)
    report = SupersessionReport(groups_detected, 1, {}, superseded_keys)
    return kept, report
