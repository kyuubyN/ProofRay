<p align="center">
  <img src="assets/Horizon.jpeg" alt="Horizon Memory — orbital horizon symbol" width="420">
</p>

<h1 align="center">Horizon Memory</h1>

<p align="center"><em>Memory that can show why it remembers.</em></p>

Horizon Memory is a standalone, model-agnostic memory architecture for AI
systems. It stores durable state, retrieves evidence under explicit budgets,
preserves provenance, verifies proof-carrying results, and abstains when its
conditions are not satisfied. The core requires no LLM, hosted API, or network.

Created and founded by **Kaue Oliveira Costa**<br>
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

For controlled structured inputs, the opt-in [authorized typed sidecar](docs/TYPED_SIDECAR.md)
binds schema/rule identity, source microcitations, capabilities, lifecycle and completeness
proofs to each deterministic fact. It is the recommended zero-LLM path for databases, tools,
event streams and application state; it is not marketed as unrestricted text understanding.

`OpenTextHorizonMemory` extends the authority boundary to arbitrary text without pretending to
understand it at ingestion: documents become exact `surface_document` spans and then enter the
multilingual deterministic route/verify/compose path. On the pinned 120-case MemGym port it reproduced
the judged output byte-for-byte and therefore inherits its 0.950 score. See
[Benchmarks](BENCHMARKS.md#memgym-open-text-sidecar-port) for the boundary.

The first deterministic language pack promoted from the H-PLT program is an opt-in **English atomic
relation reader**. It recognizes a finite one-hole query grammar, preserves exact answer spans and
clause force, and uses a 38 KB checksummed WordNet morphology table. No model, embedding, network or
external parser runs:

```python
from horizon_memory import EnglishAtomicRelationCompiler, compact_english_atomic_relation

compiler = EnglishAtomicRelationCompiler()
result = compiler.read("You can buy me dinner.", "What did You buy?")
assert result.answer == "dinner" and result.proof_closed
blob = compact_english_atomic_relation(result)  # 140-byte re-openable proof envelope
```

On the frozen cross-treebank UD English-GUM test gate it reached 77/82 = **93.90% positive
accuracy**, 77/79 = **97.47% selective precision**, and 82/82 negative abstention. This promotes only
the bounded EN atomic family—not unrestricted English or universal text understanding. Its public
open-text entry point requires an explicitly selected attested FactId:
`OpenTextHorizonMemory.answer_atomic_relation_en(question, fact_id=...)`.

The same method reproduces compact HPPS evidence exactly on 1,002 Simplified-Chinese CMRC trial
questions (90.02% gold containment) and 3,524 Traditional-Chinese DRCD holdout questions (98.35%).
These are memory-delivery results, not short-answer F1; Horizon reports the two separately.

On 7,653 physically valid extractive rows from Portuguese SQuAD, HPPS reaches 93.86% containment at
K=3 and 96.85% at K=5. The equal-K real BM25 control reaches 96.60%; the +0.248 pp Horizon delta is
small but significant by paired McNemar (p=0.01445). The dataset is automatically translated, so a
native-Portuguese confirmation remains pending.

Native PT-BR confirmation is now available on FaQuAD: HPPS reaches 98.41% at K=3 and 100% at K=5
with source validity 100%. The split has only 63 questions and BM25 also reaches 100% at K=5, so this
supports language transfer but not a superiority claim.

The public open-text facade also transports the frozen LongMemEval-S deterministic composer without
drift: 120/120 answer texts are byte-identical, so its existing judge score 0.767 transfers without a
new API call. Separately, the opt-in HSSD operator lattice preserves at least one bounded interpretation
for 99.0% of all 500 public questions. Those are integration and candidate-reachability results — not
90% end-to-end answer accuracy. The active gap is proof-backed execution of surviving programs and
short semantic readout.

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
- [Authorized typed sidecar](docs/TYPED_SIDECAR.md)
- [HorizonAI Engine — tutorial, examples, MCP/REST/polish](HorizonAI%20Engine/README.md)
- [Benchmarks and claim boundaries](BENCHMARKS.md)
- [Research module](RESEARCH.md)
- [Licensing policy](LICENSE_POLICY.md)
- [Contributor License Agreement](CLA.md)
- [Governance](GOVERNANCE.md)
- [Security policy](SECURITY.md)
- [Responsible use](RESPONSIBLE_USE.md)
- [Authors](AUTHORS.md)
- [Contributors](CONTRIBUTORS.md)

## Freedom and attribution

The engine is free software under **AGPL-3.0-or-later**. If a modified version
is offered to users over a network, those users must be offered its complete
corresponding source as required by the AGPL. This is the Linux-kernel model:
the core stays free and source-available, and `horizon_memory.adapters` is the
Apache-2.0-or-AGPL dual-licensed build point where anyone can build a
distribution, product or closed-source integration on top — the way Ubuntu,
Fedora or Red Hat build on the Linux kernel rather than fork it. See
[Licensing policy](LICENSE_POLICY.md) for exactly what the Apache option does
and does not cover.

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
