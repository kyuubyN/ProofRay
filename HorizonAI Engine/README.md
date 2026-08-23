# HorizonAI Engine

The packaged, runnable surface of Horizon Memory's deterministic engine: the HTTP API, an MCP
server for connecting a chat client directly, a generic polish step for local or hosted models,
and this tutorial. Nothing here changes how the engine itself works -- see the repository root's
[`README.md`](../README.md) for what Horizon Memory *is*. This folder is how you *run* it.

## Licensing

`src/horizon_memory/` (the engine this folder wraps) stays `AGPL-3.0-or-later`, exactly as
documented in [`../LICENSE_POLICY.md`](../LICENSE_POLICY.md). A separate commercial license may
be offered later without revoking anything already granted under AGPL -- see
[`LICENSE_COMMERCIAL_PLACEHOLDER.md`](LICENSE_COMMERCIAL_PLACEHOLDER.md) for exactly what that
does and does not mean today (short version: no binding commercial terms exist yet).

## Quickstart

```bash
# from the repository root
pip install -e .
pip install -r "HorizonAI Engine/requirements.txt"
python3 "HorizonAI Engine/examples/quickstart.py"
```

The minimum code, from [`examples/quickstart.py`](examples/quickstart.py):

```python
from horizon_memory import DEFAULT_PROFILE, HorizonAnswerEngine, RouteDocument

documents = (
    RouteDocument(1, "The Meridian project reduced compute cost by exactly 42 percent...",
                  scope_id=1, session_id="s1", version=1, source="doc:1"),
)
engine = HorizonAnswerEngine(profile=DEFAULT_PROFILE, scope_id=1, session_id="s1")
result = engine.answer("What percent did the Meridian project reduce cost by?", documents)
print(result.state, result.answer_text)
```

Zero LLM, zero neural net, inside this call -- `result.state` is `"RESOLVED"` or an abstain state
name; a confident wrong answer never happens, Horizon declines instead.

## Memory presets: pick the profile for your corpus size

