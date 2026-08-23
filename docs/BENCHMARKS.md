# Benchmarks and claim boundaries

## Proof-convergent execution development audit

On the consumed first 120 LongMemEval-S episodes, the single zero-model laboratory executor now
resolves 35/120; all 35 are development-gold compatible and reopen from exact source proofs.
Integrated states are 35 resolved, 21 abstain, 3 contested and 61 unsupported. The compact `HSC1`
envelope costs 92–311 bytes (mean 153.14 B) per resolved answer. Twenty-seven outputs repair prior D145
failures, so the deterministic policy “proof-convergent answer when resolved, otherwise D145
composer” projects 119/120 = 0.9917 without an oracle at runtime. This remains a **counterfactual on
consumed development data**; no new frozen independent protocol has confirmed the post-holdout
operators. See `lab/results/proof-convergent-integrated-dev-audit-v6.json`; v1–v5 are retained as
history. Nothing here is a universal ≥90% claim or a core promotion. The one remaining row abstains
on a real corpus/annotation ambiguity.

### Untouched post-calibration holdout v3: 119/120 did not transfer end-to-end

Ordinals 400–499 were frozen before inspection, after the 200–399 calibration range. Two independent
seed-0 compositions produced identical records for all 100 rows (canonical row digest
`057374b2...ce3e5`): 5 proof answers and 95 deterministic fallbacks. The preregistered zero-model
scorer then produced **2/100 strict exact match**, mean token F1 **0.03724**, proof coverage **5%**,
proof selective exact match **2/5**, and fallback exact match **0/95**. This independently rejects the
consumed-development 119/120 as end-to-end evidence.

Post-score diagnosis found four of five proof answers semantically compatible: two exact, one
punctuation-only mismatch and one answer that conservatively retained source uncertainty. The factual
error was a cumulative inventory query treated as a count of acquisition events (2 vs 5); a
post-holdout last-write fix now returns five but cannot alter v3. More importantly, fallback outputs
were roughly 24.6 KB evidence packs, not concise answers. Their old semantic-judge performance was a
retrieval/readability result, not standalone answer execution. See
`lab/results/proof-convergent-longmemeval-holdout-score-v3.json` and
`lab/results/proof-convergent-longmemeval-holdout-diagnosis-v3.json`.

Post-score observer-role diagnosis found 55 questions referring to prior assistant speech. Treating
assistant text as an attested utterance (not world truth), direct HPPS plus old-user-turn→assistant-
successor transport reaches 33/35 literally present answers. A first structural decoder was rejected:
31 emissions but only 1 exact/3 containment. The route gain is retained as a diagnostic; the decoder
is not integrated. See `lab/results/assistant-utterance-causal-route-dev-v0.json`.

The required independent check subsequently **rejected** that development interpretation. On a
pre-frozen 80-episode LongMemEval slice (120–199), two `PYTHONHASHSEED=0` compositions reproduced all
80 row hashes exactly before gold was opened. Strict deterministic scoring gave **0/80 normalized
exact match**, mean token F1 **0.02607**, proof coverage 6/80, proof selective EM **0/6**, and fallback
EM **0/74**. All six proof emissions were factual operator errors. Literal containment was 0.2625 but
is not accuracy. See `lab/results/proof-convergent-longmemeval-holdout-score-v2.json` and
`lab/datasets/manifests/proof-convergent-longmemeval-holdout-v2.json`. The earlier development
counterfactual must not be cited as universal accuracy. The post-holdout fail-closed fixes require a
new independent evaluation and are not retroactively validated on this consumed holdout.

Horizon Memory has different validation surfaces. Results from structured
causal execution, retrieval and language-model reading are reported separately
because they measure different things. Retrieval hit rate is not answer
accuracy, and selective precision must always be accompanied by coverage.

The tables below summarize frozen private-laboratory protocols that motivated
the standalone implementation. Dataset files, answer keys and private Q-HDRE
development records are deliberately not distributed in this repository. A
public artifact release must include the corresponding manifests, result
digests and reproduction commands before these numbers are treated as an
independently reproducible release claim.

## Structured and verifiable domains

These experiments exercise authenticated ingestion, typed execution, HSSD and
proof reopening. A result is accepted only when its source span and digest
verify; conflicts and unsupported operations abstain.

| Protocol | Domain | Evaluated cases | Result | Boundary |
|---|---|---:|---:|---|
| V54 | generated typed causal holdout | 649 | 649/649 | generated structured input |
| V55 | VTE and llama.cpp JSON telemetry | 48 queries | 48/48 | fixed JSON adapter |
| V59 | full standalone causal route | 640 | 640/640 | generated structured input |
| V60 | temporal operations | 512 | 512/512 | generated typed input |
| V64 | timestamped runtime log | 30 holdout queries | 30/30 | preregistered ten-field grammar |
| V76 | Git metadata | 82 | 82/82 | generated structural queries |
| V78 | filesystem metadata, zero-shot | 256 | 256/256 | metadata, not file-content QA |

The promoted claim is narrow: Horizon can achieve exact, proof-verifiable
execution in structured or explicitly compiled domains. These results do not
show 99% accuracy over unrestricted natural language.

## Retrieval against BM25

Horizon Proof-Pressure Search (HPPS) protects a lexical core and admits other
candidate surfaces under an evidence budget. Each comparison below used a
frozen protocol and matched or bounded evidence cost.

| Evaluation set | Queries | System | Hit@32 | Recall@32 |
|---|---:|---|---:|---:|
| LoCoMo | 1,676 | BM25 | 0.737894 | 0.662636 |
| LoCoMo | 1,676 | Horizon HPPS | **0.812452** | **0.737101** |
| SciFact | 206 | BM25 | 0.883495 | 0.872573 |
| SciFact | 206 | Horizon HPPS | **0.898058** | **0.885922** |
| NFCorpus | 323 | BM25 | 0.733746 | 0.197934 |
| NFCorpus | 323 | Horizon Pareto tail | **0.758514** | **0.210711** |

Hit@1 was preserved in these evaluations. Horizon also used fewer mean
evidence bytes in the reported protocols. The supported claim is improvement
over BM25 on these frozen evaluations, not universal search superiority.

### Diagnostic re-open with the current engine (not a fresh holdout claim)

