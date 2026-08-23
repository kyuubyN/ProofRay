# Research module

Experimental retrieval is deliberately separated from the stable storage API.
Applications must opt in through:

```python
from horizon_memory.research import HorizonSearchEngine
```

The namespace exposes proof-pressure, materialized and feedback-transport
retrieval variants, and `collapse_evidence_items`, an opt-in mechanism that
excludes superseded restatements of a value (e.g. a revised date) from an
already-verified evidence pool. It is measured, not assumed, and the
measurement has already been corrected several times: a real bug (a
sentence's own leading capitalized word could be mistaken for a value,
causing a false exclusion), a real gap (Chinese text, having no
letter-casing, never activated the mechanism at all), a real over-collapse
risk once Chinese did activate (a character-bigram anchor signal saturates
on combinatorial noise in a small candidate pool), and most recently an
anchor-primary relevance rule for non-CJK text (a claim's own anchor, once
confirmed non-empty, is sufficient relevance on its own -- removing a
dependency on an English-only stopword list that was breaking Portuguese
detection under noise). The Chinese fix replaced character bigrams with
maximum-matching word segmentation against a small, self-built frequency
dictionary (no external NLP dependency); a same-day attempt to expand that
dictionary with a large general-purpose Chinese wordlist was tested and
reverted (it doubled the false-positive rate for zero measured gain). The
anchor-primary fix is a real, measured trade, not a clean win: Portuguese and
English now clear the pre-registered decision rule on some noise/budget
combinations for the first time, but the false-positive exclusion rate on
input without an actual value revision rose from **7.05% to 14.03%** across
the same 1,290-pair check -- kept deliberately, after the project owner
weighed both numbers directly. It is never called by any default routing or
ranking path for exactly that reason. These classes are research surfaces:
signatures may change, negative results remain part of the record, and no
benchmark result should be generalized beyond its frozen protocol.

`narrative_composition` composes an arbitrary collection of already-typed,
already-linked facts (e.g. from `TypedCausalExecutor.fibers`) into one
coherent, non-duplicating multi-sentence narrative -- causal, superseding,
temporal or plain listing, chosen only from signals the typed data model
already carries (a real causal edge, a shared fiber with differing content
and clock, a shared subject with differing event time), never a lexical
dictionary or a trained parser. Every clause is a literal slice of the
fact's own attested source span; joining is restricted to symmetric
concatenation (a closed connector word), never substitution into another
clause's structure, following Lebanoff et al. (2019)'s measured finding that
substitution-style joins are markedly less faithful. `current_value_fact`
identifies which fact in a revision chain states the current value, walking
from most recent to least recent and skipping a trailing confirmation
clause that carries no value of its own -- a real gap a 2026-08-22 oracle-
fiber probe against 48 real long-horizon scenarios found and this fix
closed for most of the affected cases. It solves entity/fiber linking for
nobody: a caller must already know which facts describe the same thing,
which remains an open, unsolved problem for raw unstructured text in this
project. It stays research-namespace-only for that reason -- no frozen
holdout has been opened for it yet.

The public package contains executable implementations and mechanical tests.
It intentionally does not contain private theory notebooks, unpublished
derivations, benchmark datasets, answer keys, development logs or papers.

The stable `horizon_memory` namespace contains the durable memory, typed causal
program, evidence, proof, HSSD and integration contracts needed by ordinary
users. Importing the research namespace is never required to store or retrieve
explicit facts.
