<p align="center">
  <img src="assets/Horizon.jpeg" alt="Horizon Memory — orbital horizon symbol" width="420">
</p>

<h1 align="center">Horizon Memory</h1>

<p align="center"><em>Memory that can show why it remembers.</em></p>

## What is Horizon Memory?

Most AI memory tools work like a confident friend: they answer fast, but you can never fully
trust what they say, because you can't see where the answer came from.

Horizon works the other way around. Give it your documents and a question, and it hands back an
answer built only from things it can actually point to and verify. Every sentence in the answer
comes with a receipt: which document it came from, and proof that document hasn't been tampered
with. If it can't find a real answer, it tells you honestly instead of making one up.

It runs entirely on your own machine. No AI model is required to get an answer, and no data ever
leaves your computer unless you choose to connect one yourself.

> **Status:** this is an early, actively-developed version. Things may still be rough around the
> edges. Read on to see how it works today.

The parts of Horizon that read and understand natural-language questions have so far been
carefully tested in **English and Portuguese** only. Other languages, starting with Chinese, are
planned but not validated yet; see [Roadmap](ROADMAP.md).

Horizon grew out of a multi-year research project that tried many different approaches to AI
memory and kept only the ones that survived real testing. If you're curious about that history,
it's written up in [Origin and design lineage](docs/ORIGIN_AND_DESIGN.md).

## Try it in two minutes

```bash
pip install horizon-memory
```

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

That's the low-level storage piece. Most people will actually want to ask Horizon a question
in plain language and get a written answer back:

```python
from horizon_memory import DEFAULT_PROFILE, HorizonAnswerEngine, RouteDocument

documents = (
    RouteDocument(1, "The Meridian project reduced compute cost by exactly 42 percent...",
                  scope_id=1, session_id="s1", version=1, source="doc:1"),
)
engine = HorizonAnswerEngine(profile=DEFAULT_PROFILE, scope_id=1, session_id="s1")
result = engine.answer("What percent did the Meridian project reduce cost by?", documents)
print(result.answer_text)
```

The full walkthrough (connecting your own database, adding a chat assistant, picking the right
settings for a small personal memory versus a large company knowledge base) lives in
[HorizonAI Engine](HorizonAI%20Engine/README.md), written as a step-by-step tutorial.

## What can you connect it to?

Horizon never decides anything on its own behalf. Every connection below is something you turn on
yourself.

- **Your own data.** Point it at anything: a SQLite file, MongoDB, Postgres, Redis, DynamoDB,
  Elasticsearch, and more. Ready-made examples for each one are in the tutorial.
- **An AI model, if you want one.** Horizon's own answer is already complete and verified. If
  you'd like it rewritten in smoother prose, you can optionally pass it through any AI model
  (local or hosted) purely for style. That model never gets to invent facts, and if the request
  fails for any reason, you simply get Horizon's original answer back, unaffected.
- **A chat assistant**, like Claude Desktop or Cursor, through a small connector so it can ask
  Horizon questions directly.
- **A simple web request**, if you'd rather call it like any other API from your own app.

## Learn more

- [HorizonAI Engine](HorizonAI%20Engine/README.md): the full tutorial covering quickstart,
  connecting a database, adding a model, and chat assistants
- [Roadmap](ROADMAP.md): where the project is headed
- [Architecture](docs/ARCHITECTURE.md): how it's actually built, for the technically curious
- [Benchmarks and claim boundaries](docs/BENCHMARKS.md): the tests we ran and what they showed
- [Origin and design lineage](docs/ORIGIN_AND_DESIGN.md): the research history behind the project
- [Authorized typed sidecar](docs/TYPED_SIDECAR.md): for connecting structured data sources
- [Research module](docs/RESEARCH.md): experimental, not-yet-stable ideas being tried
- [Authors](docs/AUTHORS.md)

## The license, in plain terms

Horizon is free software, licensed under AGPL-3.0-or-later. Think of it like the Linux kernel:
the core stays free for everyone, forever, and anyone can build a product or business on top of
it, the way Ubuntu or Fedora build on Linux without needing permission. The one condition is that
if you offer a modified version of Horizon to other people over a network, you need to share your
changes to it too.

There's also a small, separate part of the codebase (the "adapters") licensed more permissively,
specifically so it's easy to connect Horizon to other systems, even closed-source ones.

You're welcome to charge for hosting it, supporting it, or building on it. You just can't remove
the license and credit notices, or present your own version as if it were the official project.
The full legal details are in [LICENSE_POLICY.md](docs/LICENSE_POLICY.md),
[TRADEMARKS.md](docs/TRADEMARKS.md), [GOVERNANCE.md](docs/GOVERNANCE.md) and
[AI_TRAINING_POLICY.md](docs/AI_TRAINING_POLICY.md).

## Who's responsible for what

Horizon is a general-purpose tool. If you deploy it, you're responsible for the data you feed it,
what you automate with it, and following the rules that apply to your own use case. See
[DISCLAIMER.md](docs/DISCLAIMER.md), [SECURITY.md](docs/SECURITY.md) and
[RESPONSIBLE_USE.md](docs/RESPONSIBLE_USE.md).

## Citing Horizon

The founding paper doesn't have a permanent identifier yet. Until it does, please cite the
software itself using [CITATION.cff](CITATION.cff), along with the specific release or commit you
used.
