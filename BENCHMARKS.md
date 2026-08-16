# Benchmarks and claim boundaries

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

**What is currently under active test, not yet resolved:** whether the
remaining gap above 0.72 is better explained by evidence still being
incomplete at this budget, or by the reader's difficulty composing evidence
it already has into one correct answer. An oracle-style ceiling test (a
reader handed the literal correct answer directly) scored above 0.97,
confirming the reading contract itself is not the bottleneck. Distinguishing
"more of the right evidence would help" from "the reader needs help
composing what it already has" is the current open experiment.

## What is not yet solved

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
