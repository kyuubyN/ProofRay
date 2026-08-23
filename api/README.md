# HorizonAPI

An OpenAI-style HTTP surface over `HorizonAnswerEngine` -- the same deterministic,
zero-LLM, zero-neural-net route -> verify -> compose pipeline this project validates
against MemGym-DR and LongMemEval, behind conventional `POST`/`GET` endpoints.

No model decides what is true. Every answer is composed from verified, sealed,
source-attributable claims -- the API can hand back that full evidence trail on request,
not just the compressed answer.

## Running it

```bash
pip install -e .                    # from the repository root, once
pip install -r api/requirements.txt # flask, mcp, requests
python3 api/server.py               # serves on http://127.0.0.1:8420
```

The first run prints a bearer token you'll need for every request except `GET /v1/health` (see
"Authentication and rate limiting" below):

```
Bearer token (also saved at /home/you/.config/horizon-memory/api_credentials.json): 3f9a2b...
```

Lost it or need it again later without restarting the server? `python3 api/show_api_token.py`.

An MCP server exposing the same functionality to a chat client (Claude Desktop, Cursor, ...) also
lives here: `python3 api/mcp_server.py`. See
[`../HorizonAI Engine/README.md`](../HorizonAI%20Engine/README.md) for the full MCP setup and a
generalized tutorial covering local models, hosted API models, and bring-your-own-database
patterns.

## Authentication and rate limiting

This API has no multi-tenant concept -- it's meant to run on your own machine (default bind
`127.0.0.1`) for your own use, not as a shared public service. The token described above is a
narrower property than real multi-user auth: it's generated once on first run, persisted locally,
and additionally bound to a best-effort OS machine identifier (`/etc/machine-id` on Linux, the
hardware UUID on macOS, `MachineGuid` on Windows) recomputed on every request -- so a copy of the
credentials file moved to a different machine stops working there. It cannot stop code already
running on this machine from reading its own token file; that's the same limit any locally-stored
secret has, not a defect specific to this design. Nothing heavier (OAuth/JWT) is used because
there's no multi-user/session concept for it to protect yet -- see `machine_auth.py`'s own
docstring for the full threat model.

Every request except `GET /v1/health` needs the token:

```bash
curl -X POST http://127.0.0.1:8420/v1/answers -H "Content-Type: application/json" \
  -H "Authorization: Bearer 3f9a2b..." -d '{
  "question": "What percent did the Meridian project reduce cost by?",
  "documents": ["The Meridian project reduced compute cost by exactly 42 percent..."]
}'
```

A missing or wrong token returns `401`.

Separately, every request (including `GET /v1/health`) is rate-limited per caller: a token bucket
that refills continuously rather than resetting on a fixed clock tick, defaulting to 60
requests/minute. If you legitimately send a high volume of documents per call (not more calls),
raise the limit rather than working around it: `HORIZON_RATE_LIMIT_PER_MINUTE=600 python3
api/server.py`. Exceeding it returns `429`; every rejection is logged at `WARNING` on the server
side so you can tell a real rate-limit hit from a client bug.

## Endpoints

### `GET /v1/health`

Liveness and version check.

```json
{"status": "ok", "engine_profile": "default", "schema": "engine-profile.v1"}
```

### `POST /v1/answers`

Request body:

```json
{
  "question": "What percent did the Meridian project reduce cost by?",
  "documents": ["The Meridian project reduced compute cost by exactly 42 percent...", "..."],
  "include_sources": false
}
```

- `question` (string, required)
- `documents` (array of non-empty strings, required) -- the corpus for this one question.
  Each request is a fresh, ephemeral store; there is no persistent corpus across calls yet
  (see "Deferred" below).
- `include_sources` (bool, default `false`) -- when `true`, the response's `sources` field
  is populated with the full verified claim list (every sentence the engine actually
  routed, verified, and considered -- not just what made the compressed answer), each
  tagged with its originating document and relevance score.
- `polish` (bool, default `false`) -- when `true`, additionally rewrites the answer for
  fluency via an external OpenAI-compatible model (Groq, OpenAI, a local Ollama/llama.cpp/
  vLLM/LM Studio server -- anything speaking the `/chat/completions` shape). The external
  model only ever sees Horizon's own already-verified `answer` text, never the raw
  documents, and is explicitly instructed not to add, remove, or invent facts. `answer`
  itself is never affected by `polish` -- see `polished_answer`/`polish_state` below.
- `polish_model` (string) -- **required** when `polish: true`; the model name as the
  provider expects it (e.g. `"qwen/qwen3.6-27b"`).

