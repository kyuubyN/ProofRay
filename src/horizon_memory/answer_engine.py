# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A model-shaped entry point over deterministic route -> verify -> compose.

The historical default remains a compact clean-evidence facade.  The MemGym reference pipeline
requires four explicit opt-ins that the old facade did not carry: a calibrated candidate generator,
turn-scoped intents, priority-aware merge and full-dossier rendering.  These are now first-class
contracts rather than being inaccurately inherited from a different lab runner.

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

from dataclasses import dataclass, field
import re
import secrets
import shutil
import tempfile
from pathlib import Path
from typing import Protocol

from .claim_composer import ClaimSource, ContextIntent
from .claim_routing import ClaimGenerator
from .config import HorizonConfig
from .api import HorizonMemory
from .engine_profile import DEFAULT_PROFILE, EngineProfile
from .materialized_proof_pressure_search import MaterializedIndependentHorizonSearchEngine
from .proof_dossier import ProofDossier, build_proof_dossier
from .raw_causal_channels import RawCausalDocument, is_cjk
from .routing import CandidateGenerator, HorizonVerifier, QueryEnvelope, RouteDocument, RouteState, \
    RoutingIndex, SemanticRouter
from .conformal_routing import document_priority_by_source

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
class AnswerContextIntent:
    """An observed query/goal attached to exact document FactIds, never inferred from gold."""

    intent_id: str
    text: str
    fact_ids: tuple[int, ...]

    def __post_init__(self) -> None:
        if (not self.intent_id or not self.text or not self.fact_ids or
                self.fact_ids != tuple(sorted(set(self.fact_ids))) or
                any(item < 0 for item in self.fact_ids)):
            raise ValueError("answer context intent must be non-empty and FactId-canonical")


@dataclass(frozen=True)
class DirectAnswer:
    """A short answer channel, explicitly separate from verified evidence.

    `candidate` may carry an extractive proposal while obligations remain open. Only
    `resolved` is a complete direct answer and therefore requires `proof_closed=True` plus at
    least one verified source ID. Evidence remains available independently in AnsweredResult.
    """
    state: str = "not_attempted"
    text: str = ""
    method: str = "none"
    source_ids: tuple[str, ...] = ()
    proof_closed: bool = False
    residual: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        allowed = {"not_attempted", "candidate", "resolved", "abstain", "unsupported",
                   "contested"}
        if self.state not in allowed:
            raise ValueError("invalid direct-answer state")
        if self.source_ids != tuple(dict.fromkeys(self.source_ids)):
            raise ValueError("direct-answer source IDs must be unique and canonical")
        if self.state in ("candidate", "resolved") and (not self.text or self.method == "none"):
            raise ValueError("candidate/resolved direct answer requires text and method")
        if self.state == "resolved" and (not self.proof_closed or not self.source_ids):
            raise ValueError("resolved direct answer requires closed proof and verified sources")
        if self.state not in ("candidate", "resolved") and self.text:
            raise ValueError("non-answer direct state cannot carry answer text")


@dataclass(frozen=True)
class DirectAnswerProposal:
    """Untrusted readout proposal. The engine can admit it only as `candidate`."""
    text: str
    method: str
    source_ids: tuple[str, ...]
    residual: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.text or not self.method or self.method == "none" or not self.source_ids:
            raise ValueError("direct-answer proposal requires text, method and sources")
        if self.source_ids != tuple(dict.fromkeys(self.source_ids)):
            raise ValueError("proposal source IDs must be unique and canonical")


class DirectAnswerReader(Protocol):
    def propose(self, question: str,
                evidence: tuple[AnsweredClaim, ...]) -> DirectAnswerProposal | None: ...


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
    selector: str = "diversity"
    selector_proof_closed: bool | None = None
    selector_residual: tuple[str, ...] = ()
    direct_answer: DirectAnswer = field(default_factory=DirectAnswer)
    # Size of the candidate pool `_pick_clean_answer`'s shortlist/gate operate on -- lets a caller
    # see how close a corpus is to `answer_shortlist_size` before picking a named `EngineProfile`
    # preset (see `PERSONAL_MEMORY_PROFILE`/`TEAM_MEMORY_PROFILE` in `engine_profile.py`). Cheap
    # telemetry, mirrors the existing `documents_considered`/`verified_candidates` pattern; never
    # affects rendered answer bytes.
    chosen_size: int = 0

    @property
    def resolved(self) -> bool:
        return self.state == "RESOLVED"

    @property
    def answer_text(self) -> str:
        return "\n".join(claim.text for claim in self.answer_lines)

    @property
    def evidence_text(self) -> str:
        """Explicit name for the backwards-compatible `answer_text` evidence channel."""
        return self.answer_text


