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
- **PT clean and EN heavy_noise now clear the pre-registered decision rule** (>=15pp
  distractor-token-containment reduction, <=5pp lax-containment reduction, no fill_fraction
  collapse) -- the first combinations to do so in this module's history, after an anchor-primary
  relevance fix (2026-08-18, see below). Not every combination is clean: PT heavy_noise and EN
  clean now exceed the 5pp lax-loss ceiling -- the mechanism became more aggressive across the
  board, winning on some combinations and over-excluding on others, not a uniform improvement.
- Chinese was previously reported as never firing at all (CJK text has no letter-casing, so a
  capitalization-based entity signal is structurally blind to it), then as firing but unsafely
  (a character-bigram anchor fallback that saturated on combinatorial noise -- see the note above
  `_segment_zh`). With word-segmentation anchors: clean-text Chinese at 1024B moved to
  distractor -8.3pp / lax +0.0pp (was -12.5pp / -1.4pp with bigram anchors) -- less aggressive
  but essentially loss-free. **CJK's relevance path itself was later improved too (2026-08-18,
  same day as the anchor-primary fix below)**: the plain Jaccard fallback (structurally near-zero
  for CJK, since `.lexical` merges a whole contiguous CJK run into one token) was replaced with a
  self-built, BM25-style IDF computed directly over the segmented-word candidate pool -- ported
  from `lab/supersession_collapse.py`'s validated `_cjk_bm25_scores` (see `collapse_evidence_items`'s
  own IDF-pool comment for why a literal reuse of `MaterializedRawCausalSyndromeIndex`/
  `HorizonSearchEngine` doesn't work for CJK: their shared `.lexical` channel hardcodes a
  3-character minimum, built for Latin scripts, which silently drops nearly every real
  2-character Chinese word). Measured: false-positive rate on the 1,290-pair generality check
  fell 14.03% -> 13.72%, a real improvement over the word-segmentation-only Jaccard fallback, at
  the same primary-metric level (ZH clean distractor -8.3pp unchanged).
- **Anchor-primary relevance for non-CJK text (2026-08-18)**: the diagnosed root cause of PT's
  own `light_noise` gap (below) was fixed by making a claim's own anchor (once confirmed
  non-empty, which every claim reaching this check already is) sufficient for relevance on its
  own, for non-CJK text only -- removing the dependency on `observe_raw_text`'s English-only
  stopword list. This is a real, measured trade, not a clean win: the false-positive rate on the
  1,290-pair generality check against ORDINARY (non-revision) `dataset_chat` scenarios rose from
  **7.05% to 14.03%** (a claim carrying any real anchor is now treated as relevant regardless of
  topical connection to the question). Kept deliberately, decided by the project owner after
  seeing both numbers side by side: this module is never called by default (see above), so the
  doubled false-positive rate is an honest characterization of the mechanism, not a live
  production risk -- it only matters to a caller applying this outside the single-fact-revision
  shape it is scoped to.
- **Typed-anchor majority-vote filter (2026-08-18, plan item 2a)**: a group's competing values
  are now required to share a majority anchor TYPE (date/money/percent/cardinal/entity, see
  `_anchor_type`) before resolution proceeds -- a group mixing a date revision with an unrelated
  money mention is no longer treated as one value-revision. Filters to the majority type rather
  than requiring universal agreement, since a single noise claim (a greeting/exclamation carrying
  an incidental "entity" anchor, admitted by anchor-primary relevance purely because it has SOME
  anchor) must not poison an otherwise type-consistent group of real competing claims -- an
  all-or-nothing version was tried first and measured decisively worse (severe over-abstention)
  before the majority-vote fix. Net effect, same 1,290-pair generality check: false-positive rate
  **14.03% -> 11.16%** -- recovers roughly half the ground given up by the anchor-primary
  relevance change above, without giving up that change's own fix. On the per-language decision
  bars adopted the same day (ZH 15pp/5pp, PT/EN 12pp/5pp distractor-containment reduction /
  lax-containment loss): PT clean, EN light_noise, and EN heavy_noise now clear their bar -- the
  first non-CJK combinations in this module's history to do so.