The polish destination and credential are **deploy-time configuration, not request fields**:
`HORIZON_POLISH_BASE_URL` (env var, defaults to Groq's `/chat/completions` endpoint) and
`HORIZON_POLISH_API_KEY_ENV` (env var naming *another* environment variable that holds the
API key, never the key itself; unset means an unauthenticated call). An earlier version
accepted `polish_base_url`/`polish_api_key_env` directly in the request body; that let any
caller of this unauthenticated API redirect the server's outbound polish call to a host of
its choosing while naming a real secret to attach as a Bearer token (SSRF + credential
exfiltration) -- removed for that reason, not replaced.

**Activation mode** is also deploy-time configuration, never a request field, for the same reason.
By default (`HORIZON_ACTIVATION_MODE` unset or `"direct"`), every request runs the engine
unconditionally -- today's only behavior. Setting `HORIZON_ACTIVATION_MODE=keyword` gates the
engine behind a small, closed, server-configured trigger-phrase list: a question matching none of
`HORIZON_ACTIVATION_KEYWORDS` (comma-separated env var; falls back to a built-in EN+PT default set
such as "remember"/"recall"/"lembra"/"lembrar" when unset) returns `state: "not_activated"`
without the engine running at all -- zero pipeline cost, and no document/claim processing of any
kind happens for that request. This is meant for a deployment with no LLM in the loop deciding
whether Horizon is relevant; if an orchestrating agent already makes that call itself (e.g. via
`horizon_ask` over MCP, deciding for itself when to invoke the tool), leave this at the default and
let the agent's own judgment be the activation decision -- the two modes are alternatives for
different integration shapes, not something a single deployment needs to combine.

Response (`201 Created`):

```json
{
  "id": "ans_1a2b3c4d5e6f7a8b9c0d1e2f",
  "object": "answer",
  "created": 1755600000,
  "state": "resolved",
  "answer": "Meridian reduced compute cost by exactly 42 percent...\n...",
  "evidence": "Meridian reduced compute cost by exactly 42 percent...\n...",
  "direct_answer": null,
  "direct_answer_state": "not_attempted",
  "direct_answer_method": "none",
  "direct_answer_sources": [],
  "direct_answer_proof_closed": false,
  "direct_answer_residual": [],
  "answer_lines": [
    {"text": "Meridian reduced compute cost by exactly 42 percent...",
     "source": "doc:1:0:(0, 120)", "relevance_score": 0.97}
  ],
  "documents_considered": 3,
  "verified_candidates": 5,
  "answer_bytes": 812,
  "sources": null,
  "polished_answer": null,
  "polish_state": null
}
```

`answer` is retained as the backwards-compatible name of the verified evidence text;
`evidence` is its explicit alias. `direct_answer` is a separate minimal-answer channel. It is
non-null only when a configured readout supplies an extractive candidate or a proof-closed exact
result. `direct_answer_state="resolved"` always requires `direct_answer_proof_closed=true` and
verified source IDs. A missing/unsupported direct readout never removes `evidence`.

`state` is `"resolved"` when an answer was composed, the lowercased name of a router
abstain state (e.g. `"abstention"`) when the supplied documents did not verify against the
question -- Horizon fails closed rather than guessing -- or `"not_activated"` when the
server-configured activation gate (above) declined to run the engine at all. On abstention or
`"not_activated"`, `answer`, `answer_lines`, and `sources` are all empty/`null`;
`documents_considered`/`verified_candidates`/`answer_bytes` are all `0` for `"not_activated"`
specifically, since the engine never touched the supplied documents.

`sources` stays `null` unless `include_sources: true` was passed; when populated, each
entry is `{"text": ..., "source": ..., "relevance_score": ...}`, mirroring `answer_lines`
but covering the entire verified pool, not just the composed answer.

`polished_answer` and `polish_state` stay `null` unless `polish: true` was passed.
`polish_state` is one of `"polished"` (`polished_answer` populated), `"skipped_abstained"`
(the engine itself abstained -- nothing to polish, no network call was made), or `"error"`
(the external model call failed for any reason -- `polished_answer` stays `null`, but
`answer` is still correct and the request still returns `201`; a broken external call must
never take down the primary answer).

### `GET /v1/answers/{id}`

Retrieves a previously created answer from the server's in-memory store. Same response
shape as `POST`. Compressed by default (`sources: null`) regardless of what the original
`POST` requested -- pass `?include_sources=true` to get the full claim list on this GET
specifically. Unknown ids return `404`.

```bash
curl -H "Authorization: Bearer 3f9a2b..." \
  http://127.0.0.1:8420/v1/answers/ans_1a2b3c4d5e6f7a8b9c0d1e2f?include_sources=true
```

## Deferred (named on purpose, not silently dropped)

- **Multi-user auth.** The machine-bound bearer token above is real access control for the
  "one operator, one machine" case this API is built for today, but it is not a multi-tenant
  scheme -- there's no concept of separate users/sessions/scopes with different permissions.
  Needed before multiple distinct people are meant to share one deployment.
- **Persistent answer storage.** The in-memory `{id: answer}` store is lost on restart --
  still true, and still fine for a demo -- but it is now bounded rather than unbounded: at
  most `STORE_MAX_ENTRIES` (1000) entries, each evicted after `STORE_TTL_SECONDS` (3600s) at
  the latest (LRU + TTL, see `api/_engine_bridge.py`). A real deployment still needs an
  actual persistent store; this only stops an anonymous POST loop from growing memory
  without bound in the meantime. Request bodies are capped at 1 MiB total, each document at
  64 KiB, and the question at 16 KiB, for the same reason.
- **Corpus/ingest reuse.** Every `POST /v1/answers` resends the full document set and
  builds a fresh ephemeral store -- there is no "upload a corpus once, ask many questions
  against it by id" mode yet (the OpenAI Files-API shape). Real, valuable, not built here.
- **Binary/standalone packaging.** Selling this as a compiled artifact (PyInstaller,
  Nuitka, or similar) is a separate, later decision -- nothing in the repo does this today.
