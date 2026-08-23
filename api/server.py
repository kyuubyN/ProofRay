# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""HorizonAPI -- an OpenAI-style HTTP surface over the deterministic `HorizonAnswerEngine`.

Two endpoints, one specific GET mode, one optional cosmetic step:

- `POST /v1/answers` runs the engine on caller-supplied documents and returns a compressed
  answer (`sources: null` by default; pass `include_sources: true` to also get the full,
  provenance-tagged claim list the engine actually verified -- everything a caller would need to
  audit *why* the answer says what it says, not just trust it). Pass `polish: true` (plus
  `polish_model`) to additionally get `polished_answer`: the same deterministic `answer`,
  rewritten for fluency by an external OpenAI-compatible model. `answer` itself NEVER changes
  based on `polish` -- per this project's own ground rule, models are readers/renderers only,
  never authority, so a broken polish call degrades gracefully (`polish_state: "error"`) rather
  than affecting the primary answer.
- `GET /v1/answers/{id}` retrieves a previously created answer from the in-memory store.
  `?include_sources=true` is the "activated GET" -- same compressed-by-default response shape,
  full claim list attached only when asked.
- `GET /v1/health` is a trivial liveness/version check.

**Activation mode** (deploy-time config, never a per-request field -- see
`_engine_bridge.ACTIVATION_MODE`): by default every `POST /v1/answers` runs the engine
unconditionally (today's only behavior, unchanged). Setting `HORIZON_ACTIVATION_MODE=keyword`
gates the engine behind a small, closed, server-configured trigger-phrase list
(`HORIZON_ACTIVATION_KEYWORDS`) -- a question matching none of them returns `state:
"not_activated"` without running the pipeline at all. The alternative activation mode -- an
orchestrating LLM agent deciding for itself when Horizon is relevant -- needs no server-side
mechanism here; that's exactly what `api/mcp_server.py`'s `horizon_ask` tool already is.

Everything here is the same zero-LLM, zero-neural-net, deterministic pipeline
`HorizonAnswerEngine` already wraps -- this file only adds HTTP request/response plumbing on top
of `api/_engine_bridge.py`'s shared helpers (also used by `api/mcp_server.py`). See
`api/README.md` for the full request/response contract, the authentication/rate-limiting model
(`machine_auth.py` / `rate_limit.py`) and the remaining explicitly-deferred items.
"""
from __future__ import annotations

from flask import Flask, jsonify, request

from _engine_bridge import (  # STORE is unused here but re-exported for tests (`from server import STORE`)
    DEFAULT_PROFILE, ENGINE, MAX_DOCUMENTS, STORE, build_documents, build_polish_config,  # noqa: F401
    json_bool, load_answer, maybe_answer, new_answer_id_and_timestamp, run_polish, serialize,
    store_answer, validate_question_length,
)
from machine_auth import ensure_local_credentials, verify_bearer_token
from rate_limit import RATE_LIMITER

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1 * 1024 * 1024  # 1 MiB; Werkzeug returns 413 above this

# Generated once, on first run, and persisted locally (see machine_auth.py's own docstring for
# the exact threat model this addresses and its honest limits). Every request except the health
# check must present this token; `python3 server.py` also prints it once at startup so the
# operator has it to configure their own client.
CREDENTIALS = ensure_local_credentials()


@app.before_request
def _security_gate():
    # Rate limit first (cheap, keyed on the real socket peer, never a client-suppliable header)
    # so a flood of requests is throttled before spending any work on auth or the engine itself.
    if not RATE_LIMITER.allow(request.remote_addr or "unknown"):
        return jsonify({"error": {
            "message": "rate limit exceeded", "type": "rate_limit_error"}}), 429
    if request.path == "/v1/health":
        return None  # Liveness check carries no sensitive data; left open for monitoring tools.
    if not verify_bearer_token(request.headers.get("Authorization"), CREDENTIALS):
        return jsonify({"error": {
            "message": "missing or invalid bearer token", "type": "auth_error"}}), 401
    return None


def _include_sources_from_query() -> bool:
    return request.args.get("include_sources", "").strip().lower() in ("1", "true", "yes")


def _bad_request(message: str):
    return jsonify({"error": {"message": message, "type": "invalid_request_error"}}), 400


@app.route("/v1/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "engine_profile": DEFAULT_PROFILE.name,
                     "schema": DEFAULT_PROFILE.schema})


@app.route("/v1/answers", methods=["POST"])
def create_answer():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        body = {}
    question = (body.get("question") or "").strip()
    raw_documents = body.get("documents")
    include_sources = json_bool(body.get("include_sources"))

    if not question:
        return _bad_request("`question` is required")
    try:
        validate_question_length(question)
    except ValueError as exc:
        return _bad_request(str(exc))
    if not isinstance(raw_documents, list) or not raw_documents:
        return _bad_request("`documents` must be a non-empty array of strings")
    if len(raw_documents) > MAX_DOCUMENTS:
        return _bad_request(f"`documents` exceeds the {MAX_DOCUMENTS}-document limit")

    try:
        documents = build_documents(raw_documents)
    except ValueError as exc:
        return _bad_request(str(exc))

    try:
        polish_config = build_polish_config(body)
    except ValueError as exc:
        return _bad_request(str(exc))

    result = maybe_answer(question, documents)

    polished_answer, polish_state = None, None
    if result is not None and polish_config is not None:
        if result.state == "RESOLVED":
            polished_answer, polish_state = run_polish(question, result, polish_config)
        else:
            polish_state = "skipped_abstained"

    answer_id, created = new_answer_id_and_timestamp()
    store_answer(answer_id, (result, created, polished_answer, polish_state))

    return jsonify(serialize(answer_id, created, result, include_sources,
                             polished_answer, polish_state)), 201


@app.route("/v1/answers/<answer_id>", methods=["GET"])
def get_answer(answer_id: str):
    entry = load_answer(answer_id)
    if entry is None:
        return jsonify({"error": {"message": f"no answer found with id '{answer_id}'",
                                   "type": "not_found_error"}}), 404
    result, created, polished_answer, polish_state = entry
    return jsonify(serialize(answer_id, created, result, _include_sources_from_query(),
                             polished_answer, polish_state))


if __name__ == "__main__":
    from machine_auth import credentials_path
    print(f"Bearer token (also saved at {credentials_path()}): {CREDENTIALS['token']}")
    app.run(host="127.0.0.1", port=8420, debug=False, threaded=True)
