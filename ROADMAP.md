# Roadmap

This page says where the project is headed, not exactly when it'll get there. ProofRay only ever
claims a capability once it's been built and independently tested. See
[Benchmarks](docs/BENCHMARKS.md) for what's actually validated today, and treat everything below
as direction, not a promise or a release date.

## Remembering people across conversations

The primary product direction is personal, longitudinal memory: remembering what a person said or
experienced across separate conversations, preserving who said it and when, and returning the exact
source when asked later in ordinary language. Questions such as "do you remember that day?" require
current conversational context, temporal and person binding, and an honest clarification when several
past episodes still fit. A plausible guess is not a remembered fact.

Development therefore prioritizes timestamped multi-session conversations, preference and knowledge
updates, commitments, informal references, typos/slang, absent memories, ambiguity and strict user-scope
isolation. Public long-memory benchmarks are useful parts of this portfolio, but no one dataset defines
the product. Arithmetic reading-comprehension corpora remain regression tests for the proof executor;
they are not the primary measure of whether ProofRay works as a memory.

The frozen research protocol is documented in
[ProofRay Personal Recall](lab/PROOFRAY_PERSONAL_RECALL_CHARTER.md). It reports each underlying memory
need separately from its formal, informal, noisy and cross-language renderings, so adding paraphrases
cannot inflate the number of independent facts solved.

The current full personal-conversation audit covers all 1,986 LoCoMo questions. Among the 1,982 with
evidence annotations, the promoted opt-in cascade reaches at least one annotated turn for
**1,770/1,982 (89.30%)**, all annotated turns for **1,580/1,982 (79.72%)**, and
**2,084/2,821 (73.87%)** turn recall at a 32-turn budget. The path combines only scorer-blind,
controlled signals: reciprocal-rank fusion, exact speaker metadata, explicit calendar coordinates,
same-session adjacency, an observable completeness gate and morphology that changes the proposal head.
These are consumed-development retrieval figures, not answer accuracy or independent transfer.

That cascade now has an opt-in Python implementation in `src/horizon_memory/`. An all-question
scorer-blind equivalence replay compared 1,986 rankings and found zero mismatches with the frozen lab
implementation. LoCoMo adapters,
scorers, categories and controls remain in `lab/`. HTTP/MCP now preserve structured
speaker/session/time and FactId-bound context intents while retaining the legacy string payload; the
conversation generator remains deploy-time opt-in. The immediate roadmap is to close remaining
event/paraphrase addressability, then action selection (`ANSWER`/`CLARIFY`/`ABSTAIN`) and exact answer
rendering. Scaling paraphrases or returning more arbitrary context is not treated as solving the
residual, and the result still requires independent multilingual confirmation.

Structured conversations now also survive restart in `OpenTextHorizonMemory`: the existing durable
sidecar attests route coordinates under a v2 fact domain and observed intent fibers under v3, while
legacy metadata-free v1 facts remain byte-compatible. This is one materialized authority ledger, not
a parallel session database, and neither metadata nor speaker identity is encoded into source text.

The finite `proof-convergent` executor and its question-bound, source-reopening direct-answer
certificate are also part of the core now. Its corrected LongMemEval final-output arm has a
byte-inherited worst-case lower bound of 90.83%, so it is enabled by default; callers can pass
`direct_answer_resolver=None` for evidence-only behavior. MemGym can use it after verified acquisition and optional
witness front-loading: closed operator worlds produce a concise direct answer; everything else keeps
the deterministic evidence result. The current MemGym audit closes 0/120 proofs, so this integration
is safe fallback infrastructure there, not the solution to its explanatory-composition residual.
This improves composition without making arithmetic the product
goal and without allowing an evaluator or language model to become runtime authority. Benchmark
activation remains metric-specific: mechanisms that have not crossed 90% on their exact final-output
arm stay opt-in rather than inheriting a score from older bytes.

## More languages

Today, the parts of ProofRay that read natural-language questions (not just store and retrieve raw
facts) have been carefully tested in **English and Portuguese**, each cleared through its own real
test against text the corresponding mechanism had never seen before. **Chinese support is next**,
and we intend to bring it in as soon as it's ready to meet that same bar, not before. Every language
after that follows the same rule: no "supports language X" claim ships without its own independent
test, the same way English and Portuguese were each proven on their own, not assumed to transfer
from the other.

## Better answers, not just better search

Some of our own testing has already shown that simply finding more of the right information isn't
always what limits a correct final answer; sometimes the harder part is putting several found
facts together correctly. Expect continued work here: not just retrieving evidence faster or more
precisely, but improving what happens after the right evidence has already been found.

## Easier to run

Today, using ProofRay means running Python. A packaged, no-install version (a plain binary you can
just double-click, with an installer for your operating system) is a direction we want to go,
along with a small local interface for people who'd rather not write code at all. Feasibility notes
for this specific effort already exist in
[ProofRay Engine's own roadmap](ProofRay%20Engine/ROADMAP.md) for anyone curious about the
technical shape of that work.

## Connecting directly to your data

Right now, connecting ProofRay to a database means writing a small amount of your own code to pull
rows out and hand them over (see the tutorial's own database examples). A more direct path,
pointing ProofRay at a database connection and letting it take it from there, is a real, larger
piece of work we'd like to take on, not a small checkbox.

## Beyond one operator, one machine

ProofRay's current security model is deliberately scoped to a single operator running it on their
own machine. Supporting a real multi-user or team deployment, with its own separate access model,
is a direction being considered for later, once the simpler case is solid.

## A note on scope

This list is intentionally general. We're an early-stage project and don't want to lock ourselves
into specifics that might change as we learn more, or take away the surprise of what's actually
coming.
