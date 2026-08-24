# ProofRay origin and design lineage

## The problem

ProofRay began as the Horizon Memory research line from a simple observation: an AI system can appear to
understand a long interaction and then lose its usable past when the context
window is truncated, summarized incorrectly, or replaced. Increasing a context
window delays that failure; it does not create durable, independently
addressable memory.

The founding objective was therefore broader than chat history or a vector
database:

> Build a memory that survives beyond a model's active context, can connect to
> any model or deterministic engine, preserves the origin of its evidence, and
> refuses to claim knowledge it cannot verify.

That objective remains the reason Horizon is standalone. A language model is a
possible reader or consumer, never the authority of the stored memory.

## Origin in Q-HDRE

The architecture emerged from the private Q-HDRE research program. Q-HDRE used
ideas from distributed representation, dynamical systems, causality and
information boundaries as sources of computational hypotheses. It did **not**
claim that software was quantum, cosmological, conscious, or physically
equivalent to the systems that inspired the vocabulary.

The research discipline followed a recurring chain:

```text
observation → computational principle → mechanism → falsifiable hypothesis
            → controlled experiment → retain, revise, or reject
```

This distinction mattered. Names such as horizon, field, phase, energy and
resonance were useful only when they produced a precise operation, invariant or
ablation. Metaphors that did not survive measurement were removed from the
critical path.

The full Q-HDRE derivations, theory notebooks, internal equations, development
logs and unpublished protocols are intentionally not distributed in this
repository. This document records lineage and engineering principles, not the
private research corpus.

## How the architecture was reduced into software

### 1. Fixed budgets instead of imaginary infinite memory

Early work asked what should happen when history is larger than active state.
Every meaningful comparison had to use an explicit byte or context budget.
This prevented a larger cache from being mistaken for a better memory.

### 2. Compression had to preserve addressable evidence

An early Horizon hypothesis used multiple temporal scales and selectively
retained surprising anchors. Controlled experiments showed that several of
those attractive ideas were weaker than simple, strong controls. Surprise was
not a reliable universal predictor of future usefulness, and selective anchors
could steal capacity from uniform historical coverage.

Those negative results changed the design. The surviving lesson was not
“compress everything intelligently.” It was:

- preserve a soft floor of information across history;
- keep critical identities addressable;
- measure loss rather than hiding it;
- never describe lossy reconstruction as exact recall.

### 3. Identity and provenance became non-negotiable

Similarity alone could retrieve plausible but wrong material. Horizon therefore
separated candidate generation from authority. Facts, sources, versions,
spans, scopes and deletion state became explicit identities. Ranking may
propose; it cannot silently validate.

### 4. Causality replaced narrative confidence

The system was redesigned around operations whose inputs and conditions can be
recomputed. A result may carry evidence, an operation and a proof. If a required
denominator, identity, time boundary or closed universe is missing, the correct
state is abstention.

This is why benchmark contradictions are recorded rather than learned as if
they were causal truth.

### 5. Durability became part of memory semantics

A memory that returns an acknowledgment and then loses the fact after a crash
is not reliable memory. The implementation consequently gained append-only
logging, versioned publication, copy-on-write generations, snapshots,
compaction, recovery, terminal deletion and conservative garbage collection.

These mechanisms are not branding. They define what “remembered” means across
process boundaries.

### 6. The model was removed from the trusted core

Model experiments showed that a small generator could be useful as a reader but
was not a dependable compiler of memory authority. Horizon's core path became
offline and deterministic. Model adapters receive bounded evidence only after
memory and retrieval have done their work.

### 7. Search became a proof-oriented research surface

The retrieval program grew beyond a single lexical or vector score. The public
research namespace explores candidate generation under proof obligations,
causal exclusions, materialized statistics and strict evidence budgets.

BM25 remains a strong baseline and is not dismissed. Horizon Search must earn
promotion through frozen, paired evaluations; a theoretical advantage is not a
measured one.

## Principles that survived

The public implementation is organized around these surviving principles:

1. **Model independence** — memory exists before and after any reader model.
2. **Bounded operation** — bytes, context and active evidence are explicit.
3. **Stable identity** — repeated representations do not create new evidence.
4. **Causal availability** — future or unauthorized information cannot affect a
   past decision.
5. **Proof-carrying answers** — resolved operations can be recomputed from their
   bound evidence.
6. **Honest abstention** — missing authority is a state, not an invitation to
   guess.
7. **Durable publication** — acknowledged state must be covered by published,
   recoverable state.
8. **Negative results matter** — failed hypotheses constrain future claims.
9. **Replaceable consumers** — local models, hosted models, programs and humans
   may all consume the same evidence contract.
10. **Open technical commons** — the canonical engine should remain inspectable
    and improvable by its users.

## What this history does not claim

ProofRay does not currently claim infinite memory, universal semantic
understanding, replacement of every database or search engine, elimination of
all hallucinations, or 99% end-to-end accuracy over arbitrary language.

The project is an alpha research architecture with a tested deterministic core
and experimental natural-language coverage. Public claims will be tied to a
specific release, protocol, dataset split and immutable result artifact.

## Attribution

ProofRay, the historical Horizon Memory architecture and their originating research direction were created by **Kaue
Oliveira Costa (kyuubyN)**, Brazil, ORCID
[0009-0009-8502-3220](https://orcid.org/0009-0009-8502-3220), who remains the
founding maintainer and research lead. Later contributors retain full credit
for their work without erasing the documented origin of the architecture.
