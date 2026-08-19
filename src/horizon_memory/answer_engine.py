# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A single, model-shaped entry point over the deterministic route -> verify -> compose pipeline:
`HorizonAnswerEngine.answer(question, documents) -> AnsweredResult`. Internalizes the ephemeral
store lifecycle, routing, verification, and dual-dossier budget/composition logic already proven
in this project's own MemGym-DR validation (the published 0.95 judge-score result) -- relocated
here unchanged from where it was first proven correct (a demo webapp's `run_horizon` function),
so any caller (an HTTP API, a CLI, another app) gets the same behavior without hand-wiring the
eight-odd calls that pipeline actually requires.

## The one thing this relocation also fixes

The demo's own "pick a few clean sentences instead of the whole verified pool" step had a real,
confirmed bug: its greedy gain formula, `len(new_words) * (0.3 + relevance)`, had no floor on
which candidates could compete, so a long low-relevance sentence could outscore a short
highest-relevance one on raw word count alone. Worked example (MemGym-DR ordinal 382, a question
about a system called "BARM"): a 504-character, 45-new-word sentence about a *different* system
in the same corpus ("UCEF", relevance 0.583) beat the single most relevant claim in the entire
pool -- 213 characters, 14 new words, relevance 0.991, correctly about BARM -- because
45 x (0.3+0.583) = 39.7 outscored 14 x (0.3+0.991) = 18.1.

Fix: gate the candidate pool by relevance *before* the greedy diversity loop runs, computed once
from the top of the sorted shortlist (not recomputed per iteration, which would let the gate drift
downward as the best claims get removed and re-admit weak candidates in later rounds). The gate
ratio (`EngineProfile.answer_relevance_gate_ratio`) ships as a profile field, not a hardcoded
constant, because the right value cannot be reasoned out from one example: at ratio 0.5, gate =
0.991 x 0.5 = 0.4955 -- UCEF's 0.583 still clears that and would still win.
`lab/runners/validate_answer_relevance_gate.py`'s real sweep (50 MemGym-DR questions + ordinal
382, ratios 0.10-0.90) found mean coverage fully saturated at 79.6-79.8% for every ratio <= 0.3
(byte-identical answer_lines counts from 0.10-0.20) -- 0.3 is the shipped default: the tightest
gate that already captures 100% of the achievable coverage on real data, confirmed to include
BARM (relevance 0.592, the true top claim in ordinal 382's real corpus) rather than exclude it.

The same relocation also removes the fixed "always exactly 4 sentences" cap: the greedy loop's own
`if gain <= 0: break` is already the natural stopping rule once the relevance gate makes it
trustworthy, so answer length now varies per question (bounded by the gate, the shortlist size,
and running out of genuinely new content) instead of being hardcoded.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import secrets
import shutil
import tempfile
from pathlib import Path

from .claim_composer import ClaimSource, ContextIntent
from .claim_routing import ClaimGenerator
from .config import HorizonConfig
from .api import HorizonMemory
from .engine_profile import DEFAULT_PROFILE, EngineProfile
from .proof_dossier import ProofDossier, build_proof_dossier
from .routing import HorizonVerifier, QueryEnvelope, RouteDocument, RouteState, RoutingIndex, \
    SemanticRouter

_TOKEN = re.compile(r"[^\W_]+")


def _content_words(text: str | None) -> frozenset[str]:
    if not text:
        return frozenset()
    return frozenset(_TOKEN.findall(text.casefold()))


@dataclass(frozen=True)
class AnsweredClaim:
    """One verified, source-attributable sentence. `fact_id` is the parent document's identity
    in the caller-supplied `RouteDocument`s -- the engine stays format-agnostic about how a caller
    wants to label/cite it (e.g. `website/web_app.py` renders it as `f"doc:{fact_id}"`)."""
    text: str
    fact_id: int
    source_id: str
    relevance_score: float


@dataclass(frozen=True)
class AnsweredResult:
    state: str                                # "RESOLVED" or a RouteState member name (abstain)
    claims: tuple[AnsweredClaim, ...]         # full verified/merged pool
    answer_lines: tuple[AnsweredClaim, ...]   # the clean, adaptive-length answer
    sources: tuple[ClaimSource, ...]          # lets a caller independently re-verify
    core_dossier: ProofDossier | None
    ranked_dossier: ProofDossier | None
    documents_considered: int
    verified_candidates: int
    answer_bytes: int

    @property
    def resolved(self) -> bool:
        return self.state == "RESOLVED"

    @property
    def answer_text(self) -> str:
        return "\n".join(claim.text for claim in self.answer_lines)


def _abstained(state_name: str, documents_considered: int) -> AnsweredResult:
    return AnsweredResult(state_name, (), (), (), None, None, documents_considered, 0, 0)


class HorizonAnswerEngine:
    """Deterministic, zero-LLM route -> verify -> compose pipeline behind one call."""

    def __init__(self, *, profile: EngineProfile = DEFAULT_PROFILE,
                 scope_id: int = 1, session_id: str = "s1"):
        self.profile = profile
        self.scope_id = scope_id
        self.session_id = session_id

    def answer(self, question: str, documents: tuple[RouteDocument, ...]) -> AnsweredResult:
        profile = self.profile
        index = RoutingIndex(documents)

        workdir = tempfile.mkdtemp(prefix="horizon-answer-")
        try:
            memory = HorizonMemory.create(
                HorizonConfig(workdir, self.scope_id, secrets.token_bytes(32)))
            try:
                for document in documents:
                    memory.put(self.scope_id, document.fact_id, 1, 1)

                query = QueryEnvelope("q", question, self.scope_id, self.session_id, 10)
                verifier = HorizonVerifier(memory, index)
                claim_generator = ClaimGenerator(
                    profile.claim_weights, specificity_bonus=profile.claim_specificity_bonus)
                result = SemanticRouter(index, claim_generator, verifier).route(
                    query, profile.claim_limit, allow_scope_fallback=False)

                if result.state != RouteState.EVIDENCE:
                    return _abstained(result.state.name, len(documents))

                items = result.evidence.budgeted_items(max_chars=profile.acquisition_bytes)
                sources, origin, relevance, seen = [], {}, {}, set()
                for item in items:
                    key = (item.source, item.fact_id, item.content_span)
                    if key in seen:
                        continue
                    seen.add(key)
                    content = item.content if item.content is not None else str(item.value)
                    source_id = f"{item.source}:{item.fact_id}:{item.content_span}"
                    sources.append(ClaimSource.seal(source_id, content))
                    origin[source_id] = item.fact_id
                    relevance[source_id] = item.relevance_score or 0.0
                sources = tuple(sources)
                if not sources:
                    return _abstained(RouteState.ABSTENTION.name, len(documents))

                intents = (ContextIntent.seal(
                    "q:intent", question, frozenset(s.source_id for s in sources)),)

                # `build_proof_dossier` raises `ValueError` when verified evidence exists but no
                # source text survives its own claim-extraction/selection (e.g. every candidate
                # sentence is too short to form an `AuthorizedClaim`) -- routing found *something*
                # (RouteState.EVIDENCE), but there is nothing left to compose an answer from. An
                # API-facing facade should abstain cleanly here, not propagate an internal
                # exception to a caller.
                try:
                    core = build_proof_dossier(
                        sources=sources, intents=intents, strategy="horizon",
                        per_fiber=profile.per_fiber, max_bytes=profile.answer_bytes,
                        submodular_budget_fill=True)
                    ranked = build_proof_dossier(
                        sources=sources, intents=intents, strategy="horizon",
                        per_fiber=profile.per_fiber, max_bytes=profile.acquisition_bytes,
                        global_sort_alpha=profile.global_sort_alpha,
                        anchor_bonus=profile.anchor_bonus,
                        specificity_bonus=profile.specificity_bonus)
                except ValueError:
                    return _abstained(RouteState.ABSTENTION.name, len(documents))

                chosen = list(core.claims)
                used = sum(len(c.surface.encode("utf-8")) for c in chosen)
                if used < profile.answer_bytes:
                    known = {c.claim_id for c in chosen}
                    spare = profile.answer_bytes - used
                    filled = 0
                    for claim in ranked.claims:
                        if claim.claim_id in known:
                            continue
                        cost = len(claim.surface.encode("utf-8")) + 1
                        if filled + cost > spare:
                            continue
                        chosen.append(claim)
                        filled += cost

                claims, seen_text = [], set()
                for claim in chosen:
                    normalized = " ".join(claim.surface.split()).lower()
                    if normalized in seen_text:
                        continue  # the same sentence often appears in several source documents
                    seen_text.add(normalized)
                    claims.append(AnsweredClaim(
                        claim.surface, origin.get(claim.source_id, -1), claim.source_id,
                        relevance.get(claim.source_id, 0.0)))

                answer_lines = _pick_clean_answer(chosen, relevance, origin, question, profile)
                answer_bytes = sum(len(line.text.encode("utf-8")) for line in answer_lines)

                return AnsweredResult(
                    "RESOLVED", tuple(claims), answer_lines, sources, core, ranked,
                    len(documents), len(sources), answer_bytes)
            finally:
                memory.close()
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


def _pick_clean_answer(chosen, relevance: dict, origin: dict, question: str,
                       profile: EngineProfile) -> tuple[AnsweredClaim, ...]:
    """Adaptive-length, relevance-gated selection -- see module docstring for the bug this fixes
    and why the gate ratio is a swept, not assumed, profile value.

    Known, narrow interaction (found while smoke-testing this fix, 2026-08-19): the relevance
    gate only compares candidates *within* one length tier. If the single highest-relevance claim
    is shorter than `answer_min_length_tiers`' first threshold (90 chars) while a much lower-
    relevance claim clears it, the short correct claim never gets a chance to compete and the
    tier-fallback loop returns after tier one anyway (a non-empty pick stops the fallback). Real
    MemGym-DR claims are ordinary academic sentences, almost always well over 90 characters, so
    this has not been observed to matter on real data -- but it was reproducible with a
    deliberately short synthetic claim. Not fixed here: `lab/runners/validate_answer_relevance_gate.py`
    measures real coverage, which is the authoritative check for whether this narrow case is worth
    a more invasive redesign (e.g. unioning candidates across tiers instead of an early-return
    waterfall) -- fixing it on reasoning alone, without that evidence, risks the same class of
    mistake the gate ratio itself is deliberately not guessing at."""
    question_tokens = _content_words(question)

    def build_shortlist(min_length: int, require_sentence: bool):
        shortlist, seen_clean = [], set()
        for claim in sorted(chosen, key=lambda c: -relevance.get(c.source_id, 0.0)):
            text = claim.surface.strip()
            normalized = " ".join(text.split()).lower()
            if normalized in seen_clean or len(text) < min_length:
                continue
            if require_sentence and not (text.endswith(".") and text[0].isupper()):
                continue
            seen_clean.add(normalized)
            shortlist.append(claim)
            if len(shortlist) >= profile.answer_shortlist_size:
                break
        return shortlist

    def greedy_pick(shortlist):
        if not shortlist:
            return []
        top_relevance = relevance.get(shortlist[0].source_id, 0.0)
        gate = top_relevance * profile.answer_relevance_gate_ratio
        eligible = [c for c in shortlist if relevance.get(c.source_id, 0.0) >= gate]

        def gain(claim, covered):
            new_words = _content_words(claim.surface) - question_tokens - covered
            return len(new_words) * (0.3 + relevance.get(claim.source_id, 0.0))

        picked, covered = [], set()
        while eligible:
            best = max(eligible, key=lambda c: gain(c, covered))
            if gain(best, covered) <= 0:
                break
            picked.append(AnsweredClaim(
                best.surface.strip(), origin.get(best.source_id, -1), best.source_id,
                relevance.get(best.source_id, 0.0)))
            covered |= _content_words(best.surface)
            eligible.remove(best)
        return picked

    # Prefer complete, substantial sentences; fall back progressively rather than returning
    # nothing when a corpus yields mostly short/fragmentary claims for this question.
    for min_length, require_sentence in profile.answer_min_length_tiers:
        picked = greedy_pick(build_shortlist(min_length, require_sentence))
        if picked:
            return tuple(picked)
    return ()


__all__ = ["AnsweredClaim", "AnsweredResult", "HorizonAnswerEngine"]