def _abstained(state_name: str, documents_considered: int) -> AnsweredResult:
    return AnsweredResult(state_name, (), (), (), None, None, documents_considered, 0, 0)


class HorizonAnswerEngine:
    """Deterministic, zero-LLM route -> verify -> compose pipeline behind one call."""

    def __init__(self, *, profile: EngineProfile = DEFAULT_PROFILE,
                 scope_id: int = 1, session_id: str = "s1",
                 direct_answer_reader: DirectAnswerReader | None = None,
                 candidate_generator: CandidateGenerator | None = None):
        self.profile = profile
        self.scope_id = scope_id
        self.session_id = session_id
        self.direct_answer_reader = direct_answer_reader
        self.candidate_generator = candidate_generator

    def answer(self, question: str, documents: tuple[RouteDocument, ...], *,
               context_intents: tuple[AnswerContextIntent, ...] = ()) -> AnsweredResult:
        profile = self.profile
        index = RoutingIndex(documents)
        known_fact_ids = {document.fact_id for document in documents}
        if any(set(intent.fact_ids) - known_fact_ids for intent in context_intents):
            raise ValueError("answer context intent references an unknown document FactId")

        workdir = tempfile.mkdtemp(prefix="horizon-answer-")
        try:
            memory = HorizonMemory.create(
                HorizonConfig(workdir, self.scope_id, secrets.token_bytes(32)))
            try:
                for document in documents:
                    memory.put(self.scope_id, document.fact_id, document.version, 1)

                query = QueryEnvelope("q", question, self.scope_id, self.session_id, 10)
                verifier = HorizonVerifier(memory, index)
                claim_generator = self.candidate_generator or ClaimGenerator(
                    profile.claim_weights, specificity_bonus=profile.claim_specificity_bonus,
                    bm25_k1=profile.bm25_k1, bm25_b=profile.bm25_b,
                    lexical_bm25_delta=profile.lexical_bm25_delta,
                    sublexical_bm25_delta=profile.sublexical_bm25_delta)
                result = SemanticRouter(index, claim_generator, verifier).route(
                    query, profile.claim_limit, allow_scope_fallback=False)

                if result.state != RouteState.EVIDENCE:
                    return _abstained(result.state.name, len(documents))

                source_priority = None
                if profile.priority_aware_merge:
                    document_router = getattr(claim_generator, "document_router", None)
                    if document_router is None:
                        return _abstained("ABSTAIN_PRIORITY_AUTHORITY", len(documents))
                    routed_documents = document_router.generate(
                        query, index, profile.claim_limit, same_session=True)
                    source_priority = document_priority_by_source(routed_documents, index)
                items = result.evidence.budgeted_items(
                    max_chars=profile.acquisition_bytes,
                    global_sort_alpha=profile.global_sort_alpha if source_priority else None,
                    source_priority=source_priority)
                sources, source_fact_ids, origin, relevance, seen = [], [], {}, {}, set()
                for item in items:
                    key = (item.source, item.fact_id, item.content_span)
                    if key in seen:
                        continue
                    seen.add(key)
                    content = item.content if item.content is not None else str(item.value)
                    source_id = f"{item.source}:{item.fact_id}:{item.content_span}"
                    sources.append(ClaimSource.seal(source_id, content))
                    source_fact_ids.append(item.fact_id)
                    origin[source_id] = item.fact_id
                    relevance[source_id] = item.relevance_score or 0.0
                sources = tuple(sources)
                if not sources:
                    return _abstained(RouteState.ABSTENTION.name, len(documents))

                if context_intents:
                    source_ids_by_fact: dict[int, list[str]] = {}
                    for source, fact_id in zip(sources, source_fact_ids):
                        source_ids_by_fact.setdefault(fact_id, []).append(source.source_id)
                    intents = tuple(ContextIntent.seal(
                        intent.intent_id, intent.text,
                        frozenset(source_id for fact_id in intent.fact_ids
                                  for source_id in source_ids_by_fact.get(fact_id, ())))
                                    for intent in context_intents
                                    if any(fact_id in source_ids_by_fact
                                           for fact_id in intent.fact_ids))
                    if not intents:
                        return _abstained("ABSTAIN_NO_CONTEXT_INTENT", len(documents))
                else:
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

                selector_proof_closed: bool | None = None
                selector_residual: tuple[str, ...] = ()
                if profile.answer_render_mode == "full_dossier":
                    answer_lines = tuple(AnsweredClaim(
                        claim.surface, origin.get(claim.source_id, -1), claim.source_id,
                        relevance.get(claim.source_id, 0.0)) for claim in chosen)
                elif profile.answer_selector == "hpps":
                    answer_lines, selector_proof_closed, selector_residual = _pick_hpps_answer(
                        chosen, relevance, origin, question, profile)
                else:
                    answer_lines = _pick_clean_answer(chosen, relevance, origin, question, profile)
                answer_bytes = sum(len(line.text.encode("utf-8")) for line in answer_lines)
                direct_answer = _read_direct_answer(
                    self.direct_answer_reader, question, answer_lines)

                return AnsweredResult(
                    "RESOLVED", tuple(claims), answer_lines, sources, core, ranked,
                    len(documents), len(sources), answer_bytes, profile.answer_selector,
                    selector_proof_closed, selector_residual, direct_answer,
                    chosen_size=len(chosen))
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
    deliberately short synthetic claim.

    2026-08-23: this narrow case was observed on real data after all -- a 120-document HuggingFace
    public-domain fiction corpus (raw Project Gutenberg text windowed into fixed 500-byte records,
    so genuinely relevant spans are routinely sentence fragments) made the tier-one cascade stop on
    a claim scored 0.01 while a claim scored 1.0 sat excluded from that tier's own candidate pool.
    `profile.answer_completeness_bonus` (default `None`, preserving this exact tiered cascade
    byte-for-byte) opts into `_pick_relevance_weighted_answer` below, which keeps every claim
    eligible and folds "looks like a complete sentence" into the existing greedy gain formula as an
    additive bonus instead of a hard per-tier exclusion -- matching the bonus-not-gate pattern this
    file already uses for `anchor_bonus`/`specificity_bonus` rather than redesigning the tier
    cascade itself without the real-data evidence the module docstring above says this needs."""
    if profile.answer_completeness_bonus is not None:
        return _pick_relevance_weighted_answer(chosen, relevance, origin, question, profile)
    question_tokens = _content_words(question)

    def build_shortlist(min_length: int, require_sentence: bool):
        shortlist, seen_clean = [], set()
        for claim in sorted(chosen, key=lambda c: -relevance.get(c.source_id, 0.0)):
            text = claim.surface.strip()
            normalized = " ".join(text.split()).lower()
            if normalized in seen_clean:
                continue
            # These tiers are calibrated against MemGym-DR's own English academic prose (see the
            # module docstring) -- CJK conveys substantially more content per character, so
            # applying the same character counts unscaled silently discarded ordinary, complete
            # Chinese sentences (2026-08-19, found via code review + direct reproduction: a
            # fully-verified, correctly-relevant 24-character Chinese claim produced an empty
            # answer_lines). The /3 divisor is a reasonable estimate, not a swept constant like
            # `answer_relevance_gate_ratio` -- it stops the total failure without claiming to be
            # precisely calibrated. `.isupper()` never applies to CJK (no letter-casing) and a
            # sentence there ends in a full-width terminator, not ".", so `require_sentence` is
            # checked on its own terms for CJK text instead of via the ASCII-only rule.
            cjk = is_cjk(text)
            effective_min_length = max(1, min_length // 3) if cjk else min_length
            if len(text) < effective_min_length:
                continue
            if require_sentence:
                if cjk:
                    if not text.endswith(("。", "！", "？", "…")):
                        continue
                elif not (text.endswith(".") and text[0].isupper()):
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


def _looks_like_complete_sentence(text: str, cjk: bool) -> bool:
    """The same "is this a real, well-formed sentence" signal `_pick_clean_answer`'s tiers use as
    a hard gate -- reused here as a boolean feeding an additive score bonus instead."""
    if cjk:
        return text.endswith(("。", "！", "？", "…"))
    return text.endswith(".") and bool(text) and text[0].isupper()


def _pick_relevance_weighted_answer(chosen, relevance: dict, origin: dict, question: str,
                                    profile: EngineProfile) -> tuple[AnsweredClaim, ...]:
    """`profile.answer_completeness_bonus` opt-in: one flat candidate pool instead of a tiered
    cascade, so a highly relevant fragment can never be excluded outright just for not "looking
    like a sentence" -- see `_pick_clean_answer`'s docstring for the real-corpus case this fixes.

    Only the loosest configured `min_length` (never `require_sentence`) still filters candidates,
    keeping a floor against genuinely trivial fragments while letting relevance decide the rest.
    """
    question_tokens = _content_words(question)
    loosest_min_length = min(min_length for min_length, _ in profile.answer_min_length_tiers)

    shortlist, seen_clean = [], set()
    for claim in sorted(chosen, key=lambda c: -relevance.get(c.source_id, 0.0)):
        text = claim.surface.strip()
        normalized = " ".join(text.split()).lower()
        if normalized in seen_clean:
            continue
        cjk = is_cjk(text)
        effective_min_length = max(1, loosest_min_length // 3) if cjk else loosest_min_length
        if len(text) < effective_min_length:
            continue
        seen_clean.add(normalized)
        shortlist.append((claim, cjk))
        if len(shortlist) >= profile.answer_shortlist_size:
            break

    if not shortlist:
        return ()

    top_relevance = relevance.get(shortlist[0][0].source_id, 0.0)
    gate = top_relevance * profile.answer_relevance_gate_ratio
    eligible = [(c, cjk) for c, cjk in shortlist if relevance.get(c.source_id, 0.0) >= gate]

    def gain(claim, cjk, covered):
        new_words = _content_words(claim.surface) - question_tokens - covered
        complete_bonus = profile.answer_completeness_bonus \
            if _looks_like_complete_sentence(claim.surface.strip(), cjk) else 0.0
        return len(new_words) * (0.3 + relevance.get(claim.source_id, 0.0) + complete_bonus)

    picked, covered = [], set()
    while eligible:
        best, best_cjk = max(eligible, key=lambda pair: gain(pair[0], pair[1], covered))
        if gain(best, best_cjk, covered) <= 0:
            break
        picked.append(AnsweredClaim(
            best.surface.strip(), origin.get(best.source_id, -1), best.source_id,
            relevance.get(best.source_id, 0.0)))
        covered |= _content_words(best.surface)
        eligible.remove((best, best_cjk))
    return tuple(picked)


def _pick_hpps_answer(chosen, relevance: dict, origin: dict, question: str,
                      profile: EngineProfile) \
        -> tuple[tuple[AnsweredClaim, ...], bool, tuple[str, ...]]:
    """Rank already-verified claims with HPPS; never upgrades selection to factual proof.

    D150 showed a large CJK evidence-selection gain, but its Chinese questions retained open
    obligations. The returned closure/residual telemetry is therefore explicit: source reopening
    remains the authority, while `proof_closed=False` prevents callers from confusing a useful
    evidence shortlist with a complete typed answer proof.
    """
    if not chosen:
        return (), False, ()
    documents = tuple(RawCausalDocument(
        index, claim.surface, 0, index) for index, claim in enumerate(chosen, 1))
    engine = MaterializedIndependentHorizonSearchEngine(documents)
    result = engine.search(question, max_results=profile.hpps_max_results,
                           exploration_reserve=profile.hpps_exploration_reserve,
                           core_width=1)
    by_id = {index: claim for index, claim in enumerate(chosen, 1)}
    lines = tuple(AnsweredClaim(
        by_id[fact_id].surface.strip(),
        origin.get(by_id[fact_id].source_id, -1),
        by_id[fact_id].source_id,
        relevance.get(by_id[fact_id].source_id, 0.0)) for fact_id in result.fact_ids)
    return lines, result.proof_closed, result.residual


def _read_direct_answer(reader: DirectAnswerReader | None, question: str,
                        evidence: tuple[AnsweredClaim, ...]) -> DirectAnswer:
    if reader is None:
        return DirectAnswer()
    try:
        proposal = reader.propose(question, evidence)
    except Exception as exc:  # readout is optional/untrusted; evidence must survive its failure
        return DirectAnswer("abstain", method="reader_error",
                            residual=(type(exc).__name__,))
    if proposal is None:
        return DirectAnswer("abstain", method="reader_abstained")
    by_source = {item.source_id: item.text for item in evidence}
    if any(source_id not in by_source for source_id in proposal.source_ids):
        return DirectAnswer("abstain", method="invalid_source",
                            residual=("unknown_source_id",))
    if not any(proposal.text in by_source[source_id] for source_id in proposal.source_ids):
        return DirectAnswer("abstain", method="invalid_span",
                            residual=("text_does_not_reopen",))
    # Source containment proves an extractive candidate, not that it answers the question.
    return DirectAnswer("candidate", proposal.text, proposal.method, proposal.source_ids,
                        False, proposal.residual)


__all__ = ["AnswerContextIntent", "AnsweredClaim", "AnsweredResult", "DirectAnswer", "DirectAnswerProposal",
           "DirectAnswerReader", "HorizonAnswerEngine"]
