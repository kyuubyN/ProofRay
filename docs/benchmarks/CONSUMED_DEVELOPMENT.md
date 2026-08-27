# Consumed-development memory benchmarks

These measurements informed engineering choices in the current repository.
Their datasets or rows have already been inspected, calibrated on or reopened;
they must not be presented as independent product accuracy.

## LoCoMo: personal-recall reachability

The current high-recall profile returns at least one annotated turn for
**1,799/1,982 (90.77%)** evidence-annotated questions, all annotated turns for
**1,611/1,982 (81.28%)**, and **2,188/2,821 (77.56%)** annotated turn IDs at
its measured 64-candidate cut (mean 44.49 candidates).

This is **candidate reachability**, not generated-answer correctness. Four
questions have no evidence annotation and three annotated IDs are absent from
the runtime conversation, giving raw physical ceilings of 1,979/1,982 all-hit
and 2,818/2,821 turn recall. The profile stays deployment opt-in pending an
independently manifested personal cohort.

The 32-candidate historical cascade measured 1,770/1,982 hit, 1,580/1,982
all-hit and 2,084/2,821 annotated-turn recall. It is retained only as a
comparison point; it is not the release profile.

## LongMemEval: reader/composer experiments

A frozen 120-output consumed-development paired judge run used
`gemini-3.1-flash-lite` only as evaluation infrastructure:

| Frozen arm | Mean judge result | Meaning |
|---|---:|---|
| Plain composer | 0.7750 | Comparator for that historical configuration |
| Proof-first composer | 0.9375 | Same-run experimental arm |

The 0.7750 figure is therefore neither a current Horizon/ProofRay score nor a
model-agnostic baseline. It is especially unsafe to quote alone: it predates
later certificate/rendering repairs and says nothing about independent
generalization. The paired run had 21 improvements, three regressions and 96
ties; no model, scorer, gold answer or network participated in runtime proof
acquisition.

Post-calibration, previously unopened 80- and 100-episode slices did **not**
transfer end-to-end. Those results invalidate any universal answer-accuracy
claim based on the development numbers. They are preserved in the
[archive](HISTORICAL_RESEARCH_ARCHIVE.md#untouched-post-calibration-holdout-v3-119120-did-not-transfer-end-to-end).

## MemGym: proof closure, not judge score

The Explanatory Obligation Proof (EOP) runtime-only consumed-development replay
closed **6/120** proof dossiers, with 86 unsupported, 16 contested and 12
abstaining. It is an opt-in contextual resolver. The result measures
reopenable proof coverage, not answer correctness and not retrieval quality.

The remaining gate is multi-claim `COMPARE`/`QUANTIFY`/`EXPLAIN` composition.
No MemGym score is advertised as a product claim until a sealed protocol
evaluates the complete answer path without changing the frozen output. The
paired judge-scored comparison below is additional consumed-development
engineering evidence for that same still-open composition gap, not a release
claim, and does not change that policy.

### MemGym consumer-endpoint comparison: Horizon, polish (local and cloud), traditional RAG

First 120 episodes of the raw `lab/datasets/raw/memgym-dr-4hop-development.jsonl`
4-hop set (already inspected in earlier development). Documents and one
`AnswerContextIntent` per hop are built directly from each episode's own
`turns[].documents[]`/`turns[].sub_query` structure (zero-oracle: turn
membership only, never `is_supporting` or the gold answer), replacing an
earlier pass that built documents by splitting a concatenated context string
on blank lines, which measurably diluted both Horizon's own routing and the
BM25 baseline's retrieval. Judged by `gemini-3.5-flash-lite`, one of the
independently promoted backends, executing the official MemGym judge prompt.
`DEFAULT_PROFILE` is used as-is: its `claim_limit` was raised from 800 to
8,192 as part of this same pilot (see below) and is now the shipped default,
not a benchmark-only override.

Four arms, same 120 episodes, same judge: (A) the deterministic
route→verify→compose evidence pipeline alone, unpolished; (B) that same
evidence rewritten for fluency by Qwen3-1.7B, quantized to 8-bit precision,
running entirely on local consumer hardware via llama.cpp (well under 4 GB
of memory); (C) a traditional RAG baseline with no Horizon component at all,
BM25 (top-10) feeding `gemini-3.1-flash-lite` directly, reading the same raw
per-episode document set as A/B; (D) the same deterministic evidence as A,
polished by `gemini-3.1-flash-lite` (a full cloud-hosted model) instead of
the local model, isolating whether degradation from polish is inherent to
the *polish step* or specific to the *small local model*.

| Arm | N | Mean | Median |
|---|---:|---:|---:|
| (A) Horizon, unpolished | 120/120 | 0.7975 | 1.00 |
| (D) Horizon + cloud polish (gemini-3.1-flash-lite) | 120/120 | 0.6125 | 0.70 |
| (C) Traditional RAG (BM25 + gemini-3.1-flash-lite, no Horizon) | 120/120 | 0.5583 | 0.55 |
| (B) Horizon + local polish (Qwen3-1.7B, 8-bit) | 120/120 | 0.4975 | 0.50 |

Every episode that failed to produce a parseable judge score (a small,
recurring parser limitation, not an answer-quality signal: the judge's own
free-text explanation occasionally contains raw LaTeX backslash sequences
that break strict JSON parsing) was re-judged against the identical
predicted answer, or had its score recovered directly from the judge's raw
response when re-judging itself failed to parse a second time; no predicted
answer was regenerated or altered to obtain a score.

Two findings, read together, correct an earlier, retracted version of this
comparison that used the diluted paragraph-split documents:

- **Polish measurably costs precision, and the cost scales with the
  polishing model's own capability, not with Horizon's evidence.** The
  identical evidence loses 0.19 points when polished by a full cloud model
  (A→D) and 0.30 points when polished by the small local model (A→B). A
  cloud judge call reading one of the degraded local-model answers
  attributed the loss directly to hallucinated technical detail added
  during rewriting, not to a change in the underlying facts.
- **Even after that cost, Horizon plus polish still beats traditional RAG
  using the identical polishing model.** Arm D (0.6125) exceeds arm C
  (0.5583) although both use `gemini-3.1-flash-lite` for the final text;
  the only difference is whether that model rewrites already-verified
  Horizon evidence or has to retrieve and reason over the raw corpus
  itself. Arm B (0.4975) falls short of arm C, so this specific comparison
  is model-dependent: a strong enough polishing model turns Horizon's
  evidence into an advantage even after paying the polish tax, but a very
  small one does not, on this benchmark, fully offset the tax.

Neither finding licenses a general "small-model amplification" claim from
this pilot alone: arm B specifically tests a *rewrite-for-fluency* task, not
a small model *reading Horizon's evidence and answering from scratch*,
which is the more direct test of that hypothesis and has not been run here.
This remains consumed-development evidence, not an independent holdout
result: the corpus was already used in this project's own development, and
the configuration was reached after correcting two real problems found
while running it (an initially selected Groq judge backend returned a
persistent "model not found" error and was replaced before any comparison
was drawn; the profile's `claim_limit` was raised only after a
candidate-pool truncation was found), not frozen before any result was
observed.

## Reproduction and history

Artifact hashes, denominator definitions, former snapshots and negative
results remain in the [historical archive](HISTORICAL_RESEARCH_ARCHIVE.md).
The [main benchmark page](../BENCHMARKS.md) defines which classes of evidence
may support a public claim.
