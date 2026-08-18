# Research module

Experimental retrieval is deliberately separated from the stable storage API.
Applications must opt in through:

```python
from horizon_memory.research import HorizonSearchEngine
```

The namespace exposes proof-pressure, materialized and feedback-transport
retrieval variants, and `collapse_evidence_items`, an opt-in mechanism that
excludes superseded restatements of a value (e.g. a revised date) from an
already-verified evidence pool. It is measured, not assumed, and the measurement
has already been corrected once: an earlier pass found a real bug (a sentence's
own leading capitalized word could be mistaken for a value, causing a false
exclusion) and a real gap (Chinese text, having no letter-casing, never
activated the mechanism at all). Both are fixed. Re-measuring after the fix
changed the honest picture: no language/budget/noise combination currently
clears the pre-registered acceptance rule (the earlier apparent Portuguese
pass did not survive the bug fix), Chinese now activates and resolves cleanly
where it previously never fired, and the false-positive exclusion rate on
input without an actual value revision is now ~11% (up slightly, since
Chinese input can be false-positived too where it was previously immune by
being blind). It is never called by any default routing or ranking path for
exactly that reason. These classes are research surfaces: signatures may
change, negative results remain part of the record, and no benchmark result
should be generalized beyond its frozen protocol.

The public package contains executable implementations and mechanical tests.
It intentionally does not contain private theory notebooks, unpublished
derivations, benchmark datasets, answer keys, development logs or papers.

The stable `horizon_memory` namespace contains the durable memory, typed causal
program, evidence, proof, HSSD and integration contracts needed by ordinary
users. Importing the research namespace is never required to store or retrieve
explicit facts.