`DEFAULT_PROFILE` is tuned for a large corpus (hundreds of documents, an enterprise-scale
knowledge base) -- it is the exact configuration a published 0.95 judge-score result was measured
at, deliberately conservative about how much evidence competes for the final answer so a huge
corpus never dilutes a precise one. On a much smaller corpus (a personal chat history, a small
team's internal docs) that same conservatism can be too tight: Horizon always finds the right
source, but the one sentence carrying the actual number or name sometimes loses out to a shorter,
less specific neighbor. Two more presets ship for exactly that case:

```python
from horizon_memory import DEFAULT_PROFILE, TEAM_MEMORY_PROFILE, PERSONAL_MEMORY_PROFILE

engine = HorizonAnswerEngine(profile=PERSONAL_MEMORY_PROFILE, scope_id=1, session_id="s1")
```

| Preset | Best for | What changes vs. `DEFAULT_PROFILE` |
|---|---|---|
| **`DEFAULT_PROFILE`** ("Scale Memory") | A large document set or RAG-style knowledge base (hundreds of documents and up). The only preset with a published, judge-scored result behind it (MemGym-DR 0.95, LongMemEval 0.767) -- exact reproduction depends on these values. | Nothing -- this is the shipped default. |
| **`TEAM_MEMORY_PROFILE`** ("Team Memory") | A medium corpus: a small team's internal docs, a few hundred KB. A real, measured middle ground, not independently benchmarked at its own scale -- try it against your own data before relying on it. | `answer_relevance_gate_ratio` 0.3 → 0.15, `answer_shortlist_size` 50 → 150, `answer_bytes` 24,576 → 32,768. |
| **`PERSONAL_MEMORY_PROFILE`** ("Personal Memory") — **recommended for personal/small-corpus use** | A small, personal-scale corpus: a chat history, personal notes, a handful to a couple hundred messages. Favors completeness over precision-per-byte. | `answer_relevance_gate_ratio` 0.3 → 0.0, `answer_shortlist_size` 50 → 500, `answer_bytes` 24,576 → 40,000. |

**How these were found (2026-08-22/23)**: 136 real, hand-verified questions across five
independent live MongoDB-backed corpora -- a casual Brazilian-Portuguese-slang conversation
history, a formal technical Q&A corpus (two rounds of increasingly ambiguous/typo-laden
questions), an English Gen-Z-slang corpus with cross-lingual (PT-asking-about-EN) queries, and a
27-conversation "multi-hop" extension of that last corpus requiring facts from 2-3 *different*
conversations to be fused into one answer. `DEFAULT_PROFILE` always locates the right source(s)
but drops the specific answer-bearing sentence, or loses part of a multi-hop answer, more often
than it should on a corpus this small (15/20 on the multi-hop battery, for example).
`PERSONAL_MEMORY_PROFILE` recovered essentially all of them across every corpus (31/32, 19/20,
12/12, 12/12, 29/30, and a clean **20/20** on the multi-hop battery), with zero wrong answers or
wrong-conversation hallucinations introduced anywhere, in any of the 136 questions, at any
setting -- only previously-dropped detail restored. That clean, repeated sweep across five
unrelated corpora (including genuine cross-conversation composition, which the engine has no
dedicated mechanism for) is why it's the recommended starting point for this class of deployment,
not just one of three equally-plausible options.

**Why this isn't automatic**: corpus size turned out not to reliably separate "safe to loosen"
from "needs the tight defaults" -- a real technical-QA corpus's own candidate pool measured
statistically indistinguishable in size from a real large-corpus benchmark episode. There is no
detector that could pick the right preset for you reliably, so pick the one matching your own
deployment's actual scale, and switch if your corpus size changes materially. Also worth knowing:
looser settings were re-tested against the large-corpus benchmark and showed no measured harm on
a token-overlap coverage metric, but that specific metric is known to reward returning more text
regardless of whether a downstream reader's answer is actually better -- so `PERSONAL_MEMORY_PROFILE`
is not recommended for a `DEFAULT_PROFILE`-scale corpus even though nothing in this project's own
testing has shown it to be actively harmful there.

## Connect a database (bring your own documents)

Horizon has no database of its own -- `documents` is always a plain list/tuple you build. "Connect
a database" means: run your own query, turn each row into a `RouteDocument`, pass them in. Nine
complete, runnable walkthroughs:

- [`examples/sqlite_documents_example.py`](examples/sqlite_documents_example.py) -- builds a
  small SQLite fixture, queries it, feeds the rows to Horizon. No server, nothing to install.
- [`examples/duckdb_documents_example.py`](examples/duckdb_documents_example.py) -- same idea,
  embedded and in-memory by default. No server, always works.
- [`examples/mongodb_documents_example.py`](examples/mongodb_documents_example.py) -- same
  pattern against a MongoDB collection; runs with no server at all against `mongomock` by
  default, or point it at a real deployment with `MONGODB_URI`.
- [`examples/redis_documents_example.py`](examples/redis_documents_example.py) -- same pattern
  against Redis keys; runs with no server against `fakeredis` by default, or point it at a real
  deployment with `REDIS_URL`.
- [`examples/dynamodb_documents_example.py`](examples/dynamodb_documents_example.py) -- same
  pattern against a DynamoDB table; runs with no real AWS account against `moto` by default, or
  point it at a real table with `DYNAMODB_USE_REAL_AWS=1`.
- [`examples/postgres_documents_example.py`](examples/postgres_documents_example.py) -- needs a
  real Postgres instance (`POSTGRES_DSN`); there's no in-process stand-in for Postgres, so this
  one won't run until you point it at a server you already have.
- [`examples/mysql_documents_example.py`](examples/mysql_documents_example.py) -- same idea for
  MySQL (`MYSQL_HOST`/`MYSQL_USER`/`MYSQL_PASSWORD`/`MYSQL_DB`), also requires a real instance.
- [`examples/elasticsearch_documents_example.py`](examples/elasticsearch_documents_example.py)
  -- same idea for Elasticsearch/OpenSearch (`ELASTICSEARCH_URL`); the most direct "already
  have a search index, want deterministic answers instead" example here, also requires a real
  cluster.
- [`examples/spacetimedb_documents_example.py`](examples/spacetimedb_documents_example.py) --
  talks to SpacetimeDB's HTTP SQL endpoint directly (no maintained Python SDK exists); needs a
  local `spacetime start` + a published module (`SPACETIMEDB_URL`/`SPACETIMEDB_DATABASE`).

None of these examples start a database server on your behalf -- the five with a pure-Python,
no-server mode (SQLite, DuckDB, MongoDB via `mongomock`, Redis via `fakeredis`, DynamoDB via
`moto`) run out of the box; the other four (Postgres, MySQL, Elasticsearch, SpacetimeDB) print
setup instructions and exit cleanly if their server isn't already running, rather than trying to
launch one.

Swap any one's query for your own schema/API call and the rest is unchanged:

```python
rows = your_db_connection.execute("SELECT id, body FROM articles").fetchall()
documents = tuple(
    RouteDocument(row_id, body, scope_id=1, session_id="s1", version=1, source=f"articles:{row_id}")
    for row_id, body in rows)
result = engine.answer(question, documents)
```

## Polish answers with a local or API model

Horizon's own answer is already deterministic and verified -- polishing is optional, purely
cosmetic prose rewriting, never a source of new facts. One adapter,
`OpenAICompatiblePolishAdapter`, works against anything speaking the OpenAI `/chat/completions`
shape (Groq, OpenAI, Ollama, llama.cpp's server, vLLM, LM Studio):

| `PolishConfig` field | Meaning | Default |
|---|---|---|
| `model` | model name as the provider expects it | required |
| `base_url` | the provider's `/chat/completions` URL | Groq's endpoint |
| `api_key_env` | name of an env var holding the API key; `None` sends no `Authorization` header | `None` |
| `temperature` | sampling temperature | `0.1` |
| `max_output_tokens` | output cap | `1200` |
| `reasoning_effort` | sent as-is if not `None`; omit for models that don't support it | `"none"` |

Two runnable examples, both dry-run-safe by default (`allow_network=False`, no network call, no
key required):

```bash
python3 "HorizonAI Engine/examples/local_model_polish_example.py"   # e.g. Ollama
python3 "HorizonAI Engine/examples/api_model_polish_example.py"     # e.g. Groq
```

The same thing via the HTTP API -- `POST /v1/answers` with `polish: true` (start the server first
with `HORIZON_POLISH_API_KEY_ENV=GROQ_KEY GROQ_KEY=your-key python3 "HorizonAI Engine/run_api_server.py"`
-- the destination/credential are this process's own env config, never request fields, see below).
The server prints a bearer token on that first run -- every request needs it (see
[`../api/README.md`](../api/README.md)'s "Authentication and rate limiting"):

```bash
curl -X POST http://127.0.0.1:8420/v1/answers -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token from server startup>" -d '{
  "question": "What percent did the Meridian project reduce cost by?",
  "documents": ["The Meridian project reduced compute cost by exactly 42 percent..."],
  "polish": true, "polish_model": "qwen/qwen3.6-27b"
}'
```

`answer` in the response is always Horizon's own deterministic text; `polished_answer` is the
additional, optional rewrite (`null` if `polish` wasn't requested, or if the polish call itself
errored -- a broken external model call never affects the primary answer, see
[`../api/README.md`](../api/README.md)).

## Connect your chat client (MCP)

Run the server:

```bash
python3 "HorizonAI Engine/run_mcp_server.py"
```

Add it to Claude Desktop's config (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "horizon-memory": {
      "command": "python3",
      "args": ["/absolute/path/to/HorizonAI Engine/run_mcp_server.py"],
      "env": { "GROQ_KEY": "your-key-here" }
    }
  }
}
```

Cursor uses the same `mcpServers` shape in its own MCP settings. Note the explicit `env` block --
MCP clients only pass a small safe allowlist of environment variables to a launched server by
default (`HOME`, `PATH`, etc.), not your shell's full environment, so any key the polish step
needs (`GROQ_KEY`, etc.) must be listed there explicitly.

`horizon_ask` tool parameters (identical to `POST /v1/answers`'s body):

| Parameter | Type | Meaning |
|---|---|---|
| `question` | string, required | the question to ask |
| `documents` | array of strings, required | the corpus for this one question |
| `include_sources` | bool, default `false` | return the full verified claim list, not just the composed answer |
| `polish` | bool, default `false` | additionally rewrite the answer via an OpenAI-compatible model |
| `polish_model` | string | required when `polish` is true |

The polish destination/credential (`HORIZON_POLISH_BASE_URL`/`HORIZON_POLISH_API_KEY_ENV`) are
deploy-time env config, not tool parameters -- an earlier version took `polish_base_url`/
`polish_api_key_env` directly as caller-suppliable arguments; that let any caller redirect the
server's outbound polish call and named secret to a host of its own choosing (SSRF + credential
exfiltration), so both were removed from the tool signature (see `api/README.md`'s own note on
this same fix -- this table previously still listed them as parameters, which was stale).

## Activation modes: tool judgment vs. a keyword gate

Two ways to decide *when* Horizon should engage, picked at deploy time, never per-request --
use whichever matches your integration, not both at once for the same deployment.

**Tool mode (recommended, the default -- nothing to configure).** An orchestrating LLM agent
already decides for itself, from its own read of the conversation, whether calling `horizon_ask`
is relevant right now. That judgment call already *is* the activation decision -- this mode needs
no keyword list, no separate mechanism, and is exactly what you get by doing nothing.

**Keyword mode**, for a deployment with no LLM making that call: set `HORIZON_ACTIVATION_MODE=keyword`
before starting either server. A question matching none of a small, closed, server-configured
trigger-phrase list returns `state: "not_activated"` -- the engine never runs, zero pipeline cost.

```bash
HORIZON_ACTIVATION_MODE=keyword python3 "HorizonAI Engine/run_api_server.py"
```

```bash
curl -X POST http://127.0.0.1:8420/v1/answers -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token from server startup>" -d '{
  "question": "What percent did the Meridian project reduce cost by?",
  "documents": ["The Meridian project reduced compute cost by exactly 42 percent..."]
}'
# -> {"state": "not_activated", "answer": null, ...} -- no trigger phrase, engine never ran

