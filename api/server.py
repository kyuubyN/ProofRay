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

Everything here is the same zero-LLM, zero-neural-net, deterministic pipeline
`HorizonAnswerEngine` already wraps -- this file only adds HTTP request/response plumbing on top
of `api/_engine_bridge.py`'s shared helpers (also used by `api/mcp_server.py`). See
`api/README.md` for the full request/response contract and the explicitly-deferred items (auth,
persistent storage, corpus reuse).
"""
from __future__ import annotations

from flask import Flask, jsonify, request

from _engine_bridge import (
    DEFAULT_PROFILE, ENGINE, MAX_DOCUMENTS, STORE, build_documents, build_polish_config,
    new_answer_id_and_timestamp, run_polish, serialize,
)

app = Flask(__name__)


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
    body = request.get_json(silent=True) or {}
    question = (body.get("question") or "").strip()
    raw_documents = body.get("documents")
    include_sources = bool(body.get("include_sources", False))

    if not question:
        return _bad_request("`question` is required")
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

    result = ENGINE.answer(question, documents)

    polished_answer, polish_state = None, None
    if polish_config is not None:
        if result.state == "RESOLVED":
            polished_answer, polish_state = run_polish(question, result, polish_config)
        else:
            polish_state = "skipped_abstained"

    answer_id, created = new_answer_id_and_timestamp()
    STORE[answer_id] = (result, created)

    return jsonify(serialize(answer_id, created, result, include_sources,
                             polished_answer, polish_state)), 201


@app.route("/v1/answers/<answer_id>", methods=["GET"])
def get_answer(answer_id: str):
    entry = STORE.get(answer_id)
    if entry is None:
        return jsonify({"error": {"message": f"no answer found with id '{answer_id}'",
                                   "type": "not_found_error"}}), 404
    result, created = entry
    return jsonify(serialize(answer_id, created, result, _include_sources_from_query()))


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8420, debug=False)
