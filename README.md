<p align="center">
  <img src="assets/ProofRay.jpeg" alt="ProofRay — a ray crossing an event horizon" width="420">
</p>

<h1 align="center">ProofRay</h1>

<p align="center"><em>Memory that can show why it remembers.</em></p>

<p align="center"><strong>Created by ProofRay Intelligence.</strong></p>

## What is ProofRay?

Most AI memory tools work like a confident friend: they answer fast, but you can never fully
trust what they say, because you can't see where the answer came from.

ProofRay works the other way around. Give it your documents and a question, and it hands back an
answer built only from things it can actually point to and verify. Every sentence in the answer
comes with a receipt: which document it came from, and proof that document hasn't been tampered
with. If it can't find a real answer, it tells you honestly instead of making one up.

It runs entirely on your own machine. No AI model is required to get an answer, and no data ever
leaves your computer unless you choose to connect one yourself.

> **Status — Public Alpha:** ProofRay is ready for experimentation and real integrations, but it
> does not yet map every possible question, domain or language into a closed proof. APIs and
> behavior may still evolve before the first stable release.

> **The ProofRay contract:** no proof, no asserted direct answer. When ProofRay cannot establish an
> answer from authorized memory, it returns the verified evidence it does have or abstains instead
> of filling the gap with a plausible invention. This fail-closed design substantially reduces
> false memories; it is not a claim that alpha software can never contain an ingestion, routing or
> interpretation bug.

The core has also been successfully run by early testers on **Windows**. Automated release CI
currently covers Ubuntu with Python 3.10–3.13, so Windows support should still be considered
early-user validated rather than part of the automated compatibility matrix.

The current limitation is semantic coverage, not an attempt to hide uncertainty: ProofRay does not
yet provide universal natural-language mapping. The parts that read and understand questions have
so far been carefully tested in **English and Portuguese** only. Other languages, starting with
Chinese, are planned but not validated yet; see [Roadmap](ROADMAP.md).

ProofRay grew out of the Horizon research line inside Q-HDRE, a multi-year project that tried many
different approaches to AI
memory and kept only the ones that survived real testing. If you're curious about that history,
it's written up in [Origin and design lineage](docs/ORIGIN_AND_DESIGN.md).

## How ProofRay answers

ProofRay uses a proof-first cascade instead of guessing:

1. **Precise answer:** it first checks whether the question can be answered exactly from authorized
   memory. A direct answer is released only when its proof closes and can be reopened against the
   original sources.
2. **Verified evidence:** if an exact answer cannot be proved, ProofRay returns the highest-ranked
   verified excerpts related to the question. These excerpts are useful context, but ProofRay does
   not pretend that they form a complete direct answer.
3. **Abstention:** if no trustworthy evidence supports the question, or the available memories
   conflict, ProofRay abstains instead of inventing a recollection.

In short: **proved answer → verified excerpts → abstention**. Relevance can choose what ProofRay
examines first, but only source authority and a reopenable proof can turn evidence into an asserted
direct answer.

##Example: Connect Gemini (or another AI) without giving it memory authority

An LLM can sit after ProofRay as a presentation layer, or it can call ProofRay as a tool. In both
designs, MongoDB stays local and Gemini receives only ProofRay's result, never the complete database
corpus. The runnable tutorial is
[`gemini_proofray_tool_call.py`](ProofRay%20Engine/examples/gemini_proofray_tool_call.py).

Install the example dependencies and provide credentials through the environment:

```bash
pip install pymongo mongomock
export GEMINI_API_KEY="your-key"
# Optional: without this, the tutorial uses a safe in-process mongomock fixture.
export MONGODB_URI="mongodb://localhost:27017"
```

PowerShell uses the same variables:

```powershell
$env:GEMINI_API_KEY = "your-key"
$env:MONGODB_URI = "mongodb://localhost:27017"  # optional
```

### 1. ProofRay first, Gemini only rewrites

```bash
python3 "ProofRay Engine/examples/gemini_proofray_tool_call.py" --mode polish
```

The application queries MongoDB, lets ProofRay route and verify the memory, and sends Gemini only a
small object containing `question`, `proofray_state`, `proofray_authority`, and `proofray_answer`.
Gemini may make the wording shorter or friendlier, but the system instruction forbids adding,
calculating or changing facts and numbers.

### 2. Gemini calls ProofRay as a native tool

```bash
python3 "ProofRay Engine/examples/gemini_proofray_tool_call.py" --mode tool
```

This uses Gemini's native function-calling protocol:

```text
user question
→ Gemini requests query_proofray_memory(question)
→ the application queries MongoDB and executes ProofRay locally
→ only {state, authority, answer, backend, source_count} returns to Gemini
→ Gemini presents that verified result
```