- **ZH anchor stopwords (2026-08-18)**: prompted by a direct question -- do the KBs/dictionaries
  this project uses for other metrics also silently fail for Chinese? One concrete instance
  confirmed immediately: `lang/china/stop_words.txt` (sourced alongside the Jieba big
  dictionary) turned out to be a plain ENGLISH stopword list, unreferenced anywhere in code, not
  Chinese at all. A real, separate gap was then found and fixed: `_cjk_anchors` applied zero
  frequency filtering, so high-frequency internet slang/intensifiers specific to this corpus's
  casual register ("666", "cap"/"no", "直接", "卧槽", "家人们"...) were treated exactly like a
  genuine distinguishing value. A classical Chinese stopword list would NOT have caught this --
  the noise is corpus-specific slang, not grammar -- so `zh_anchor_stopwords.py` derives its list
  the same way `zh_word_dictionary.py` does: document frequency (>=5%) over the project's own
  `dataset_chat` ZH corpus (282 scenario/variant pairs), not a hand-curated linguistic list.
  **Honest result, not oversold**: real and targeted (anchor occurrence count fell 1911 -> 1393,
  -27%, on the diagnostic sample; aggregate false-positive rate 11.16% -> 10.93%), but it does
  NOT close the dominant remaining cause -- re-running the same diagnostic post-fix found the
  same 45 false-positive groups on ordinary ZH data, byte-identical in count, because most
  groups are actually driven by genuine (non-slang) vocabulary that naturally co-occurs across
  multiple turns of ONE coherent multi-turn story (e.g. a thesis/graduation narrative sharing
  "university"/"thesis"/"system"/"finally" across turns) -- a discourse-cohesion problem, not an
  anchor-vocabulary problem.
- **Item 2b (discourse window / turn-distance) measured before building, closed as inapplicable
  to this failure mode**: every one of the 45 ordinary-domain false-positive groups AND every
  genuine revision group in `domains_lh_zh` has pairwise turn-distance exactly 1 -- both false
  positives and real corrections are adjacent-turn pairs, so a distance-decay filter cannot
  discriminate between them (accepting distance-1 does nothing; rejecting it would block real
  adjacent-turn corrections just as often).
- **ZH correction gate, two paths (plan item 2c, 2026-08-18)**: a first version required an
  explicit correction marker ("不是"/"其实是"/"更新一下"/"等等"...) as a hard AND-gate for every
  CJK group. Real (false-positive rate 10.93% -> 8.14%) but over-conservative: genuine ZH
  corrections without formal marker language ("居然考的是第1章" -- a surprise particle, not "不
  是/其实是") were wrongly blocked, and the primary pilot metric got WORSE (ZH clean distractor
  cut -8.3pp -> -4.2pp, moving further from the decision bar). Revised into two paths after
  testing -- and refuting -- three more specific candidate signals for a marker-less "path B"
  (each tested directly against real data before being trusted, per this project's own
  discipline): (1) turn distance -- vacuous, see 2b above; (2) restricting resolution to
  digit-bearing anchor types (date/money/percent/cardinal) -- doesn't discriminate, 90% of real
  revision groups are ALSO entity-only, same as 62% of false-positive groups; (3) the size of
  each claim's own non-shared anchor set as a "a correction only introduces the new value"
  signal -- also refuted, real revision groups have a LARGER mean per-claim value-set (4.57)
  than false-positive groups (3.24), the opposite of the prediction. What DID hold: the raw
  presence/magnitude of `shared_anchors` (already computed for the typed-anchor filter above) --
  false-positive groups share a non-empty anchor 75.6% of the time, real revision groups only
  40.0% -- matching the given-new asymmetry (Clark & Haviland 1977: a correction reduces/omits
  reference to the already-established topic; a continuation re-states it). A direct threshold
  sweep confirmed `shared_anchors == 0` as the useful cut (`<= 1` was tested and found too
  permissive, 84.4% false-positive acceptance). **Final two-path result**: Path A (marker
  present) resolves regardless of `shared_anchors`; Path B (no marker, `shared_anchors` empty)
  resolves at the same confidence. Measured: false-positive rate 8.91% (vs 10.93% with no gate
  at all, vs 8.14% with the stricter marker-only gate), ZH clean distractor cut -6.9pp (vs -8.3pp
  with no gate, vs -4.2pp with the marker-only gate) -- strictly better than the marker-only gate
  on both axes that matter, though still short of the pre-registered 15pp/5pp decision bar.