curl -X POST http://127.0.0.1:8420/v1/answers -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token from server startup>" -d '{
  "question": "Do you remember what percent the Meridian project reduced cost by?",
  "documents": ["The Meridian project reduced compute cost by exactly 42 percent..."]
}'
# -> {"state": "resolved", "answer": "...42 percent...", ...} -- "remember" matched, engine ran
```

The default trigger set covers common EN+PT phrasings ("remember", "recall", "lembra",
"lembrar", ...). Override it with `HORIZON_ACTIVATION_KEYWORDS` (comma-separated, server-side env
only -- never a request field, for the same reason `polish_base_url` isn't one, see above):

```bash
HORIZON_ACTIVATION_MODE=keyword HORIZON_ACTIVATION_KEYWORDS="what was,qual foi" \
  python3 "HorizonAI Engine/run_api_server.py"
```

Applies identically to both transports (`POST /v1/answers` and `horizon_ask` over MCP) -- one
gate, shared by both, since they already run through the same underlying implementation.

## Deferred

Named on purpose, not silently dropped:

- **No real commercial license text yet** -- see `LICENSE_COMMERCIAL_PLACEHOLDER.md`.
- **No auth / rate limiting** on either the HTTP API or the MCP server.
- **No persistent corpus** -- every call takes its documents inline; there's no "upload once,
  query by id later" mode yet.
- **One MCP tool only** (`horizon_ask`) -- no separate ingest/session tools yet.
- **No standalone binary/installer, no GUI, no direct database connectivity** -- feasibility
  notes (what's straightforward vs. what's real new engineering) live in
  [`ROADMAP.md`](ROADMAP.md).
