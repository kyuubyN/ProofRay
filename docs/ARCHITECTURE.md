# Architecture

Horizon Memory separates durable state, retrieval, proof and consumption. This
keeps a model useful without allowing it to rewrite memory authority.

```mermaid
flowchart LR
    S["Sources and applications"] --> A["Deterministic adapters"]
    A --> D["Durable memory"]
    D --> R["Retrieval and HSSD"]
    R --> P["Evidence and proof verification"]
    P --> C["Any consumer"]
    C -->|"optional feedback with provenance"| A
    X["LLM or local model"] -. "optional reader" .-> C
```

## Stable surface

The `horizon_memory` namespace provides:

- scoped, versioned writes;
- reads and immutable read views;
- terminal deletion;
- crash-aware recovery;
- compaction and conservative reclamation;
- typed causal facts and programs;
- bounded evidence packs;
- HSSD compilation, selection and proof verification;
- exact-span claim sealing, provenance-carrying proof dossiers and lossless
  rendering (`claim_composer`, `proof_dossier`, `lossless_proof_answer`);
- explicit result and abstention states;
- audit and storage ledgers.

## Durable engine

The private implementation namespace `horizon_memory._engine` contains the
append-only log, artifact descriptors, manifests, publication protocol,
recovery, compaction, snapshots and garbage-collection machinery. Applications
should not import it directly.

The central publication rule is that an applied acknowledgment must correspond
to durable published state. Recovery observes published authority and does not
silently invent repairs.

`HorizonMemory` keeps a small, bounded LRU cache of already-opened generation
handles, keyed by the immutable manifest digest that identifies a generation.
A write always produces a new digest rather than mutating an old one, so the
cache needs no invalidation logic for correctness — it only removes redundant
WAL re-verification on repeated reads against a generation that has not
changed, a real (measured, ~2.7x) latency improvement on the routing path
with no behavioral change.

## Evidence boundary

Evidence is treated as untrusted input until its identity, source digest, span,
scope and version are checked. Candidate generators are replaceable and may be
wrong. A verifier, not a ranking score, decides whether a result has authority.

## Search and candidate routing

Search never produces authority directly; it produces candidates a verifier
then checks. A `CandidateGenerator` scores documents or claims against a
query and returns a ranked `CandidateList`; `HorizonVerifier` re-opens each
candidate's own source in the durable store and checks its identity, span,
version and scope. `SemanticRouter.route()` ties the two together and
returns exactly one `RouteState`: `EVIDENCE` when verified candidates
satisfy the query, `ABSTENTION` when none do, or `ABSTAIN_SCOPE` when the
query itself targets a scope the caller isn't authorized for — never a
silent empty result standing in for a failure.

Two granularities of candidate generator exist. Whole-document generators
(lexical, BM25, dense, hybrid, causal-weave) return one candidate per fact.
`ClaimGenerator` instead splits a document into sentence-level claims (never
splitting a decimal number or code punctuation, `claim_spans`) and scores
each one independently against six typed channels computed by
`MaterializedRawCausalSyndromeIndex` — lexical overlap, character-trigram
("sublexical") overlap for paraphrase and typo robustness, entity match,
relation match, an "observable" exact-value-match signal, and a
contradiction penalty when a claim's polarity or modality conflicts with the
query — combined by a fixed weighted sum (`DEFAULT_WEIGHTS`; only lexical,
sublexical and contradiction are non-zero by default, chosen and regression-
tested against a real modal-distractor failure, not a default guess). A
document can contain one claim that answers a query and several that don't;
claim-level generation lets those compete independently for budget instead
of the whole document winning or losing as one unit.

