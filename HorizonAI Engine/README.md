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

## Connect a database (bring your own documents)

Horizon has no database of its own -- `documents` is always a plain list/tuple you build. "Connect
a database" means: run your own query, turn each row into a `RouteDocument`, pass them in. See
[`examples/sqlite_documents_example.py`](examples/sqlite_documents_example.py) for a complete,
runnable walkthrough (builds a small SQLite fixture, queries it, feeds the rows to Horizon) --
swap the SQLite connection for Postgres/MySQL/an API call/anything else and the rest is unchanged:

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

The same thing via the HTTP API -- `POST /v1/answers` with `polish: true`:

```bash
curl -X POST http://127.0.0.1:8420/v1/answers -H "Content-Type: application/json" -d '{
  "question": "What percent did the Meridian project reduce cost by?",
  "documents": ["The Meridian project reduced compute cost by exactly 42 percent..."],
  "polish": true, "polish_model": "qwen/qwen3.6-27b", "polish_api_key_env": "GROQ_KEY"
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
| `polish_base_url` | string | defaults to Groq's endpoint |
| `polish_api_key_env` | string | name of an env var holding the key; never the key itself |

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