- **Path B refinement (2026-08-18)**, after the ZH long-horizon corpus grew from 24 to 200
  scenarios (14 domains, up from 3) -- the earlier N=6 real-revision sample inside the
  `shared_anchors==0` subset was too small to calibrate a further threshold; re-measured at N=25
  before trusting anything. Two more given-new-asymmetry signals, tested jointly this time: the
  corrective (latest-turn) claim's own length (real corrections are terse, median 20 chars vs 29
  for continuations) and whether the group's distinguishing values are a SINGLE anchor type
  (96.0% of real revisions vs 63.6% of false positives, a much cleaner split than the earlier
  N=6/11 read that closed this as inconclusive). Combined gate on Path B specifically (Path A
  unaffected): false-positive acceptance risk 63.6% -> 27.3%, real-revision recall 96.0% -> 76.0%
  within that subset -- a real, deliberate trade. Isolated on the SAME N=200 dataset (with vs
  without this refinement, holding everything else fixed): 1,290-pair generality-check
  false-positive rate 8.91% -> 8.37%, ZH clean distractor cut barely moves (-1.2pp -> -0.8pp,
  both already far from the 15pp bar on this larger, more varied corpus). **A separate, unexplained
  finding surfaced by this comparison, not caused by the refinement**: the whole
  `domains_lh_zh` distractor-cut metric is now an order of magnitude smaller on the 200-scenario
  corpus than it was on the original 24-scenario one (-0.8/-1.2pp now vs -6.9pp before, in BOTH
  the with- and without-refinement configurations) -- likely a real difference in how the larger,
  more varied dataset constructs its distractor content, not a bug in either state, but not yet
  investigated. Pilot numbers on this corpus should not be compared directly against the
  pre-2026-08-18 24-scenario baselines documented above without accounting for this.

