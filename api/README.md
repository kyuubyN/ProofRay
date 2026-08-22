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

An MCP server exposing the same functionality to a chat client (Claude Desktop, Cursor, ...) also
lives here: `python3 api/mcp_server.py`. See
[`../HorizonAI Engine/README.md`](../HorizonAI%20Engine/README.md) for the full MCP setup and a
generalized tutorial covering local models, hosted API models, and bring-your-own-database
patterns.

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
- `polish_base_url` (string, optional) -- the provider's `/chat/completions` URL; defaults
  to Groq's endpoint if omitted.
- `polish_api_key_env` (string, optional) -- the *name* of an environment variable holding
  the API key, never the key itself. Omit (or pass `null`) for an unauthenticated local
  server.

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

`state` is `"resolved"` when an answer was composed, or the lowercased name of a router
abstain state (e.g. `"abstention"`) when the supplied documents did not verify against the
question -- Horizon fails closed rather than guessing. On abstention, `answer`,
`answer_lines`, and `sources` are all empty/`null`.

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
curl http://127.0.0.1:8420/v1/answers/ans_1a2b3c4d5e6f7a8b9c0d1e2f?include_sources=true
```

## Deferred (named on purpose, not silently dropped)

- **Auth / API keys / rate limiting.** This increment has no request-auth mechanism.
  Needed before this is exposed outside a private demo; the scheme (API-key header vs.
  OAuth vs. something else) hasn't been chosen yet.
- **Persistent answer storage.** The in-memory `{id: answer}` store is lost on restart.
  Fine for a demo; a real deployment needs a persistent store or a documented TTL.
- **Corpus/ingest reuse.** Every `POST /v1/answers` resends the full document set and
  builds a fresh ephemeral store -- there is no "upload a corpus once, ask many questions
  against it by id" mode yet (the OpenAI Files-API shape). Real, valuable, not built here.
- **Binary/standalone packaging.** Selling this as a compiled artifact (PyInstaller,
  Nuitka, or similar) is a separate, later decision -- nothing in the repo does this today.
