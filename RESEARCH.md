# Research module

Experimental retrieval is deliberately separated from the stable storage API.
Applications must opt in through:

```python
from horizon_memory.research import HorizonSearchEngine
```

The namespace exposes proof-pressure, materialized and feedback-transport
retrieval variants, and `collapse_evidence_items`, an opt-in mechanism that
excludes superseded restatements of a value (e.g. a revised date) from an
already-verified evidence pool. It is measured, not assumed: on real
multilingual test data it passed a pre-registered acceptance rule only for
clean-text Portuguese, failed under noise and in English, never activated on
Chinese text, and showed a measured false-positive exclusion rate near 10% on
input without an actual value revision. It is never called by any default
routing or ranking path for exactly that reason. These classes are research
surfaces: signatures may change, negative results remain part of the record,
and no benchmark result should be generalized beyond its frozen protocol.

The public package contains executable implementations and mechanical tests.
It intentionally does not contain private theory notebooks, unpublished
derivations, benchmark datasets, answer keys, development logs or papers.

The stable `horizon_memory` namespace contains the durable memory, typed causal
program, evidence, proof, HSSD and integration contracts needed by ordinary
users. Importing the research namespace is never required to store or retrieve
explicit facts.