Hard rule: this module never accepts a `distractors`/`gold_answer`-shaped parameter. A caller
measuring against known-correct answers must do so outside this module's call boundary.
"""
from __future__ import annotations

import math
import re
from collections import Counter
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
# structurally empty for it. Character bigrams were the first fallback tried and were replaced
# (2026-08-18): with a small local candidate pool (~10-15 claims) and a combinatorially huge
# bigram space (thousands of characters squared), nearly every claim ends up with several
# bigrams that are "unique" in the pool by chance, not because they carry meaning -- confirmed
# directly, rarity saturated at the pool's maximum for every claim regardless of aggregation
# (sum, mean, top-K). Replaced with maximum-matching word segmentation against
# `zh_word_dictionary.ZH_WORD_DICTIONARY` (a plain frequency-threshold word list built from this
# project's own ZH chat research corpus, not a library, not a learned model) -- reduces the
# anchor space to the same order of magnitude as PT/EN's naturally sparse numbers/proper-noun
# anchors. Only genuine multi-character dictionary matches count; a leftover single character
# from incomplete segmentation is not a word and is dropped, not kept.
_CJK_CHAR = re.compile(r"[一-鿿㐀-䶿]")
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z0-9_+.'-]*")
_MAX_ZH_WORD_LEN = 4


def _is_cjk(text: str) -> bool:
    return bool(_CJK_CHAR.search(text))


def _segment_zh(text: str) -> list[str]:
    """Bidirectional maximum-matching segmentation against `ZH_WORD_DICTIONARY`. Falls back to
    single characters for spans the dictionary doesn't cover -- those never become anchors (see
    `_cjk_anchors`), so an incomplete dictionary fails toward fewer anchors, not spurious ones."""
    from .zh_word_dictionary import ZH_WORD_DICTIONARY as _DICT

    chars = list(text)
    n = len(chars)

    def _forward() -> list[str]:
        words, i = [], 0
        while i < n:
            if not _CJK_CHAR.match(chars[i]):
                words.append(chars[i])
                i += 1
                continue
            matched = None
            for length in range(min(_MAX_ZH_WORD_LEN, n - i), 1, -1):
                candidate = "".join(chars[i:i + length])
                if candidate in _DICT:
                    matched = candidate
                    break
            words.append(matched or chars[i])
            i += len(matched) if matched else 1
        return words

    def _backward() -> list[str]:
        words, i = [], n
        while i > 0:
            if not _CJK_CHAR.match(chars[i - 1]):
                words.append(chars[i - 1])
                i -= 1
                continue
            matched = None
            for length in range(min(_MAX_ZH_WORD_LEN, i), 1, -1):
                candidate = "".join(chars[i - length:i])
                if candidate in _DICT:
                    matched = candidate
                    break
            words.append(matched or chars[i - 1])
            i -= len(matched) if matched else 1
        return list(reversed(words))

    forward, backward = _forward(), _backward()
    if len(forward) != len(backward):
        return forward if len(forward) < len(backward) else backward
    singles = lambda seq: sum(1 for w in seq if len(w) == 1 and _CJK_CHAR.match(w))
    return forward if singles(forward) <= singles(backward) else backward


def _cjk_anchors(text: str) -> frozenset[str]:
    from .zh_anchor_stopwords import ZH_ANCHOR_STOPWORDS

    words = _segment_zh(text)
    anchors = {w for w in words if len(w) >= 2 and _CJK_CHAR.match(w[0])}
    latin_words = {match.group().casefold() for match in _LATIN_WORD.finditer(text)
                  if len(match.group()) >= 2}
    channels = observe_raw_text(text)
    raw = frozenset(anchors) | frozenset(latin_words) | frozenset(channels.numbers)
    # 2026-08-18: corpus-derived slang/discourse-filler stopwords (see
    # `zh_anchor_stopwords.py` for full derivation) -- confirmed on real ordinary-domain data to
    # drive spurious group detection the same way an unfiltered "Final" from "Final answer?" did
    # once for English. Measured: real, targeted (anchor occurrence count -27% on the ZH
    # diagnostic sample), but does NOT close the dominant remaining false-positive driver (see
    # this module's own docstring "Measured scope" section) -- kept as a real, non-regressive
    # improvement, not oversold as a fix for the whole problem.
    return raw - ZH_ANCHOR_STOPWORDS


def _text_anchors(text: str) -> frozenset[str]:
    if _is_cjk(text):
        return _cjk_anchors(text)
    channels = observe_raw_text(text)
    return frozenset(channels.entities) | frozenset(channels.numbers)


def _cjk_content_words(text: str) -> frozenset[str]:
    return frozenset(w for w in _segment_zh(text) if len(w) >= 2 and _CJK_CHAR.match(w[0]))


_CURRENCY_MARK = re.compile(r"[$€£¥]|R\$")
_CURRENCY_WORD = re.compile(r"\b(reais?|dollars?|euros?|bucks?|USD|EUR|BRL)\b", re.IGNORECASE)
_PERCENT_MARK = re.compile(r"%")
_PERCENT_WORD = re.compile(r"\b(percent|porcento|por\s+cento)\b", re.IGNORECASE)

_ZH_CORRECTION_MARKER = re.compile(
    "|".join((
        "不是", "其实是", "实际上是", "改成", "改为", "应该是", "更正", "纠正",
        "说错了", "打错了", "写错了", "更新一下", "更新：", "等等", "最终", "最后确定",
        "最终结果", "最新", "我记错了", "不对",
    )))


def _has_zh_correction_marker(text: str) -> bool:
    return bool(_ZH_CORRECTION_MARKER.search(text))


def _anchor_type(anchor: str, surface: str, temporal: frozenset[str]) -> str:
    """Coarse type classification (2026-08-18, ported from lab/supersession_collapse.py):
    reuses `observe_raw_text`'s existing numbers/temporal/entities split as the base signal
    rather than building a new extractor -- only adds light regex to separate MONEY/PERCENT
    within `.numbers`. See `collapse_evidence_items`'s own type-filter comment for how this is
    used (majority-vote filter, not universal agreement -- a single noise claim must not
    poison a whole group)."""
    if anchor in temporal:
        return "date"
    if any(char.isdigit() for char in anchor):
        match = re.search(r"\b" + re.escape(anchor) + r"\b", surface)
        index = match.start() if match else -1
        window = surface[max(0, index - 20):index + len(anchor) + 20] if index >= 0 else ""
        if _CURRENCY_MARK.search(window) or _CURRENCY_WORD.search(window):
            return "money"
        if _PERCENT_MARK.search(window) or _PERCENT_WORD.search(window):
            return "percent"
        return "cardinal"
    return "entity"


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
    question_anchors = _text_anchors(question)
    question_cjk_words = _cjk_content_words(question) if _is_cjk(question) else frozenset()

    # Self-built BM25-style IDF pool for the CJK fallback (2026-08-18 port from
    # lab/supersession_collapse.py's `_cjk_bm25_scores`, validated: false-positive rate on the
    # 1,290-pair generality check fell 14.03% -> 13.72%, a real improvement over the prior
    # word-segmentation-only Jaccard fallback). Not a literal reuse of
    # `MaterializedRawCausalSyndromeIndex`/`HorizonSearchEngine` (tried first, in `lab/`): both
    # build on `observe_raw_text`'s `.lexical` channel, which hardcodes `len(token) >= 3` --
    # built for Latin scripts, but Chinese words are overwhelmingly 2 characters ("我们", "计划",
    # "会议"...), so every real segmented CJK word scored 0.0 lexical/sublexical under either
    # shared engine. Computed once over every CJK item's own content instead, never touching
    # `observe_raw_text`'s filtered channels or a static dictionary for this part.
    cjk_word_sets: dict[int, frozenset[str]] = {}
    df: Counter = Counter()
    for index, item in enumerate(items):
        if item.content and _is_cjk(item.content):
            words = _cjk_content_words(item.content)
            cjk_word_sets[index] = words
            df.update(words)
    cjk_pool_size = len(cjk_word_sets)

    def idf(word: str) -> float:
        return math.log(1.0 + (cjk_pool_size - df[word] + .5) / (df[word] + .5))

    candidates: list[tuple[EvidenceItem, frozenset[str], object]] = []
    for index, item in enumerate(items):
        if not item.content or item.parent_sha256 is None or item.content_span is None:
            continue
        channels = observe_raw_text(item.content)
        anchors = _text_anchors(item.content)
        if not anchors:
            continue
        if not _is_cjk(item.content):
            # A real anchor (number/proper noun) is itself sufficient relevance for non-CJK text
            # -- falling through to lexical Jaccard against the question is what broke on real PT
            # noise (2026-08-18): `observe_raw_text`'s stopword list is English-only, so a
            # Portuguese preposition ("para") could be the ONLY nonzero-Jaccard token, and
            # informal noise ("p" for "para") erased that coincidence even though the item's real
            # anchor (its number) survived intact. Narrower than the relevance-floor removal
            # already tried and reverted (2026-08-17): CJK is untouched below, because CJK's
            # anchor space is not the same sparse, meaningful signal PT/EN's numbers and proper
            # nouns are -- that is exactly why removing the floor globally caused CJK to collapse
            # nearly its whole candidate pool into one group.
            relevance = 1.0
        else:
            # IDF-weighted overlap with the question's own segmented words, computed over the
            # local CJK candidate pool -- replaces the old Jaccard-on-merged-CJK-token fallback
            # (structurally near-zero for CJK, since `.lexical` merges a whole contiguous CJK run
            # into one token) with a real, validated signal (2026-08-18 port, see the IDF pool
            # comment above).
            relevance = sum(idf(word) for word in cjk_word_sets.get(index, frozenset())
                           & question_cjk_words)
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

    # Typed-anchor filter (2026-08-18, ported from lab/supersession_collapse.py): a group whose
    # members' distinguishing values span genuinely different kinds of facts (a date competing
    # with a money amount) is not a real value-revision. Filters to the MAJORITY type rather than
    # requiring universal agreement -- a single noise claim (a greeting/exclamation carrying an
    # incidental "entity" anchor, admitted by anchor-primary relevance since it has SOME anchor)
    # must not poison a whole group of otherwise type-consistent real competing claims. Measured
    # (lab/ pilot + FP check, mirrored here): false-positive rate 14.03% -> 11.16%.
    item_types: dict[tuple[int, tuple[int, int] | None], frozenset[str]] = {}
    for item, value, channels in value_bearing:
        temporal = frozenset(channels.temporal)
        key = (item.fact_id, item.content_span)
        item_types[key] = frozenset(_anchor_type(anchor, item.content, temporal)
                                    for anchor in value)
    # Captured pre-filter (used by the ZH Path B refinement below): whether every value-bearing
    # member's own distinguishing anchors are a SINGLE type, before majority-type filtering can
    # itself force apparent homogeneity by dropping the minority members.
    pre_filter_homogeneous = len(frozenset().union(*item_types.values())) == 1 if item_types else False
    type_votes: Counter = Counter()
    for types in item_types.values():
        type_votes.update(types)
    majority_type = type_votes.most_common(1)[0][0] if type_votes else None
    if majority_type is not None:
        value_bearing = [(item, value, channels) for item, value, channels in value_bearing
                         if majority_type in item_types[(item.fact_id, item.content_span)]]
    if len(value_bearing) < 2:
        return items, SupersessionReport(groups_detected, 0, {"type_mismatch": 1}, frozenset())

    # ZH correction gate, two-path (plan item 2c, 2026-08-18, ported from
    # lab/supersession_collapse.py -- see that module's own comment on this exact block for the
    # full derivation, including three refuted alternative signals). Path A: a correction marker
    # ("不是"/"其实是"/"等等"/"更新一下"...) present anywhere in the group -- high confidence,
    # resolve regardless of `shared_anchors`. Path B: no marker, but `shared_anchors` is EMPTY --
    # the given-new asymmetry signal alone (Clark & Haviland 1977: a correction reduces/omits
    # reference to the already-established topic; a continuation re-states it). Measured:
    # `shared_anchors == 0` recovers 60% of marker-less real revisions at only 24.4% acceptance
    # risk on ordinary (non-revision) data -- `<= 1` was tested and found too permissive (84.4%
    # FP risk). Scoped to CJK groups only; non-CJK resolution is untouched.
    if any(_is_cjk(item.content) for item, _value, _channels in value_bearing):
        has_marker = any(_has_zh_correction_marker(item.content)
                         for item, _value, _channels in value_bearing)
        if not has_marker:
            if shared_anchors:
                return items, SupersessionReport(groups_detected, 0, {"no_correction_marker": 1},
                                                 frozenset())
            # Path B refinement (2026-08-18, ported from lab/supersession_collapse.py -- see that
            # module's own comment on this block for the full derivation). Re-tested after the ZH
            # long-horizon corpus grew from 24 to 200 scenarios (the earlier N=6 real-revision
            # sample was too small to calibrate any further threshold). Two more given-new-
            # asymmetry signals, tested jointly: (1) the corrective (latest-turn) claim's own
            # length -- terse for a real correction, longer for a continuation (median 20 vs 29
            # chars); (2) whether the group's distinguishing values are a SINGLE anchor type (96.0%
            # of real revisions vs 63.6% of false positives at N=25/11). Combined: false-positive
            # acceptance risk 63.6% -> 27.3%, real-revision recall 96.0% -> 76.0%.
            latest = max(value_bearing, key=lambda row: row[0].fact_id)[0]
            corrective_len = len(latest.content)
            if not (pre_filter_homogeneous and corrective_len <= 28):
                return items, SupersessionReport(groups_detected, 0,
                                                 {"path_b_low_confidence": 1}, frozenset())

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