All three rows above come from one-shot frozen holdouts already opened once
against a 2026-08-13 engine snapshot. At the project owner's explicit
direction, the same three holdouts were reopened against the current engine
(this session's contradiction-channel numbers-rule fix and
modality-confirmed-finding exception, both found and fixed on an unrelated
dataset) to check whether either fix changes retrieval quality here too.
**This is a genuine, acknowledged departure from this project's own "open a
holdout exactly once" discipline** — the numbers below are diagnostic-only,
not a second independent holdout confirmation, following the same downgrade
already applied elsewhere in this project's own record to a re-read
confirmation split.

Every metric each original protocol reported, not just the two headline
columns:

**LoCoMo (1,676 q, Horizon HPPS) — every reported metric is byte-identical:**

| Metric | Original | Reopened | Delta |
|---|---:|---:|---:|
| Hit@1 | 0.296695 | 0.296695 | 0 |
| Hit@5 | 0.587241 | 0.587241 | 0 |
| Hit@10 | 0.695619 | 0.695619 | 0 |
| Hit@32 | 0.812452 | 0.812452 | 0 |
| Gold recall@1 | 0.263661 | 0.263661 | 0 |
| Gold recall@5 | 0.521064 | 0.521064 | 0 |
| Gold recall@10 | 0.617877 | 0.617877 | 0 |
| Gold recall@32 | 0.737101 | 0.737101 | 0 |
| MRR | 0.422516 | 0.422516 | 0 |
| Mean bytes selected | 3914.743 | 3914.743 | 0 |
| Mean items selected | 27.61 | 27.61 | 0 |

**SciFact (206 q, Horizon HPPS) — every Hit@/Recall@ depth identical; MRR/MAP shift under 0.02pp:**

| Metric | Original | Reopened | Delta |
|---|---:|---:|---:|
| Hit@1 | 0.577670 | 0.577670 | 0 |
| Hit@5 | 0.766990 | 0.766990 | 0 |
| Hit@10 | 0.820388 | 0.820388 | 0 |
| Hit@32 | 0.898058 | 0.898058 | 0 |
| Recall@1 | 0.557848 | 0.557848 | 0 |
| Recall@5 | 0.746359 | 0.746359 | 0 |
| Recall@10 | 0.804612 | 0.804612 | 0 |
| Recall@32 | 0.885922 | 0.885922 | 0 |
| MRR@32 | 0.669579 | 0.669780 | +0.000201 |
| MAP@32 | 0.659173 | 0.659347 | +0.000175 |
| NDCG@10 | 0.694336 | 0.694336 | 0 |
| Mean bytes selected | 50021.956 | 50129.485 | +107.53 |
| Mean items selected | 31.117 | 30.990 | -0.126 |
| Max bytes (any query) | 75693 | 75774 | +81 |

**NFCorpus (323 q, Pareto tail) — the one dataset with a real, if small, shift:**

| Metric | Original | Reopened | Delta |
|---|---:|---:|---:|
| Hit@1 | 0.439628 | 0.439628 | 0 |
| Hit@5 | 0.650155 | 0.650155 | 0 |
| Hit@10 | 0.687306 | 0.690402 | +0.003096 |
| Hit@32 | 0.758514 | 0.755418 | -0.003096 |
| Recall@1 | 0.056574 | 0.056574 | 0 |
| Recall@5 | 0.118790 | 0.118790 | 0 |
| Recall@10 | 0.150154 | 0.151131 | +0.000977 |
| Recall@32 | 0.210711 | 0.210521 | -0.000191 |
| MRR@32 | 0.528006 | 0.528004 | -0.000002 |
| MAP@32 | 0.137163 | 0.137814 | +0.000651 |
| NDCG@10 (linear gain) | 0.321416 | 0.322115 | +0.000700 |
| Mean bytes selected | 48946.037 | 49424.433 | +478.40 |
| Mean items selected | 31.724 | 31.669 | -0.056 |
| Max bytes (any query) | 63324 | 64663 | +1339 |

LoCoMo's Horizon-side numbers are exactly byte-identical across all eleven
reported metrics — the fix changed nothing measurable here. SciFact keeps
every Hit@/Recall@ depth exactly identical; only MRR/MAP move, by under
0.02 percentage points, and NDCG@10 (which only looks at the top 10) is
untouched — meaning the small reordering the fix caused happened below
rank 10, not within it. NFCorpus's Pareto-tail arm is the one real,
non-trivial shift: Hit@32 down 0.31pp exactly offset by Hit@10 up 0.31pp
(content moved between rank bands, not lost), every other metric moving
by under 0.1pp. In all three datasets, BM25's own numbers (which never
touch the contradiction channel) are unchanged to the last reported
digit, confirming nothing else in the pipeline shifted — the fix is the
only variable that changed between the original and reopened runs.

## Natural-language reader pilot

A public 30-question LongMemEval-S pilot compared no memory, turn-level BM25
and Horizon HPPS under the same 2,048-token evidence limit. Qwen 2.5, Qwen 3,
Granite and Gemini consumed the same interface.

- Horizon beat BM25 on all three diagnostic response metrics only with Qwen 3.
- Results were mixed for Granite and lower in token F1 for Qwen 2.5 and Gemini.
- BM25 and Horizon both reached 0.9667 session hit; BM25 had higher mean
  session recall.
- The pilot did not run the official LongMemEval judge and is not an official
  LongMemEval accuracy score.

This result rejects universal end-to-end dominance of the current retrieval
configuration. It also supports the architecture boundary: a reader is an
optional consumer, not the authority that decides whether memory is true.

## Reading-comprehension pilot program

A separate, more recent pilot program targets a harder question than
retrieval hit rate: given a fixed byte budget and a small, non-frontier
language model as reader, does memory-selected evidence produce a *correct
answer*, not just a relevant one? This program uses a public multi-hop
reading-comprehension benchmark whose official scoring is an LLM judge
(semantic equivalence against a gold answer, continuous 0-1 scale with
partial credit for a partially correct answer), not term overlap.

**A metric-validity lesson worth recording.** An earlier internal scorer
(verbatim-fact-assertion matching) was found, on audit, to score the literal
correct gold answer at 0% — it required transcription that contradicted the
reading contract's own instruction to combine evidence into one paragraph
rather than quote it verbatim. It was retracted, and every prior claim built
on it was struck. The replacement is a validated LLM-judge instrument: it was
checked against a synthetic battery covering a correct answer, a
conversationally-reframed correct answer, a partial answer, a cross-topic
wrong answer, a same-topic hard-negative wrong answer, an explicit
abstention, and an empty response — and only promoted once it separated
correct from hard-negative-wrong by a wide margin, graded partial credit
in between rather than snapping to 0/1, and was invariant to conversational
framing. Two independent backends passed this validation and cross-check
each other. This is the instrument behind every number below.

