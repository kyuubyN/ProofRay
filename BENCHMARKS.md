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
