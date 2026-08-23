# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A versioned, swappable bundle of every tunable value `HorizonAnswerEngine` (`answer_engine.py`)
consumes -- the "weights" half of shipping this as architecture-plus-weights, analogous to a model
checkpoint: swap the JSON to retune a deployment, never touch the code. Follows the same
frozen-dataclass-with-`__post_init__`-validation shape already used by `HorizonConfig`
(`config.py`) and `ConformalCalibrator` (`conformal_routing.py`) -- no new dependency, plain `json`.

What is deliberately NOT in here, and why: the ZH word-segmentation dictionary and stopword list
(`zh_word_dictionary.py`, `zh_anchor_stopwords.py`) are linguistic resource data for a different
code path (`supersession_collapse.py`), not calibrated scoring weights, and nothing in this
project's own research notes suggests they need per-deployment tuning. `proof_dossier.py`'s inline
`asserted_bonus=0.3`/anchor-overlap `0.35` and `materialized_proof_pressure_search.py`'s `_bm25`
formula constants (`2.2`/`1.2`/`.25`/`.75`) also stay inline -- turning those into parameters means
new signature surface on two modules with hundreds of passing tests riding on them, for constants
with no evidence yet that a different value is ever wanted. Both are a clearly-scoped future
increment, not silently dropped.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path

from .claim_routing import DEFAULT_WEIGHTS as CLAIM_GENERATOR_DEFAULT_WEIGHTS
from .conformal_routing import LEXICAL_SUBLEXICAL_WEIGHTS

SCHEMA = "engine-profile.v1"

# `(min_length, require_complete_sentence)` tiers the clean-answer picker falls back through in
# order -- matches the three-tier fallback already validated in the demo webapp
# (`_pick(90, True) or _pick(60, True) or _pick(40, False)`), so a corpus that yields mostly
# short/fragmentary claims for a given question still produces *some* answer instead of none.
_DEFAULT_LENGTH_TIERS: tuple[tuple[int, bool], ...] = ((90, True), (60, True), (40, False))