**Result, on the validated instrument, N=88 fully paired episodes, one
consistent small reader throughout:**

| Evidence source | Mean judge score (0-1 scale) |
|---|---:|
| Memory-selected packet (matched budget) | **0.72** |
| BM25, same byte budget | 0.55 |
| BM25, ~3x the byte budget | 0.50 |

The gap between memory-selected evidence and matched-budget BM25 is large
and statistically clear (paired 95% confidence interval excludes zero).
Giving BM25 roughly three times more budget does not close the gap — it
makes BM25 slightly *worse*, not better, meaning the win is not explained by
BM25 simply needing more room. The reference dataset's own published
baseline (a standard retriever paired with a much larger hosted model)
scored 0.555 on the same official scoring family; the memory-selected packet
above, paired with a small (single-digit-billion-parameter) reader, already
clears that number on a comparable scale. This is encouraging for the
project's core hypothesis — that the right memory substrate lets a small
model do more with less — but it is a comparable-scale anchor, not a
byte-exact reproduction of the published baseline's exact harness, and it is
still well short of the program's long-term accuracy target (see below).

**This question has since been resolved.** The open question was whether the
remaining gap above 0.72 is better explained by evidence still being
incomplete at this budget, or by the reader's difficulty composing evidence
it already has into one correct answer. An oracle-style ceiling test (a
reader handed the literal correct answer directly) scored above 0.97,
confirming the reading contract itself is not the bottleneck, but that
alone did not distinguish the two remaining explanations. A later causal
test settled it directly: taking the same reader's evidence packet from its
real, as-selected coverage up to a gold-directed oracle's coverage, at the
identical byte budget, did **not** move the judge score in a statistically
distinguishable way (paired delta +0.0194, 95% CI [-0.0484, +0.1000],
crosses zero), confirmed by a working negative control that did move the
score in the expected direction. **Synthesis, not coverage, is the
bottleneck**: a reader with sufficient evidence still struggles to combine
multiple facts into one correct answer; retrieving more of the right
evidence does not, by itself, close the remaining gap. This reframes what
further work on this pilot should target: the consumer-side reading
contract or reader capacity, not another retrieval/ranking improvement.

## Offline composer coverage (zero-LLM proxy, engineering diagnostic)

Distinct from the judge-scored pilot above: this is a cheaper, zero-LLM,
zero-API, zero-network diagnostic — token/anchor overlap between the
composer's rendered evidence and the gold answer — used during development
to iterate quickly before spending judge-API budget on a real reader pilot.
**It is not a judge-scored accuracy number and must not be read as one.**

