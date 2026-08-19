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

Co-author: **Yuri Yassumura Pecelin**<br>
[ORCID 0009-0007-9766-9809](https://orcid.org/0009-0007-9766-9809) ·
[yuripecelin@gmail.com](mailto:yuripecelin@gmail.com)

Co-author: **Matheus Geraldi**<br>
[ORCID 0009-0009-9059-7827](https://orcid.org/0009-0009-9059-7827) ·
[Matheus.ge.si@gmail.com](mailto:Matheus.ge.si@gmail.com)

See [AUTHORS.md](AUTHORS.md) for the full authorship record and
[CONTRIBUTORS.md](CONTRIBUTORS.md) for the broader contributor roster.

> Status: alpha version. Interfaces and research claims are being audited
> before the first public release, and there is still a lot to improve —
> expect rough edges, incomplete coverage, and active, ongoing work.

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

## Performance

Measured directly, not estimated: five fresh (non-cached) questions from the
MemGym-DR benchmark corpus, one full route → verify → compose pipeline run
each, single process, no GPU.

| Metric | Value |
|---|---|
| Mean CPU time per question | 1.79 s |
| Mean wall-clock time per question | 1.79 s |
| Peak resident memory (one process) | ~127 MB |
| Documents searched per question | 483–567 (mean ≈ 536) |
| Evidence budget spent | 24–64 KB of verified text (not documents) |

CPU time and wall-clock time came out effectively identical, which means the
pipeline spent that time actually computing, not waiting on disk or network —
no GPU, model weights or hosted API are involved anywhere in this path.
Measured with Python's `resource.getrusage`; see
[Benchmarks and claim boundaries](BENCHMARKS.md) for accuracy numbers on the
same corpus, and [HorizonAI Engine](HorizonAI%20Engine/README.md) for the
live demo this was measured against.

## Connecting Horizon

The core never calls a model, a database driver, or a chat client on its own
— every connection point below is explicit, optional, and lives at the edge,
never inside routing, verification or composition.

- **A database**: Horizon takes `documents: list[str]` per call. Query your
  own database yourself and hand the results in — this bring-your-own-data
  pattern is the supported way in today; no native DB driver ships yet.
  Runnable examples exist for SQLite, DuckDB, MongoDB, Redis, DynamoDB,
  PostgreSQL, MySQL, Elasticsearch/OpenSearch and SpacetimeDB — see
  [HorizonAI Engine](HorizonAI%20Engine/README.md#connect-a-database-bring-your-own-documents).
- **An AI model, local or hosted**: the optional `polish` layer hands the
  already-composed, already-verified answer to any OpenAI-compatible
  `chat/completions` endpoint purely to smooth prose. It never decides facts,
  and a failed or rate-limited call always degrades to the original,
  unmodified verified answer.

  ```python
  from horizon_memory.adapters import OpenAICompatiblePolishAdapter, PolishConfig

  adapter = OpenAICompatiblePolishAdapter(allow_network=True)
  config = PolishConfig(model="llama-3.1-8b-instant", api_key_env="GROQ_KEY")
  result = adapter.polish(question, answer_text, config)
  ```

- **A chat client** (Claude Desktop, Cursor, and similar): `api/mcp_server.py`
  exposes the same deterministic answer engine as an MCP tool, `horizon_ask`.
- **A REST client**: `api/server.py` exposes `POST /v1/answers` over HTTP,
  with the same optional `polish` option available as a request field.

Full tutorial, runnable examples (quickstart, database, local/hosted model,
chat client) and licensing notes:
[HorizonAI Engine](HorizonAI%20Engine/README.md).

## Documentation

- [Origin and design lineage](docs/ORIGIN_AND_DESIGN.md)
- [Architecture](docs/ARCHITECTURE.md)
- [HorizonAI Engine — tutorial, examples, MCP/REST/polish](HorizonAI%20Engine/README.md)
- [Benchmarks and claim boundaries](BENCHMARKS.md)
- [Research module](RESEARCH.md)
- [Licensing policy](LICENSE_POLICY.md)
- [Governance](GOVERNANCE.md)
- [Security policy](SECURITY.md)
- [Responsible use](RESPONSIBLE_USE.md)
- [Authors](AUTHORS.md)
- [Contributors](CONTRIBUTORS.md)

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
