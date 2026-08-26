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
No MemGym score is advertised until a sealed protocol evaluates the complete
answer path without changing the frozen output.

## Reproduction and history

Artifact hashes, denominator definitions, former snapshots and negative
results remain in the [historical archive](HISTORICAL_RESEARCH_ARCHIVE.md).
The [main benchmark page](../BENCHMARKS.md) defines which classes of evidence
may support a public claim.