Both rows below run the identical engine now shipped in
`horizon_memory.claim_composer` / `proof_dossier` / `lossless_proof_answer`
(claim-level extraction, submodular core selection under the final answer
budget, `anchor_bonus`/`specificity_bonus`-weighted fallback fill under the
acquisition budget) at the same two-stage byte budget (65,536-byte
acquisition, 24,576-byte final render). The only thing that differs between
rows is the adapter that turns each dataset's own shape into the composer's
`(sources, intents)` inputs: MemGym-DR decomposes into per-turn intents
(each multi-hop question's own sub-queries); LongMemEval-S has no native
turn/sub-query structure, so intents are scoped per haystack session instead
— the structural analog, reusing the same question text per session. This is
one engine with two adapters, not two separate pipelines.

| Dataset | N | Metric | Mean rendered coverage | Ceiling |
|---|---:|---|---:|---:|
| MemGym-DR (frozen dev split) | 120 | gold-anchor overlap (numbers, proper nouns) | **0.9166** | 0.9853 (any document, physical ceiling) |
| LongMemEval-S | 120 | gold-answer token overlap (whole answer) | **0.8384** | 0.8554 (pool coverage before the final-budget cut) |

Caveats, stated plainly:

- These are two different, dataset-specific token-overlap metrics, not one
  shared instrument. MemGym-DR's is restricted to anchor tokens after
  several documented metric-defect corrections (a bare list marker like
  "(1)" or a possessive suffix is not counted as required content);
  LongMemEval-S's is unrestricted whole-answer token overlap. The two mean
  scores are not directly comparable to each other as a single ranked
  "accuracy" — each is only meaningful against its own dataset's own
  ceiling.
- Neither number is judge-scored. Internal testing has previously found a
  token/anchor-overlap proxy can diverge from real judge/reader quality in
  either direction (a large evidence dump can score artificially high on
  overlap while a real reader gets no benefit from it, or vice versa).
  Treat these as engineering-iteration signals, not accuracy claims — see
  the judge-scored section directly below for the real-judge numbers on
  this exact composer, which is the one that should be cited as an
  accuracy result.
- MemGym-DR's own ceiling (0.9853) is well above its measured score,
  meaning the majority of the remaining gap is not a hard, dataset-authored
  wall the way some earlier internal ceiling checks found for other
  datasets — there is real, not-yet-closed headroom here. LongMemEval-S's
  own ceiling was measured only at the coverage-proxy layer (before the
  final-answer budget cut), not independently audited against its raw
  source text the way MemGym-DR's was, so it should be read as a looser
  bound.

## Composer judge-scored pilot (current pinned configuration)

Real, LLM-judge-scored results for the exact composer configuration in the
table above — not the token-overlap proxy. The composer's own deterministic
rendered text is judged directly against the gold answer, with no reader
call in the loop: an earlier internal pilot found that judging a raw,
unranked evidence dump this way inflates the score (the judge rewards
"is the answer findable somewhere in this pile of text" rather than
correctness of a real, budget-constrained selection), so this only holds
for a composed answer built under the same byte budget a real deployment
would use — exactly what both rows below are.

Judged by **`gemini/gemini-3.1-flash-lite`** — one of three backends
promoted in this project's own instrument-validation pass after each passed
all six pre-registered acceptance criteria on a hard-negative/abstention
battery (see the metric-validity note above). The chain's primary candidate,
`groq/llama-3.3-70b-versatile`, was unavailable for this session (Groq now
returns "model does not exist" for it, consistent with the model having
been deprecated on Groq's side since it was promoted); LongMemEval-S's own
per-call records confirm gemini answered all 120 of its calls, and
MemGym-DR was scored in the same session under the same condition.

| Dataset | N | Mean judge score | Paired control | Delta |
|---|---:|---:|---:|---|
| MemGym-DR (frozen dev split) | 120 | **0.950** | 0.726 (LLM reader, same evidence budget) | **+0.224**, 95% CI [0.171, 0.277] |
| LongMemEval-S | 120 | **0.767** | — (no clean paired control exists yet) | — |

MemGym-DR's confidence interval excludes zero: a real, decisive win over a
paired LLM-reader control on an actual semantic judge, not a proxy — and
the largest judge-scored margin in this document. LongMemEval-S has no
established paired control under this un-contaminated methodology yet (an
earlier internal LongMemEval judge number scored raw, un-composed evidence
directly and is now understood to be inflated the same way the MemGym-DR
evidence-only pilot above was), so its score is reported alone rather than
against a possibly-misleading baseline.

The same 120 frozen LongMemEval-S deterministic answers now pass through the public
`OpenTextHorizonMemory` facade **120/120 byte-exact**. This permits score inheritance without another
judge/API call; it is an integration proof, not a new accuracy sample. The run uses two spawned
workers, peaks at 211,320 KiB per worker and makes zero model/API/network calls. Artifact file SHA-256:
`08044f6cf710b6a016ce47262d8fd48e117f14264a5d3ce94a26382dfc650d62`.

A separate streaming, question-only audit of the opt-in HSSD operator lattice covers 495/500 questions
(99.0% candidate reachability; 394 unique plans, 101 finite ambiguities, five unsupported; max width
four) in 2.34 s at 75,600 KiB RSS. This is not 99% answer accuracy: no evidence was executed and no
candidate was declared true. It measures that the correct operator family can now remain representable
without prematurely guessing between COUNT/SUM or LOOKUP/SUM.

The existing D145 error ledger contains 28/120 failures: 10 single-session scalar/entity/duration
readouts and 18 multi-session COUNT/SUM/duration aggregations. With its binary judge, at least 16 of
those 28 must be repaired without regressions to reach 0.90. This is exactly what the
proof-convergent execution line at the top of this document targets (typed operands and
completeness, not another retrieval reranker); see
[Proof-convergent execution development audit](#proof-convergent-execution-development-audit)
for that attempt's own result: a real, measured improvement on the same consumed-development
episodes, but one that has not yet transferred to an untouched holdout (see the "Untouched
post-calibration holdout v3" subsection there), so this specific 0.90 gate remains open.

## Authorized typed-sidecar contract gate

The opt-in structured sidecar has a separate internal conformance benchmark because its denominator is
not natural language. After a 560-case development pass, core and runner SHA-256 values were frozen and
a disjoint-seed 560-case holdout was opened once.

| Family | Holdout cases | Exact contract accuracy |
|---|---:|---:|
| lookup | 80 | 100% |
| explicit version update | 80 | 100% |
| TTL boundaries | 80 | 100% |
| terminal invalidation | 80 | 100% |
| certified aggregation | 80 | 100% |
| adversarial authority failures | 80 | 100% |
| durable close/reopen | 80 | 100% |
| **total** | **560** | **100%** |

There were zero false accepts and zero crashes. Measured single-process p50/p95/p99 per scenario were
0.312/1.001/1.262 ms; peak RSS was about 46 MiB. The immutable result is
`lab/results/typed-sidecar-product-gate-holdout-v1.json` (SHA-256
`37e9bc33335e538761509aefef05038d00f884bcf0a26dfbb468d008b731b570`).

This passes the internal 80% and 90% gates in every tested family. It is generated contract
conformance, not an independent integration study and not open-text/language accuracy. It proves that
Horizon executes and rejects these already-typed cases correctly; it does not prove that arbitrary text
can be converted into those facts. Independently authored schemas/adapters are the next transfer gate.

### MemGym open-text sidecar port

`OpenTextHorizonMemory` records each arbitrary input document under the weakest universally valid
typed assertion: a sealed source contains this exact `surface_document` span. It invents no entity or
relation. Verified documents then enter the frozen conformal route, proof dossier and deterministic
composer.

All **120/120** pinned D144 MemGym-DR episodes were byte-identical to the previously judged composer
output. The existing **0.950 mean semantic-judge score** is therefore inherited without another API
call: the evaluator would receive the exact same bytes. Horizon used zero LLM/API/network calls. Peak
RSS was 322,672 KiB after the calibration builder was changed from retaining 200 complete episodes to
streaming one episode and retaining only 869 scalar scores.

| Evidence | Value |
|---|---|
| artifact | `lab/results/memgym-open-text-sidecar-port-v1.json` |
| SHA-256 | `d84650738b4cbb671dffa090e685d87e5755e492273b75bdbe46cb0c1daac93e` |
| byte-exact cases | 120/120 |
| inherited D144 judge score | 0.950 |
| LLM/API calls in Horizon | 0 |
| peak RSS | 322,672 KiB |

This is a port proof on the same frozen cases, not a new independent MemGym split. D144's semantic
judge was external evaluation infrastructure and never part of Horizon runtime. Exact byte equality is
what makes score inheritance valid.

### Open-text CJK evidence transfer

The same public `OpenTextHorizonMemory.retrieve_evidence()` path was compared byte-for-byte with the
previously frozen HPPS K=5 artifacts. It reproduced **1,002/1,002** Simplified-Chinese CMRC trial
queries and **3,524/3,524** Traditional-Chinese DRCD holdout queries exactly, with source validity 1.0.
Consequently the already-scored compact-memory containment transfers unchanged:

| Corpus | Script | Byte-exact | Gold containment in K=5 | Peak RSS |
|---|---|---:|---:|---:|
| CMRC trial, Simplified Chinese | 简体中文 | 1,002/1,002 | **0.9002** | 87,516 KiB |
| DRCD holdout, Traditional Chinese | 繁體中文 | 3,524/3,524 | **0.9835** | 90,368 KiB |

Artifacts: `cjk-open-text-sidecar-port-trial-v1.json` (SHA-256
`2114bb241bb9fcc426b5af3b9bb542e0e8d040bdbbe91783959bfc221e2a5f4f`) and
`cjk-open-text-sidecar-port-drcd-holdout-v1.json` (SHA-256
`661517f9cf3c42c97714c0562ad4ae01568c569c7237835e01dbfde9e07b6c0f`).

Containment is the memory-delivery gate: the correct answer occurs in the five verified source spans.
It is not direct-answer F1. The current short deterministic reader remains far below 0.90, so Horizon
must expose evidence accuracy and direct-answer accuracy as separate product metrics.

### Portuguese SQuAD transfer

The automatically translated Portuguese SQuAD validation split contains 10,570 questions, but only
7,653 have any literal gold answer at the annotated offset. Results therefore publish the 72.40%
total-denominator physical ceiling and use those 7,653 rows for exact-extractive memory delivery.

| Arm | K | Containment | Mean bytes |
|---|---:|---:|---:|
| HPPS | 1 | 74.86% | 189 |
| HPPS | 3 | **93.86%** | 525 |
| HPPS | 5 | **96.85%** | 724 |
| HPPS | 8 | 97.43% | 820 |
| real core BM25 | 5 | 96.60% | 718 |

Source validity and coverage are 100%. HPPS K5's +0.248 pp advantage over equal-K BM25 is statistically
significant but small: 37 HPPS-only vs 18 BM25-only successes, exact McNemar p=0.01445, paired bootstrap
95% CI [+0.065,+0.431] pp. The claim is a small consistent superation, not a large practical win.
Prediction artifact SHA-256: `5b44cee03a7d0fcec1cb4ff8f62633be9af2839d215ef46f7e38b35c83aa6308`;
score file SHA-256: `07747dde8ccd61dbc3047b010814696544e12e7bdbbd9a1019d66164168e5d7b`.

### Native Brazilian-Portuguese confirmation

FaQuAD is natively authored in Brazilian Portuguese and requires each annotated answer to be a source
substring. Its small official dev split has 63 questions, all with valid literals and offsets.

| Arm | K | Containment | Mean bytes |
|---|---:|---:|---:|
| HPPS | 1 | 69.84% | 272 |
| HPPS | 3 | **98.41%** | 673 |
| HPPS | 5 | **100%** | 851 |
| HPPS | 8 | **100%** | 926 |
| real core BM25 | 5 | **100%** | 848 |

Source validity is 100%. This confirms native PT-BR transfer but not superiority: HPPS and BM25 tie at
K=5 (McNemar p=1), and n=63 is a small confirmation set. Prediction SHA-256:
`cbe7febef503667e9fb8b15c48c2ac8cca3330ef9f394b4a91375004f8a3fc5e`; score file SHA-256:
`9a8504fff56865ce4f80404793c62ef53713ba7b37ff91909774b51172d4e706`.

## What is not yet solved

### Lightweight deterministic grammar bridge (lab-only)

The historical D39/D45 plan was revisited with Link Grammar 5.13 as a much smaller syntax front end
than ERG. The pinned Ubuntu packages total 720,950 bytes and extract to 2,717,427 bytes. A C-library
bridge in the single living `lab/proof_convergent_executor.py` preserves every returned word span and
typed link, retains parse alternatives separately, disables spell guessing and places hard limits on
time, memory, null links and analyses.

On the already-consumed LongMemEval holdout-v3 questions (ordinals 400–499), streamed rather than
materialized, 100/100 produced a bounded parse, 82/100 had at least one zero-unused-word parse and all
returned spans reopened exactly. Runtime was 2.43 s (24.33 ms/question) with 106,244 KiB peak RSS and
zero parser resource exhaustion. However, 77/100 searches exceeded the 64-analysis post-processing cap;
the raw linkage count had median 528 and maximum 214,639,682. Raising the cap to 256 still left 61/100
truncated. These forests are therefore **not** complete enough to authorize answers directly, and
explicit enumeration is not a viable edge design.

A generated 100-pair active/passive suite achieved 100/100 role-equivalent projections, 100/100
role-swap contrast separation and 100% span conservation at 71,716 KiB peak RSS. The corresponding
active/passive signatures also remained equal after actual D45 transport in 100/100 pairs; all 200
graphs emitted four Sigma-PBA facts and every fact reopened under the D45 compiler rule (73,864 KiB
peak RSS). The naive direct-answer idea failed: even with an oracle selecting the gold-containing
assistant sentence,
a generic graph cut made the literal answer reachable in only 8/33 cases. It is rejected.

One typed law does survive the negative: an owned entity with several modifier edges can answer an
attribute query by exact set difference when owner, entity and the query's known modifiers agree and
exactly one source modifier remains in every applicable complete parse. It recovers the consumed
Plesiosaur example as the exact span `blue`. The first policy reached 80/100 because it incorrectly
treated a parse environment that could not close the requested attribute as a contradictory complete
world. Reusing the existing D40 law fixes the distinction: zero-candidate environments emit no proof;
two-or-more-candidate environments block; every one-candidate proof environment must agree. The same
generated suite then reaches 100/100, while wrong-owner and extra-unknown controls abstain in 100/100.
This is a genuine generated mechanism result, but it still does not satisfy independent promotion.

The same closure law was generalized to binary relations. On a generated 100-case suite, forward
object, passive object and reverse-subject demands each resolve 100/100; wrong-known-argument controls
abstain 100/100. Transport through the actual D45 registry and Sigma-PBA preserves the same result, and
the exact-span proof envelope averages 93.4 bytes (maximum 94). This establishes compact typed
composition once the event exists. It does **not** establish that open text reliably creates that
event.

Two tempting conversation shortcuts were also rejected. Direct binary transport over 33 real
oracle-selected assistant-tail cases resolved 0/33: the required relation commonly belongs to a causal
turn pair or list structure rather than one reply sentence. A unique-successor shortcut emitted only
one answer over 56 cases with 37 unique successors, and that answer was factually wrong (`Okay.` rather
than `Absinthe`); the shortcut was removed. These negatives localize the missing layer to complete
semantic/event compilation, not another assistant-tail heuristic.

The surviving role is deliberately narrower: Link Grammar may witness compact grammar symbols and
subject/object transport for the pre-existing D45 semantic hypergraph, while HSSD/Sigma-PBA remains the
only binding authority. Truncated or disagreeing alternatives abstain. No core promotion or accuracy
claim follows from this diagnostic. Immutable diagnostic:
`lab/results/link-grammar-d45-bridge-diagnostic-v0.json`.

Further rule iteration is paused behind the single living
`lab/UNIVERSAL_DETERMINISTIC_COMPILATION_PROGRAM.md`. The plan combines a packed Link Grammar connector
circuit, audited RelEx-style dependency rewrites, VerbNet-style semantic frames, H-DCA/Constraint
Grammar monotone elimination, D45 and Sigma-PBA. It also formalizes an owner-proposed Transformer
reduction: replace learned Q/K/V and softmax with HSSD obligations, D45 typed keys, exact-span values and
provenance-semiring union/join. The first experiment must prove exact equivalence to exhaustive worlds
and Sigma-PBA; no benchmark tuning or core work is authorized before that gate.

The first, deliberately small differential now passes: Proof Attention and Sigma-PBA return identical
state, values and provenance monomials for handcrafted three-hop alternatives/conflicts/nogoods and for
100/100 seeded finite proof fields with shuffled facts and retracted environments. This validates the
algebraic reinterpretation only. No lower runtime/bytes, packed-parser completeness or open-language
gain has been shown, so a second production engine and core promotion remain unjustified.

The first packed-syntax gate has now passed in the lab. A version-pinned patch exposes an existential
projection call over Link Grammar 5.13's existing MiniSAT encoding instead of enumerating linkages. The
classic parser at cap 256 was the complete oracle; the SAT arm was allowed to materialize only one
linkage. Across 400 generated active, passive, PP-attachment-ambiguous and embedded-clause sentences,
all **1,000/1,000** positive/negative projections agreed, with zero false-possible, zero
false-impossible and zero oracle truncation. The oracle had at most 30 linkages; 100/100 requirements
that occurred after the first SAT model were still found, and both competing PP attachments survived in
200/200 candidate queries.

The frozen v1 arm then removed scorer-written connectors from a finite binary semantic path. The
question parser compiled `who`/known participant into ARG1/ARG2; active/passive dependency rules emitted
SAT constraints; certificates reopened every tested candidate; and the unique value traversed D45,
Sigma-PBA and compact exact-span serialization. Result: **800/800** generated queries correct — 400/400
positives resolved and 400/400 wrong-known controls abstained. Compact answers were 97 bytes with zero
reopen failure. Combined runtime was 6.90 s and peak RSS 88,872 KiB. Frozen artifact SHA-256:
`dd32f499b608efc7e1cab0a75755de95b623df9fc1ec372c7568709f7e0ccf2c`.

This proves compact query-conditioned **syntactic existence** and one finite binary semantic family, not
open-language understanding. The direct syntax arm supplied indices; the semantic arm generated them
mechanically but covers only capitalized single-token participants and active/passive `who` relations.
Broader audited frames, independent templates, SAT null links and an interruptible solver timeout
were never added, and this specific gap is no longer being pursued rather than pending: the pack
that was later actually promoted to core (EN, then PT below) bypasses Link Grammar entirely and
uses the H-DEM/H-DCA/H-PLT line instead, confirmed to score the same or better without it (see the
promoted EN pack's own "Link Grammar/SAT ablation contributes no quality on GUM and slightly hurts
EWT" finding further down). Patch and runner, kept as a historical diagnostic, not a pending task:
`lab/link_grammar_sat_projection_5_13.patch` and
`lab/runners/run_sat_projection_differential.py`. Nothing from this specific line is eligible for
core promotion.

V2 added possession through `have` and a locative `where` goal lowered from certified `MVp+J`
prepositional structure. The frozen generated result is **1,200/1,200**: 600 positives resolved, 600
wrong-known controls abstained, compact mean 96.83 bytes/max 97, 8.08 s and 89,408 KiB peak RSS.
Artifact SHA-256: `adc11ff7bed15ff0f71825399824ddfaf769a993bc9f75a34424aea3aa44b17c`.

The first external transfer diagnosis rejected broad generalization. On 94 subject/object probes
synthesized from official RelEx `TestRelEx.java` gold after v2 froze, only **25/94 = 26.60%** resolved
exactly. Selective precision remained 25/25 = 100%; no resolved answer was wrong and 69 abstained. This
is consumed external development, not natural QA or confirmation, because gold chose the relation and
generated the probe. Artifact SHA-256:
`3aeb1b07bab5781313783e9ef6fd3843517886c8efe478ef14973718250ea8c5`.

The next development basis is Universal Dependencies. Only EN-EWT, PT-Bosque and ZH-GSD **dev** splits
were acquired and checksummed; their external test splits remain unopened. UD provides a shared
`nsubj/obj/iobj/obl/nmod:poss` contract, while every language retains a separate deterministic pack and
must clear the 90% gate independently. This is a methodology change, not a quality result.

The first EN-EWT dev audit now exposes its full denominator. In 2,001 sentences there are 663 heads
with both `nsubj` and `obj`; the deliberately atomic 12-token probe admits only 51 (**7.69%**). On its
102 positive role questions it resolves 18 (**17.65%**) with 18/18 selective precision, while all
102 absent-entity controls abstain. Complete accuracy is 58.82%, but that number is dominated by easy
negative abstentions and is not the target metric. Expanding the token budget to 20/30 increases the
eligible fraction only to 11.61%/14.18%, lowers positive accuracy to 14.29%/11.70%, and at 30 tokens
introduces one wrong resolved answer. The bottleneck is therefore open-text construction and packed
ambiguity, not Sigma execution. Dev artifact SHA-256:
`8a1faefa1e1b2bba68c9a8cb93b05d0cb69a9af83a76ddc7f137b1c2e181f14d`.

The living theory now names the candidate architecture **H-PLT (Horizon Proof-Lattice
Transformer)**. It keeps the Transformer's query/key/value dataflow but replaces embeddings and
softmax with a finite span/reading lattice, hard typed compatibility, provenance-semiring union/join
and least-fixed-point propagation. It returns only answers invariant over every complete surviving
world. This may remove the need for one perfect parse before execution, but remains an unvalidated
architecture: candidate reachability, packed-equivalence, per-language >=90% and edge cost are its
gates. It is not a new core engine yet.

The first H-PLT correctness adapter is now executable through the existing H-DEM → guarded facts →
Proof Attention/Sigma path. Packed and exhaustive interpretation semantics match **100/100** generated
lattices, including answer state and complete provenance: 38 consensus-resolved, 62 contested, zero
semantic divergence. Mean packed states 3.30 versus 3.39 explicit assignments is only an equivalence
result, not a compression win; no runtime/core promotion follows. Combined v3 artifact SHA-256:
`66ea415ef5b2673ef6ae9b93f171acd10dc6088fb8f07587e40e5811c34ee4bb`.

V4 adds symbolic certain-answer execution by searching for counterexamples instead of enumerating
worlds. It remains exact against packed and explicit execution on 100/100 seeded lattices. Small cases
favor enumeration (6.09 symbolic states versus 3.39 assignments), but a 25-variable binary stress has
33,554,432 implicit worlds: symbolic H-PLT resolves the invariant answer in 77 states while enumeration
abstains at its 100-assignment budget. This is a real compression regime, still over structured
candidates rather than open text. Artifact SHA-256:
`5d681d08aec83e950ae12a86d07877806e01d55a92ad7ff7dd7125b59e25f99b`.

### EN atomic relation pack v1 — frozen transfer result

After development, the EN surface/query heads were frozen at compiler SHA-256
`f200514a32ad4e4d84d9f91573ec8ce51b81127da44721716a8182f31db93ccf` and scorer SHA-256
`64d6bfe71f46994c957ab4231aeecde64ea74d25913ba95e8e352e39a56e05c7` before the official
UD English-EWT test split was downloaded. Dev reached **96/102 = 94.12%** positive accuracy,
96/96 selective precision and 102/102 negative abstention on the atomic <=12-token family.

The one-shot test **failed promotion**: **67/78 = 85.90%** positives, 67 correct among 73 emitted
(91.78% selective precision), and 78/78 negative abstention. Complete 92.95% is not the gate because
half the rows are easy negative controls. The test is now consumed and cannot confirm a retuned v2.
Test data SHA-256 `fa024f43dc5da3c5ac02563bc9bd0e974f46cbb1560823976a8f342a37dc494a`;
result artifact SHA-256 `b1c1f275172d82236b05b4a24fcff83dca4935ca3c2d8a8c584236952bd0f823`.
Nothing promotes to core from v1.

### EN atomic relation pack v2 — promoted in bounded scope

V2 was developed only on EWT dev/train. The deterministic surface-only arm reached **98/102 =
96.08%** positives on dev and **419/444 = 94.37%** on train, with 98.00%/97.22% selective precision
and 100% negative abstention. It was then frozen and evaluated once on the independent, untouched
UD English-GUM test split at commit `1fe635509c649e376dfb449d528424ab78f4eaee` (data SHA-256
`cd96a285e7339f401f4803dd0f4f61109c51692e3ef6f9186a1f977544080b69`). Result: **77/82 =
93.90% positive accuracy**, **77/79 = 97.47% selective precision**, and **82/82 negative
abstention**. The preregistered v2 gate passes.

The Link Grammar/SAT ablation contributes no quality on GUM and slightly hurts EWT. The promoted core
therefore contains only the small surface reader plus the exact 38,033-byte Princeton WordNet 3.0
`verb.exc` resource (SHA-256 `dbbcf9a...b891b42c`). Core parity reproduces all three surface-only
metrics exactly; every resolved row compacts to **140 bytes** and reopens with zero failures. The wheel
contains code, resource and upstream license; 1,082 core tests pass.

Claim boundary: the probe uses gold UD dependencies to choose an atomic relation and synthesize a
question. Only 41/684 candidate GUM relations (5.99%) enter the <=12-token, single-token-operand
family. This is a real >=90% result and a legitimate opt-in core promotion for that family, but it is
not natural QA, all English relations, semantic truth assertion or universal language coverage.

### PT atomic relation pack — early raw-token adapter rejected; H-FMRL/H-DEM/H-PLT bridge now in core, opt-in, holdout confirmation failed narrowly

The same language-neutral surface kernel was first fitted with a development-only Portuguese
adapter built on a growing raw-token `skip` set. Bosque dev improved from **33/46 = 71.74%** to
**46/46 = 100%** positives, with 46/46 negative abstentions. That development number did not
transfer:

- Bosque official test: 23/26 = **88.46%**;
- Portuguese-GSD official test after general diagnostic changes: 17/22 = **77.27%**;
- Portuguese-PUD official test after another freeze: 10/12 = **83.33%**.

All corresponding absent-entity controls abstained, but selective precision equaled the failing
positive accuracy because the adapter confidently chose the wrong surface role. All three test sets,
plus Porttinari, DANTEStocks, PetroGold and CINTIL-test opened in later sessions, are consumed and may
not be used as a virgin confirmation after further changes.

The raw-token `skip` design was abandoned, not patched further. Its replacement is a genuine typed
constraint-satisfaction resolver — H-FMRL (finite morphosyntactic reading lattice) supplying typed
per-token alternatives, H-DEM/H-DCA (packed-domain arc consistency plus an exhaustive correctness
oracle) turning PP-governance/clause-local competition into an explicit CSP, and H-PLT (proof lattice
attention) resolving only when every complete interpretation world agrees — the same architecture
that underlies the promoted EN pack's own proof discipline, applied to Portuguese's richer closed-class
morphology instead of raw-token skip lists. Verified on CINTIL-dev (a repeatedly-reused development
corpus, not a holdout): **428/456 = 93.86%** with an optional real PortiLexicon-UD lexicon (MIT-licensed,
rebuilt locally from the official per-POS TSVs, never shipped in the package) recovering a small,
screened set of irregular/ambiguous verbs the closed-class rules alone cannot reach. A 196-scenario
informal/slang battery (built from real generated conversational Portuguese, not UD treebank prose)
moved from 84.69% to **194/196 = 98.98%** across eighteen independently-verified, zero-regression
fixes; the two remaining cases are genuine multi-way coordinated-object questions with no single
correct atomic span — confirmed by CINTIL-dev's own gold-relation methodology, which excludes the
identical "conj"-dependent shape from its own eligible set for the same structural reason, not a
mechanism failure.

**This is now real, reachable core code, exported from the stable top-level `horizon_memory`
namespace** — `RoleReadResult`, `read_pt_atomic_relation`, `resolve_pt_surface_role`,
`OpenTextAtomicRelationResultPT` and `OpenTextHorizonMemory.answer_atomic_relation_pt` are all
importable directly as `from horizon_memory import ...`, not gated behind the experimental
`horizon_memory.research` namespace (which still re-exports the same objects, unchanged, for
backward compatibility). The underlying module is `horizon_memory.portuguese_atomic_relations`
(`read`, `resolve_surface_role`) plus its own dependency chain (`hdem_hdca_kernel.py`,
`proof_lattice_attention.py`, `sigma_pba.py`, `finite_morphology_lattice.py`,
`portilexicon_compact.py`).

**This promotion to the stable namespace was an explicit product decision by the project owner
(2026-08-22), not a claim that the PT pack cleared the same evidentiary bar as the EN pack.** The
EN pack cleared a genuinely fresh, never-touched holdout (UD English-GUM, 78/82 = 95.12%
positive, 97.50% selective, 100% negative abstention) before being wired into the stable
namespace. The PT bridge has instead: (a) been measured repeatedly against corpora this project
has already read and iterated against (CINTIL-dev, the 196-scenario battery — both consumed
development data, not holdouts); and (b) had exactly one fresh, genuinely untouched holdout
opened against it — the official `UD_Portuguese-CINTIL` test split — which **failed** the
promotion bar by a narrow margin: 92.39% positive accuracy (clears >=90%), **94.44% selective
precision (misses the >=95% bar by 0.56pp)**, 100% negative abstention (clears the bar). Ten of
the fourteen positive misses on that holdout were resolved-but-wrong, not honest abstentions: a
per-sentence diagnosis found the same small family of shapes across most of them, not ten
unrelated failures: a noun embedded inside a genitive/oblique prepositional phrase, a clitic
pronoun, or an adjacent determiner/adverb winning the role over the true head. No later
development-only fix
may be cited as having closed this specific gap, since CINTIL-test is consumed and cannot be
retuned or rescored. A new, independent PT holdout clearing >=90% positive accuracy, >=95%
selective precision, 100% negative abstention and a separately reported eligible denominator
would be required to claim the same standing as the EN pack; until then, treat the PT pack as
**reachable, tested, and useful, but not holdout-confirmed** — the opposite failure mode from
before (previously correctly gated behind an experimental namespace; now correctly documented as
stable-but-unconfirmed rather than silently implying confirmation by virtue of being at the top
level).

Horizon does not currently cover arbitrary words, relations or question forms.
The principal open problem is deterministic compilation of unrestricted text
into authorized causal fibers with both high coverage and very low false-accept
rate. Current public claims must therefore avoid:

- 99% accuracy on unrestricted natural language;
- universal superiority over BM25;
- treating retrieval recall as answer accuracy;
- counting abstention on a positive question as a correct answer;
- extrapolating narrow structured results to every domain.

The long-term research target remains near-99% end-to-end correctness without
requiring an LLM or hosted API, while publishing precision, coverage,
abstention, provenance failures and compute cost separately.

A related distinction now matters: the authorized typed sidecar has explicit, attested
`supersedes` lineage and rejects incomplete updates, while untyped free-text evidence still needs the
opt-in research mechanism `horizon_memory.research.collapse_evidence_items`. The sidecar result must
not be projected onto the untyped evidence path.

## Real-world `HorizonAnswerEngine` validation: five live corpora, 136 hand-verified questions

Distinct from every result above (all measured against public research benchmarks with published
gold answers), this validates the actual shipped `HorizonAnswerEngine` (the same code path
`api/server.py` and `api/mcp_server.py` expose) against real conversational data the project
had never seen before, queried live from a running MongoDB instance. Ground truth for every
question was verified directly against the literal database text, never against the tester's own
paraphrase of it, after two early false negatives (a number written differently, a missing accent)
made that discipline necessary.

**What this found.** `DEFAULT_PROFILE` (tuned and judge-validated for the large-corpus MemGym-DR/
LongMemEval benchmarks above) reliably located the right source document on a small, personal-
scale corpus, but frequently dropped the one sentence carrying the concrete answer (a number, a
name, a specific detail) in favor of a shorter, less specific competing sentence. Root-caused to
`EngineProfile.answer_shortlist_size=50`: an engineering safety cap with no benchmark evidence
behind it (unlike `answer_relevance_gate_ratio`, which a 2026-08-19 MemGym-DR sweep had already
validated), too tight for a corpus with no real dilution risk to guard against.

| Corpus (language, register) | Questions | `DEFAULT_PROFILE` | `TEAM_MEMORY_PROFILE` | `PERSONAL_MEMORY_PROFILE` |
|---|---:|---:|---:|---:|
| PT-BR casual chat/slang (52 conversations) | 32 | 17/32 | 23/32 | **31/32** |
| PT-BR formal technical Q&A, round 1 (typo-laden CS/physics/biology) | 20 | 15/20 | 17/20 | **19/20** |
| PT-BR formal technical Q&A, round 2 (harder/ambiguous) | 12 | 12/12 | n/a | 12/12 |
| PT-BR formal technical Q&A, round 3 (noisier still) | 12 | 12/12 | n/a | 12/12 |
| EN Gen-Z slang/memes (50 conversations, incl. cross-lingual PT queries about EN content) | 30 | 20/30 | 24/30 | **29/30** |
| EN Gen-Z slang, multi-hop/evolving memory (+27 chained conversations, 127 total) | 20 | 15/20 | 18/20 | **20/20** |
| **Total** | **136** | **91/136** | *(not run on every corpus)* | **123/136** |

Zero false answers and zero wrong-conversation hallucinations were introduced by either loosened
preset across all 136 questions at every setting tested — every miss at every setting was a
dropped detail (or, at `DEFAULT_PROFILE` only, one lost cross-conversation competition), never a
fabricated fact. Full narrative and exact per-question categorization (PASS / partial-hop /
right-source-missing-detail / wrong-source) live in this project's own session history; the
summary numbers above are what's citable here.

**Multi-hop composition, not designed for, worked anyway.** The last row above is the first test
in this validation requiring a single answer to combine facts from two or three *different*
source conversations (e.g. a puppy's name from one conversation, what it later chewed from
another). `HorizonAnswerEngine` has no dedicated multi-hop/graph-traversal stage — it builds one
flat pool of routed, verified claims and renders whichever survive the shortlist/relevance-gate/
budget side by side. That turned out to be sufficient: two or three claims from different source
conversations only need to each individually clear routing and verification to end up rendered
together in the same answer. The only failure mode was the same shortlist/budget competition
already diagnosed above, not a missing architectural capability: `PERSONAL_MEMORY_PROFILE`
resolved it to a clean 20/20.

**Why this isn't promoted to a new default, and why no single automatic detector replaces
picking a preset.** The loosened settings were also re-swept against the full 120-question
MemGym-DR benchmark and showed no regression on a token-overlap coverage proxy, but that
specific metric is already documented above (Composer judge-scored pilot) as capable of rewarding
a larger, less-precise evidence dump, so this is not read as a safety proof at that scale.
Calibration also found corpus size does not reliably separate "safe to loosen" from "needs the
tight defaults": a real technical-QA corpus's own candidate-pool size measured statistically
indistinguishable from a real MemGym-DR episode. Both findings are why three separate named
presets ship (`DEFAULT_PROFILE` / `TEAM_MEMORY_PROFILE` / `PERSONAL_MEMORY_PROFILE`, see
[Architecture](ARCHITECTURE.md#answer-engine)) rather than one adaptively-tuned default:
an operator picks the preset matching their own deployment's real scale.