Routing admission can also be conformally calibrated:
`ConformalClaimGenerator`/`ConformalDocumentGenerator` wrap a base generator
with a `ConformalCalibrator` fit on a held-out calibration set, and admit a
candidate whenever its calibrated p-value exceeds `epsilon` — a statistical
marginal-coverage guarantee ("the true source is in the routed set with
probability at least 1-epsilon"), not a ranked top-k cutoff. A *smaller*
epsilon is a *stronger* guarantee and admits *more* candidates, trading
precision for a bound on how often the right source is missed outright.

Selection happens at two budgets, not one. `EvidencePack.budgeted_items()`
fills an acquisition-stage budget from verified candidates across every
routed source, with optional merge behavior: `global_sort_alpha` replaces
the default rank-major fill with one blended sort key per item (document-
level `source_priority` weighted against the item's own channel relevance),
and `dedup_threshold` rejects near-duplicate claims. `proof_dossier.
build_proof_dossier` then fills a second, tighter final-answer budget from
that acquisition pool — optionally as budget-constrained submodular
selection (`submodular_budget_fill`) rather than a plain rank-major cut —
with `anchor_bonus`/`specificity_bonus` biasing selection toward claims
carrying a number, proper noun or other locally rare token: the kind of
content most likely to be the one fact a query actually needs, not just a
topically related sentence.

None of this changes what counts as authoritative. However a candidate is
scored, ranked or admitted, it only ever proposes; `HorizonVerifier` is
still what decides whether a result is trustworthy, per the boundary above.

## Research retrieval

`horizon_memory.research` exposes experimental proof-pressure and feedback
transport engines. The namespace is opt-in because retrieval hypotheses evolve
faster than the storage contract.

These engines may combine lexical candidates with causal observables, hard
exclusions and evidence budgets. They must continue to report paired BM25
baselines and may not convert retrieval hit rate into an answer-accuracy claim.

The namespace also exposes `collapse_evidence_items`: an opt-in mechanism that
excludes superseded restatements of a value (a revised date, a reversed
decision) from an already-verified evidence pool, so a downstream reader
isn't handed several conflicting values with nothing marking which is
current. It reuses the stable namespace's `TypedCausalExecutor` unmodified
for resolution — the same clock-and-orbit logic `typed_causal_program`
already validated for query answers, repurposed here as a general
"which-value-is-current" primitive — and only adds detection on top: does a
claim carry an anchor (a number or proper noun) at all. Measured, not
assumed, with mixed results across language and noise conditions; see
`RESEARCH.md` for the honest numbers and why it stays opt-in.

## The validated reading pipeline (partially promoted; full pipeline still a private prototype)

A private research line reached a different, more directly validated
mechanism for turning stored facts into a reader-ready evidence pack, described
in five stages below. Parts of it have since moved into the stable namespace:
claim-level (sentence-span, not whole-document) candidate generation and
conformal-calibrated document routing, the mechanisms behind stages 1-2, now
ship as `ClaimGenerator` and `ConformalClaimGenerator`/`ConformalDocumentGenerator`,
with the budget-fill merge options (`global_sort_alpha`, `source_priority`,
`dedup_threshold`) on `EvidencePack.budgeted_items()`. Stages 3-4 — exact-span
claim sealing/verification and provenance-carrying packet assembly, and the
plain, lossless rendering step — now ship too, as `claim_composer`
(`ClaimSource`, `AuthorizedClaim`, `extract_authorized_claims`),
`proof_dossier` (`build_proof_dossier`, including the budget-fill merge
options above plus `submodular_budget_fill`, `anchor_bonus` and
`specificity_bonus`) and `lossless_proof_answer`
(`render_lossless_proof_answer`). Stage 5 — the reading contract itself — is
a downstream-consumer instruction, not a storage mechanism, and stays out of
the core by design; see `horizon_memory.adapters` for where a caller wires an
actual reader.

The pipeline has five stages:

1. **Full claim scan.** Every factual claim available to an episode is
   extracted up front — not only a pre-filtered "supporting" subset. Working
   from the complete candidate pool, rather than a subset chosen too early,
   turned out to matter more than any later ranking refinement.
2. **Budget-aware causal selection.** Claims are scored for relevance to the
   query's causal thread and packed into a fixed byte budget. The scorer
   specifically protects the most causally central thread from being starved
   when many competing turns want the same budget, rather than spreading
   the budget evenly regardless of relevance.
3. **Packet assembly with provenance.** Selected claims are packaged with
   their source, obligations (role, timing, unit, cause, identity,
   completeness) and a verifiable digest. Obligations are treated as
   non-negotiable requirements distinct from ordinary relevance — a claim can
   be topically relevant and still fail an obligation.
4. **Plain natural-language rendering.** The packet is rendered as an
   ordinary flat list of complete sentences — no tags, no internal IDs, no
   visible section headings. Controlled ablations found that every attempt to
   add visible structure (tags, headings, explicit scaffolding) to the
   evidence surface *hurt* small readers, while a plain natural-language
   surface with byte-identical content did not. This was the single largest
   reader-side improvement found in the whole program.
5. **An explicit extractive reading contract.** The consumer is instructed to
   answer only from the supplied evidence, preserve exact names, numbers and
   relations, combine every relevant statement into one answer, and abstain
   explicitly rather than guess when the evidence does not support an answer.
   This contract, held constant, is what let stages 1-4 be evaluated on a
   level field.

This pipeline, at a byte budget matched to what it naturally uses, was
compared against a strong lexical baseline (BM25) at both a matched budget
and a substantially larger one, scored by a validated LLM-judge instrument
(see [Benchmarks](../BENCHMARKS.md)). It won at both budgets, and giving the
baseline more budget did not close the gap. What still limits accuracy beyond
that point — whether more of the right evidence is being missed, or whether
a small reader model struggles to compose evidence it already has into a
correct answer — is an open, actively-tested question, not yet closed.

## External readers

`horizon_memory.adapters` connects bounded evidence to independent readers.
Adapters cannot alter durable memory, declare proof validity or enable network
access implicitly. Remote access is application-authorized and outside the
offline core.

## Current boundary

The durable substrate currently stores unsigned-byte values and references
richer application content through identities and provenance. Natural-language
compilation supports only explicitly implemented operation families. Unknown or
ambiguous input abstains.

This boundary is deliberate: a narrow verified operation is part of Horizon; a
broad unverified guess is not.