The first Gemini request is forced to call `query_proofray_memory`; after the local result is
available, the second request disables further tool calls and permits presentation only. If ProofRay
returns `state=abstain`, Gemini is instructed to report that there is not enough verified memory.

Run both patterns together with:

```bash
python3 "ProofRay Engine/examples/gemini_proofray_tool_call.py" --mode both
```

`both` uses three `generateContent` calls: one polish request and one two-call tool cycle. In the
frozen five-call smoke test that motivated this tutorial (one polish request plus two repeated tool
cycles), `gemini-3.1-flash-lite` preserved the unique value `42` in all three final outputs, issued
both tool requests with the original question unchanged, and produced byte-identical answers across
the repeated tool cycles. The run used `mongomock`, 979 input tokens, 110 output tokens and 18.27 s
of accumulated provider latency. This is an integration and authority-boundary smoke test, not an
answer-accuracy benchmark.

## Try it in two minutes

```bash
pip install proofray
```

```python
import secrets

from proofray import ProofRayConfig, ProofRayMemory

scope = 7
memory = ProofRayMemory.create(ProofRayConfig(
    root="./proofray-data",
    scope_id=scope,
    key=secrets.token_bytes(32),
))
memory.put(scope, fact_id=100, version=1, value=90)
result = memory.get(scope, fact_id=100)
print(result.value)
memory.close()
```

```bash
proofray --doctor
```

That's the low-level storage piece. Most people will actually want to ask ProofRay a question
in plain language and get a written answer back:

```python
from proofray import DEFAULT_PROFILE, ProofRayAnswerEngine, RouteDocument

documents = (
    RouteDocument(1, "The Meridian project reduced compute cost by exactly 42 percent...",
                  scope_id=1, session_id="s1", version=1, source="doc:1"),
)
engine = ProofRayAnswerEngine(profile=DEFAULT_PROFILE, scope_id=1, session_id="s1")
result = engine.answer("What percent did the Meridian project reduce cost by?", documents)
print(result.answer_text)
```

The full walkthrough (connecting your own database, adding a chat assistant, picking the right
settings for a small personal memory versus a large company knowledge base) lives in
[ProofRay Engine](ProofRay%20Engine/README.md), written as a step-by-step tutorial.

For timestamped conversations, the Python API also has an opt-in recall route that keeps speaker,
session, order and observation time as typed metadata instead of adding them to the remembered text:

```python
from datetime import date
from proofray import (
    CONVERSATIONAL_HIGH_RECALL_PROFILE, ConversationalRecallGenerator,
    ProofRayAnswerEngine, RouteDocument,
)

history = (
    RouteDocument(1, "I finally bought the cobalt bicycle.", 1, "summer-chat", 1, "chat:1",
                  sequence=1, event_time=date(2025, 7, 12).toordinal(), speaker="Alice"),
)
engine = ProofRayAnswerEngine(
    profile=CONVERSATIONAL_HIGH_RECALL_PROFILE,
    scope_id=1,
    session_id="current-chat",
    candidate_generator=ConversationalRecallGenerator(),
    allow_scope_fallback=True,
)
result = engine.answer("Which bicycle did Alice buy?", history)
```

This generator only transports candidate `FactId`s. The normal ProofRay verifier still reopens and
authorizes every source, and unsupported or conflicting readout still has to abstain. Cross-session
fallback is explicitly enabled above and remains off by default in the Python engine. HTTP and MCP
also accept a backward-compatible structured document shape that preserves these coordinates without
putting them into document text. The measured 64-candidate profile reaches 90.77% annotated-turn hit
on consumed-development LoCoMo, but that is retrieval reachability rather than answer accuracy. It
remains a deploy-time opt-in (`PROOFRAY_CONVERSATIONAL_RECALL=true`; the historical
`HORIZON_CONVERSATIONAL_RECALL` alias remains accepted) until an independently manifested
personal-conversation cohort confirms the result. Enabling the flag selects the exact
`CONVERSATIONAL_HIGH_RECALL_PROFILE`, not the unevaluated 800-claim scale profile.

`OpenTextProofRayMemory(..., ledger_path=...)` persists those route coordinates in the same attested
sidecar record. Reopening the ledger reconstructs multi-session `RouteDocument`s exactly; legacy
metadata-free ledgers continue to reopen under their original v1 attestations. FactId-bound observed
context intents are also restored with exact fiber membership and insertion order.
Repeated questions over an unchanged open-text snapshot reuse only disposable derived routing state;
ingest invalidates it. Direct `ProofRayAnswerEngine` calls remain request-ephemeral unless a persistent
facade explicitly passes `reuse_prepared_runtime=True`.

For finite questions that can be closed from exact measurements, counts, dates or relations, the
default engine now attempts the proof-convergent resolver. It returns a short `direct_answer` only
after ProofRay reopens a question-bound certificate; otherwise the ordinary verified evidence remains
unchanged. Pass `direct_answer_resolver=None` when an evidence-only integration is desired:

