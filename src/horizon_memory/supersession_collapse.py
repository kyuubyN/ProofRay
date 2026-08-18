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
72 value-revision scenarios, `lab/dataset_chat/run_supersession_collapse_pilot.py`, mirrored by
`lab/supersession_collapse.py`, the twin research-side implementation this Core module was ported
from)** -- report honestly, do not oversell, and this section was already corrected once (see
git history): a first version of this module's own anchor detection reused D48's `_anchors()`,
which tagged a sentence's own leading capitalized word as if it were a proper noun ("Final
answer?" -> spurious anchor "final") -- confirmed as the direct cause of a real false exclusion
in English test data. Fixed by computing anchors locally from `observe_raw_text` only, which this
Core module already did from the start (the bug was research-side only) -- but re-measuring after
fixing the research twin changed the honest picture reported here:
- **No language/budget/noise combination currently clears the pre-registered decision rule**
  (>=15pp distractor-token-containment reduction, <=5pp lax-containment reduction, no
  fill_fraction collapse). The closest is clean-text Chinese at a 1024-byte budget (distractor
  containment -12.5pp, lax containment -1.4pp) -- real, safe, directionally right, short of the
  bar. Every measured combination is now either a small-but-safe positive or a true no-op, never
  a large content-losing negative -- the earlier apparent Portuguese "win" was partly an artifact
  of the anchor bug and did not survive the fix at full honesty.
- Chinese was previously reported as never firing at all (CJK text has no letter-casing, so a
  capitalization-based entity signal is structurally blind to it). Fixed with a character-bigram
  anchor fallback for CJK text (plus embedded Latin words/numbers) -- Chinese now detects and
  resolves groups on every tested variant, with zero abstention.
- A generality check against ORDINARY (non-revision) `dataset_chat` scenarios
  (`lab/dataset_chat/check_supersession_false_positives.py`, 1290 scenario/variant pairs) found an
  **11.01% false-positive exclusion rate** (up from 9.46% before the CJK fix, because Chinese
  scenarios can now be false-positived too, where they were previously immune by being blind) --
  e.g. a scenario mentioning two different services' two different port numbers was wrongly
  collapsed as if it were one revised value. This is why this module is never called by default:
  it should only be applied to input a caller has reason to believe is single-fact-revision-
  shaped, not to arbitrary multi-fact evidence.

Hard rule: this module never accepts a `distractors`/`gold_answer`-shaped parameter. A caller
measuring against known-correct answers must do so outside this module's call boundary.
"""
from __future__ import annotations

import re
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

# CJK text has no letter-casing, so `observe_raw_text.entities` (built on capitalization) is
# structurally empty for it -- character bigrams are the established fallback for CJK anchor
# detection in this project (see `lab/supersession_collapse.py`'s identical fix), plus embedded
# Latin words/numbers (Chinese chat routinely embeds product names/technical terms verbatim).
_CJK_CHAR = re.compile(r"[一-鿿㐀-䶿]")
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_+.'-]*")


def _is_cjk(text: str) -> bool:
    return bool(_CJK_CHAR.search(text))


def _cjk_anchors(text: str) -> frozenset[str]:
    chars = [ch for ch in text if not ch.isspace()]
    bigrams = {"".join(pair) for pair in zip(chars, chars[1:])
              if _CJK_CHAR.match(pair[0]) and _CJK_CHAR.match(pair[1])}
    latin_words = {match.group().casefold() for match in _LATIN_WORD.finditer(text)
                  if len(match.group()) >= 2}
    channels = observe_raw_text(text)
    return frozenset(bigrams) | frozenset(latin_words) | frozenset(channels.numbers)


def _text_anchors(text: str) -> frozenset[str]:
    if _is_cjk(text):
        return _cjk_anchors(text)
    channels = observe_raw_text(text)
    return frozenset(channels.entities) | frozenset(channels.numbers)


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
    question_anchors = _text_anchors(question)
    candidates: list[tuple[EvidenceItem, frozenset[str], object]] = []
    for item in items:
        if not item.content or item.parent_sha256 is None or item.content_span is None:
            continue
        channels = observe_raw_text(item.content)
        anchors = _text_anchors(item.content)
        if not anchors:
            continue
        lexical = frozenset(channels.lexical)
        # `.lexical` merges an entire contiguous CJK run into one token (no whitespace to split
        # on), so plain Jaccard against the question is almost always zero for CJK text even when
        # the claim is genuinely on-topic -- the anchor-overlap bonus is what actually gates
        # relevance there, mirroring `lab/supersession_collapse.py`'s identical `_relevance` fix.
        relevance = _jaccard(lexical, question_lexical)
        if anchors & question_anchors:
            relevance += 0.35
        if relevance <= relevance_floor:
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
