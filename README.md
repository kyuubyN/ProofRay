<p align="center">
  <img src="assets/Horizon.jpeg" alt="Horizon Memory — orbital horizon symbol" width="420">
</p>

<h1 align="center">Horizon Memory</h1>

<p align="center"><em>Memory that can show why it remembers.</em></p>

Horizon Memory is a standalone, model-agnostic memory architecture for AI
systems. It stores durable state, retrieves evidence under explicit budgets,
preserves provenance, verifies proof-carrying results, and abstains when its
conditions are not satisfied. The core requires no LLM, hosted API, or network.

Created and founded by **Kaue Oliveira Costa (kyuubyN)**, Brazil<br>
[ORCID 0009-0009-8502-3220](https://orcid.org/0009-0009-8502-3220) ·
[kaue.o.costa@proton.me](mailto:kaue.o.costa@proton.me)

> Status: private alpha. Interfaces and research claims are being audited
> before the first public release.

Horizon originated inside the private **Q-HDRE research program**. Its
physics-inspired hypotheses were not treated as claims about nature: they were
translated into computational mechanisms, exposed to falsifiable tests, and
retained only when their useful invariants survived. Read the sanitized public
history in [Origin and design lineage](docs/ORIGIN_AND_DESIGN.md).

## Install

The public PyPI distribution will be named `horizon-memory` because `horizon`
is already used by OpenStack:

```bash
pip install horizon-memory
```

The Python import and command remain concise:

```python
import secrets

from horizon_memory import HorizonConfig, HorizonMemory

scope = 7
memory = HorizonMemory.create(HorizonConfig(
    root="./horizon-data",
    scope_id=scope,
    key=secrets.token_bytes(32),
))
memory.put(scope, fact_id=100, version=1, value=90)
result = memory.get(scope, fact_id=100)
print(result.value)
memory.close()
```

```bash
horizon --doctor
```

Values in the durable substrate are currently unsigned bytes (`0..255`). Rich
application content is referenced by fact identity, source and span. This limit
is intentional and will not be hidden behind a broader marketing claim.

## Surfaces

- `horizon_memory`: stable storage, evidence, typed causal execution and HSSD.
- `horizon_memory.adapters`: integration boundary, dual-licensed for adoption.
- `horizon_memory.research`: experimental retrieval engines; not stable API.

The repository intentionally excludes private theory notebooks, private
datasets, unpublished papers, benchmark answer keys and development logs.

## Documentation

- [Origin and design lineage](docs/ORIGIN_AND_DESIGN.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Benchmarks and claim boundaries](BENCHMARKS.md)
- [Research module](RESEARCH.md)
- [Licensing policy](LICENSE_POLICY.md)
- [Governance](GOVERNANCE.md)
- [Security policy](SECURITY.md)
- [Responsible use](RESPONSIBLE_USE.md)
- [Draft: OpenRAIL-R license under consideration](LICENSE_RAIL_DRAFT.md)

## Freedom and attribution

The engine is free software under **AGPL-3.0-or-later**. If a modified version
is offered to users over a network, those users must be offered its complete
corresponding source as required by the AGPL. Integration adapters carry an
Apache-2.0-or-AGPL dual-license to make connection to independent systems easy.

You may charge for hosting, support or integration. You may not remove license,
copyright or attribution notices, and the Horizon Memory name may not be used
to imply that an unofficial derivative is the official project.

See [LICENSE_POLICY.md](LICENSE_POLICY.md), [TRADEMARKS.md](TRADEMARKS.md),
[GOVERNANCE.md](GOVERNANCE.md) and [AI_TRAINING_POLICY.md](AI_TRAINING_POLICY.md).

## Responsibility

Horizon Memory is general-purpose infrastructure. Operators are responsible
for the data they ingest, decisions they automate, legal compliance, security,
human review and consequences of deployment. See [DISCLAIMER.md](DISCLAIMER.md),
[SECURITY.md](SECURITY.md) and [RESPONSIBLE_USE.md](RESPONSIBLE_USE.md).

## Citation

Until the founding paper receives a persistent identifier, cite the software
using [CITATION.cff](CITATION.cff) and the immutable release or commit used.