```python
from proofray import ProofRayAnswerEngine

engine = ProofRayAnswerEngine()
result = engine.answer("How many days did the two trips take in total?", documents)
print(result.final_answer_text)
```

This path is local and deterministic. A benchmark judge may score its frozen output, but no model,
API response or relevance score participates in proof construction or authorization. The default
activation follows the measured >90% final-output gate; it does not make unsupported operator worlds
answerable, and explicit `None` retains the prior evidence-only behavior.

For visible multi-turn subqueries, `ExplanatoryProofResolver` is an additional opt-in contextual
resolver. It closes a DAG of exact source obligations and witnessed bridges, rejects incomplete or
contested worlds, and reopens a certificate bound to every context intent and authority coordinate.
Its current MemGym consumed-development proof coverage is 6/120, so it is not enabled by default and
is not advertised as MemGym answer accuracy. `ProofCascadeResolver` is the ready-made opt-in order:
scalar proof first, explanatory proof second, ordinary engine evidence fallback last.

## What can you connect it to?

ProofRay never decides anything on its own behalf. Every connection below is something you turn on
yourself.

- **Your own data.** Point it at anything: a SQLite file, MongoDB, Postgres, Redis, DynamoDB,
  Elasticsearch, and more. Ready-made examples for each one are in the tutorial.
- **An AI model, if you want one.** ProofRay's own answer is already complete and verified. If
  you'd like it rewritten in smoother prose, you can optionally pass it through any AI model
  (local or hosted) purely for style. That model never gets to invent facts, and if the request
  fails for any reason, you simply get ProofRay's original answer back, unaffected.
- **A chat assistant**, like Claude Desktop or Cursor, through a small connector so it can ask
  ProofRay questions directly.
- **A simple web request**, if you'd rather call it like any other API from your own app.

## Learn more

- [ProofRay Engine](ProofRay%20Engine/README.md): the full tutorial covering quickstart,
  connecting a database, adding a model, and chat assistants
- [Roadmap](ROADMAP.md): where the project is headed
- [Architecture](docs/ARCHITECTURE.md): how it's actually built, for the technically curious
- [Benchmarks and claim boundaries](docs/BENCHMARKS.md): the tests we ran and what they showed
- [Origin and design lineage](docs/ORIGIN_AND_DESIGN.md): the research history behind the project
- [Authorized typed sidecar](docs/TYPED_SIDECAR.md): for connecting structured data sources
- [Research module](docs/RESEARCH.md): experimental, not-yet-stable ideas being tried
- [Authors](docs/AUTHORS.md)

## The license, in plain terms

ProofRay is free software, licensed under AGPL-3.0-or-later. Think of it like the Linux kernel:
the core stays free for everyone, forever, and anyone can build a product or business on top of
it, the way Ubuntu or Fedora build on Linux without needing permission. The one condition is that
if you offer a modified version of ProofRay to other people over a network, you need to share your
changes to it too.

There's also a small, separate part of the codebase (the "adapters") licensed more permissively,
specifically so it's easy to connect ProofRay to other systems, even closed-source ones.

You're welcome to charge your own customers for hosting ProofRay, supporting it, or building a
product on top of it. If you modify ProofRay's core and offer that modified version to others over
a network, you need to share those changes back, as described above. You just can't remove the
license and credit notices, or present your own version as if it were the official project.
The full legal details are in [LICENSE_POLICY.md](docs/LICENSE_POLICY.md),
[TRADEMARKS.md](docs/TRADEMARKS.md), [GOVERNANCE.md](docs/GOVERNANCE.md) and
[AI_TRAINING_POLICY.md](docs/AI_TRAINING_POLICY.md).

## Who's responsible for what

ProofRay is a general-purpose tool. If you deploy it, you're responsible for the data you feed it,
what you automate with it, and following the rules that apply to your own use case. See
[DISCLAIMER.md](docs/DISCLAIMER.md), [SECURITY.md](docs/SECURITY.md) and
[RESPONSIBLE_USE.md](docs/RESPONSIBLE_USE.md).

## Citing ProofRay

The founding paper doesn't have a permanent identifier yet. Until it does, please cite the
software itself using [CITATION.cff](CITATION.cff), along with the specific release or commit you
used.

## Name and compatibility

ProofRay is the public project and distribution name; ProofRay Intelligence is the project
organization. The historical Horizon name remains part of the architecture and research lineage.
To avoid breaking existing applications and durable records during the alpha rebrand,
`horizon_memory`, `HorizonMemory`, `HorizonAnswerEngine`, the `horizon` command, `HORIZON_*`
environment variables and existing wire fields remain supported compatibility identifiers. New
code should prefer `proofray`, `ProofRayMemory`, `ProofRayAnswerEngine` and the `proofray` command.