@dataclass(frozen=True)
class EngineProfile:
    schema: str = SCHEMA
    name: str = "default"

    # Claim-generator / conformal-routing channel weights (see claim_routing.py,
    # conformal_routing.py for the six-channel order: lexical, sublexical, entity, relation,
    # observable, contradiction).
    claim_weights: tuple[float, ...] = CLAIM_GENERATOR_DEFAULT_WEIGHTS
    claim_specificity_bonus: float | None = None
    conformal_weights: tuple[float, ...] = LEXICAL_SUBLEXICAL_WEIGHTS
    bm25_k1: float = 1.2
    bm25_b: float = 0.75
    lexical_bm25_delta: float = 0.0
    sublexical_bm25_delta: float = 0.0

    # Candidate-routing budget.
    claim_limit: int = 800

    # Dossier / composition budgets -- the exact values the published 0.95 judge-score result
    # (MemGym-DR, D144) was measured at.
    acquisition_bytes: int = 65_536
    answer_bytes: int = 24_576
    per_fiber: int = 64
    global_sort_alpha: float = 0.3
    anchor_bonus: float = 0.3
    specificity_bonus: float = 0.5
    dedup_threshold: float | None = None

    # Clean-answer selection (adaptive length, relevance-gated -- see answer_engine.py).
    # `answer_relevance_gate_ratio=0.3` was locked in from `lab/runners/
    # validate_answer_relevance_gate.py`'s real sweep (2026-08-19, 50 MemGym-DR questions +
    # ordinal 382's own BARM/UCEF case, ratios 0.10-0.90): mean coverage is fully saturated for
    # every ratio <= 0.3 (79.6-79.8%, byte-identical answer_lines counts from 0.10-0.20), so 0.3
    # is the tightest gate that already captures 100% of the achievable coverage on this data --
    # going looser buys nothing, going tighter starts trading coverage away (0.5: 74.3%, 0.7:
    # 53.9%, 0.9: 29.5%). At 0.3, ordinal 382's answer includes BARM (relevance 0.592, the real
    # top claim in that corpus) alongside UCEF, not instead of it -- with the adaptive-length cap
    # removed, the fix is about the correct claim never being *excluded*, not about picking order,
    # matching the caller's own framing: Horizon hands its evidence to a downstream reader, it
    # does not need to pre-decide which single fact matters most.
    answer_shortlist_size: int = 50
    answer_relevance_gate_ratio: float = 0.3
    # `None` (default) preserves the historical hard-gated tier cascade in `_pick_clean_answer`
    # byte-for-byte: a claim that fails a tier's `require_sentence`/`min_length` check is excluded
    # from that tier's candidate pool entirely, and the cascade only advances to a looser tier when
    # the current one is *empty*. Real-corpus testing (2026-08-23, a 120-document HuggingFace
    # public-domain fiction corpus split into fixed 500-byte windows, so most genuinely relevant
    # spans are sentence fragments) found this can make the cascade stop at tier one even when its
    # own best surviving candidate is far less relevant than a fragment the tier just excluded --
    # confirmed via `ClaimGenerator` scoring the fragment 1.0 vs. the surviving sentence's 0.01,
    # yet the sentence still winning because the fragment was never in tier one's candidate pool at
    # all. Setting this to a non-negative float switches to a single-pass selector: every claim
    # clearing one loose length floor stays eligible (no hard sentence-shape exclusion), and
    # "looks like a complete sentence" becomes an *additive* bonus inside the existing greedy gain
    # formula instead of a gate -- matching the bonus-not-gate pattern already used successfully
    # elsewhere in this file (`anchor_bonus`, `specificity_bonus`) rather than the hard-gate pattern
    # this project's own history has repeatedly found regresses recall (e.g. the ZH
    # marker-as-hard-gate correction in `supersession_collapse.py`).
    answer_completeness_bonus: float | None = None
    answer_selector: str = "diversity"
    # `clean` is the historical API surface. `full_dossier` returns the complete verified
    # composed packet under `answer_bytes`, which is the surface the D144 MemGym result judged.
    answer_render_mode: str = "clean"
    priority_aware_merge: bool = False
    hpps_max_results: int = 3
    # Explicit breadth after HPPS's proof-directed core. Zero preserves the historical selector;
    # exploration can widen evidence but is never allowed to masquerade as proof closure.
    hpps_exploration_reserve: int = 0
    answer_min_length_tiers: tuple[tuple[int, bool], ...] = field(
        default_factory=lambda: _DEFAULT_LENGTH_TIERS)

    def __post_init__(self) -> None:
        if self.schema != SCHEMA:
            raise ValueError(f"unsupported EngineProfile schema: {self.schema!r}")
        if not self.name:
            raise ValueError("EngineProfile.name is required")
        if len(self.claim_weights) != 6 or any(w < 0 for w in self.claim_weights):
            raise ValueError("claim_weights requires six non-negative channel weights")
        if len(self.conformal_weights) != 6 or any(w < 0 for w in self.conformal_weights):
            raise ValueError("conformal_weights requires six non-negative channel weights")
        if self.claim_specificity_bonus is not None and self.claim_specificity_bonus < 0:
            raise ValueError("claim_specificity_bonus must be non-negative")
        if self.bm25_k1 <= 0 or not 0 <= self.bm25_b <= 1:
            raise ValueError("invalid BM25 parameters")
        if self.lexical_bm25_delta < 0 or self.sublexical_bm25_delta < 0:
            raise ValueError("BM25+ deltas must be non-negative")
        if self.claim_limit < 1:
            raise ValueError("claim_limit must be positive")
        if self.acquisition_bytes < 256 or self.answer_bytes < 256:
            raise ValueError("acquisition_bytes/answer_bytes must be >= 256")
        if self.answer_bytes > self.acquisition_bytes:
            raise ValueError("answer_bytes cannot exceed acquisition_bytes")
        if self.per_fiber < 1:
            raise ValueError("per_fiber must be positive")
        if not 0.0 <= self.global_sort_alpha <= 1.0:
            raise ValueError("global_sort_alpha must be in [0,1]")
        if self.anchor_bonus < 0 or self.specificity_bonus < 0:
            raise ValueError("anchor_bonus/specificity_bonus must be non-negative")
        if self.dedup_threshold is not None and not 0.0 <= self.dedup_threshold <= 1.0:
            raise ValueError("dedup_threshold must be in [0,1]")
        if self.answer_shortlist_size < 1:
            raise ValueError("answer_shortlist_size must be positive")
        if not 0.0 <= self.answer_relevance_gate_ratio <= 1.0:
            raise ValueError("answer_relevance_gate_ratio must be in [0,1]")
        if self.answer_completeness_bonus is not None and self.answer_completeness_bonus < 0:
            raise ValueError("answer_completeness_bonus must be non-negative")
        if self.answer_selector not in ("diversity", "hpps"):
            raise ValueError("answer_selector must be 'diversity' or 'hpps'")
        if self.answer_render_mode not in ("clean", "full_dossier"):
            raise ValueError("answer_render_mode must be 'clean' or 'full_dossier'")
        if self.hpps_max_results < 1:
            raise ValueError("hpps_max_results must be positive")
        if not 0 <= self.hpps_exploration_reserve <= self.hpps_max_results:
            raise ValueError(
                "hpps_exploration_reserve must be in [0, hpps_max_results]")
        if not self.answer_min_length_tiers:
            raise ValueError("answer_min_length_tiers must not be empty")
        for min_length, _require_sentence in self.answer_min_length_tiers:
            if min_length < 1:
                raise ValueError("answer_min_length_tiers entries must be positive")

    def to_dict(self) -> dict:
        return {
            "schema": self.schema, "name": self.name,
            "claim_weights": list(self.claim_weights),
            "claim_specificity_bonus": self.claim_specificity_bonus,
            "conformal_weights": list(self.conformal_weights),
            "bm25_k1": self.bm25_k1, "bm25_b": self.bm25_b,
            "lexical_bm25_delta": self.lexical_bm25_delta,
            "sublexical_bm25_delta": self.sublexical_bm25_delta,
            "claim_limit": self.claim_limit,
            "acquisition_bytes": self.acquisition_bytes, "answer_bytes": self.answer_bytes,
            "per_fiber": self.per_fiber, "global_sort_alpha": self.global_sort_alpha,
            "anchor_bonus": self.anchor_bonus, "specificity_bonus": self.specificity_bonus,
            "dedup_threshold": self.dedup_threshold,
            "answer_shortlist_size": self.answer_shortlist_size,
            "answer_relevance_gate_ratio": self.answer_relevance_gate_ratio,
            "answer_completeness_bonus": self.answer_completeness_bonus,
            "answer_selector": self.answer_selector,
            "answer_render_mode": self.answer_render_mode,
            "priority_aware_merge": self.priority_aware_merge,
            "hpps_max_results": self.hpps_max_results,
            "hpps_exploration_reserve": self.hpps_exploration_reserve,
            "answer_min_length_tiers": [list(tier) for tier in self.answer_min_length_tiers],
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "EngineProfile":
        data = dict(payload)
        if "claim_weights" in data:
            data["claim_weights"] = tuple(data["claim_weights"])
        if "conformal_weights" in data:
            data["conformal_weights"] = tuple(data["conformal_weights"])
        if "answer_min_length_tiers" in data:
            data["answer_min_length_tiers"] = tuple(
                tuple(tier) for tier in data["answer_min_length_tiers"])
        return cls(**data)

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "EngineProfile":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


DEFAULT_PROFILE = EngineProfile()

# Named deployment presets ("Scale" / "Team" / "Personal" memory). `DEFAULT_PROFILE` above is
# "Scale Memory": its `answer_shortlist_size=50`/`answer_relevance_gate_ratio=0.3` are exactly the
# values the published MemGym-DR/D144 (0.95) and LongMemEval (0.767) judge scores were measured
# at, tuned to protect a large, hundreds-of-document corpus from dilution -- the right choice for
# an enterprise-scale knowledge base or a RAG-style large document set.
#
# Found 2026-08-22: on real, small (tens-of-KB) personal-memory and team-technical-QA corpora --
# a casual conversational chat history and a formal technical Q&A corpus, 64 hand-verified real
# questions total, verified against a live MongoDB instance -- `DEFAULT_PROFILE` always found the
# right source but frequently dropped the specific answer-bearing sentence in favor of a shorter,
# less informative one, because `answer_shortlist_size=50` (an engineering safety cap with no
# benchmark backing at all, unlike the gate ratio) truncated the candidate pool before the correct
# claim could compete. A corpus this small has no real dilution risk for the tight defaults to
# guard against.
#
# Corpus size does NOT reliably distinguish "safe to loosen" from "needs the tight defaults" --
# calibration found the technical-QA corpus's own candidate-pool size (120-130) statistically
# indistinguishable from a real MemGym-DR episode (87-138 across the full 120-question set), so
# there is no automatic detector to build here. These are deliberate, named choices a deployment
# picks for itself, matching its own actual corpus scale -- never an automatic default.
# "Team Memory": balanced middle tier for a medium corpus (a small team's internal docs, a few
# hundred KB) -- meaningfully more complete than Scale Memory without fully removing its
# diversity/anti-dilution safeguards. Measured on the two real MongoDB corpora above: 23/32 and
# 17/20 (up from Scale Memory's 17/32 and 15/20), zero wrong answers. Not independently
# judge-score-validated the way `DEFAULT_PROFILE`/`PERSONAL_MEMORY_PROFILE` are at their own
# extremes -- a reasonable interpolated default, not a separately benchmarked one; test against
# your own corpus before relying on it.
#
# `answer_completeness_bonus=0.5` added 2026-08-23 after a real, reproducible bug: two fresh
# external HuggingFace corpora (120 Brazilian-Portuguese legislative/news questions, 120 English
# public-domain-fiction questions, neither previously seen by this project) found the historical
# hard-gated tier cascade in `_pick_clean_answer` could exclude the single most relevant claim
# outright just for not "looking like a complete sentence" -- confirmed via direct `ClaimGenerator`
# inspection (a 1.0-relevance fragment excluded in favor of a 0.01-relevance full sentence). 0.5 was
# chosen from a calibration sweep (odd-indexed half of each corpus, values 0.3/0.5/1.0 all landed on
# the identical plateau -- not a hand-picked constant) and reconfirmed on the even-indexed holdout
# half never touched during calibration, plus a zero-regression spot-check against the same three
# already-validated MongoDB corpora below (byte-identical answers before/after). See
# `docs/BENCHMARKS.md` for the full numbers.
TEAM_MEMORY_PROFILE = EngineProfile(
    name="team-memory-v1", answer_relevance_gate_ratio=0.15,
    answer_shortlist_size=150, answer_bytes=32_768, answer_completeness_bonus=0.5)

# "Personal Memory" -- RECOMMENDED default for a personal-memory or small-corpus deployment: a
# chat history, personal notes, a handful to a couple hundred documents, where completeness of the
# answer matters more than precision-per-byte. Fully validated across seven real question
# batteries against five independent live MongoDB-backed corpora (136 total questions, literal
# substring ground truth checked directly against the database, never the paraphrased "expected
# answer" text that seeded them): 31/32, 19/20, 12/12, 12/12, 29/30, and a clean 20/20 on a
# 27-conversation multi-hop battery requiring facts from 2-3 DIFFERENT conversations to be fused
# into one answer -- zero false answers or wrong-conversation hallucinations introduced anywhere,
# only previously-dropped detail (or, in the multi-hop case, previously-dropped cross-conversation
# claims) recovered. Also re-tested against the full 120-question MemGym-DR benchmark and found
# zero regression on a token-overlap coverage metric -- but that metric is known to reward
# returning more text regardless of real answer quality (the project's own research history
# documents this exact "haystack" risk), so this profile is deliberately NOT recommended for
# large, MemGym-DR-scale corpora, where the tight `DEFAULT_PROFILE` defaults remain the validated,
# judge-scored choice.
#
# `answer_completeness_bonus=0.5` added 2026-08-23 -- same fix and calibration discipline as
# `TEAM_MEMORY_PROFILE` above. On the two new external HuggingFace corpora (120 questions each,
# right-document recovery measured by exact FactId attribution in the rendered answer, not a
# proxy): the PT/legislative-news corpus moved 106/120 -> 110/120 and the EN/fiction corpus moved
# 5/120 -> 119/120 -- the fiction corpus is exactly the shape (raw text windowed into fixed-length
# records, so relevant spans are routinely fragments) that exposed the bug in the first place. Zero
# regression on the five already-validated MongoDB corpora (byte-identical answers). See
# `docs/BENCHMARKS.md` for the full numbers.
PERSONAL_MEMORY_PROFILE = EngineProfile(
    name="personal-memory-v1", answer_relevance_gate_ratio=0.0,
    answer_shortlist_size=500, answer_bytes=40_000, answer_completeness_bonus=0.5)

__all__ = [
    "SCHEMA", "DEFAULT_PROFILE", "TEAM_MEMORY_PROFILE", "PERSONAL_MEMORY_PROFILE", "EngineProfile",
]
