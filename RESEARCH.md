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
measurement has already been corrected twice: a real bug (a sentence's own
leading capitalized word could be mistaken for a value, causing a false
exclusion), a real gap (Chinese text, having no letter-casing, never
activated the mechanism at all), and then a real over-collapse risk once
Chinese did activate (a character-bigram anchor signal saturates on
combinatorial noise in a small candidate pool -- nearly every claim ends up
with an anchor that looks "locally unique" purely by chance, not because it
means anything). All three are fixed; the third by replacing character
bigrams with maximum-matching word segmentation against a small, self-built
frequency dictionary (no external NLP dependency). Re-measuring after each
fix changed the honest picture each time: no language/budget/noise
combination currently clears the pre-registered acceptance rule (an earlier
apparent Portuguese pass did not survive the anchor-bug fix), Chinese now
activates and resolves safely where it previously either never fired or
over-collapsed, and the false-positive exclusion rate on input without an
actual value revision moved from ~9% (bigram-blind Chinese) to ~11%
(bigram-anchored Chinese, more false positives) to **~7%** (word-segmented
Chinese, the current state) across the same 1,290-pair check. It is never
called by any default routing or ranking path for exactly that reason. These
classes are research surfaces: signatures may change, negative results
remain part of the record, and no benchmark result should be generalized
beyond its frozen protocol.

The public package contains executable implementations and mechanical tests.
It intentionally does not contain private theory notebooks, unpublished
derivations, benchmark datasets, answer keys, development logs or papers.

The stable `horizon_memory` namespace contains the durable memory, typed causal
program, evidence, proof, HSSD and integration contracts needed by ordinary
users. Importing the research namespace is never required to store or retrieve
explicit facts.
