# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""HorizonAPI over MCP -- connect a chat client (Claude Desktop, Cursor, any MCP client)
directly to the deterministic `HorizonAnswerEngine`, exposing one tool: `horizon_ask`.

Same request/response contract as `POST /v1/answers` in `server.py`, same shared logic via
`_engine_bridge.py` -- one implementation behind two transports (HTTP and MCP), not two
drifting copies. AGPL, not the `Apache-2.0 OR AGPL-3.0-or-later` carve-out reserved for
`src/horizon_memory/adapters/`: this is a transport, structurally the same role as `server.py`,
not a model-reader integration boundary (see `LICENSE_POLICY.md`).

**This transport is also the recommended "activation mode"**: an orchestrating LLM agent already
decides for itself, from its own read of the conversation, when calling `horizon_ask` is relevant
-- no keyword list, no separate activation mechanism needed here, since the calling agent's own
judgment already IS the activation decision. The alternative mode (a small, closed, server-
configured trigger-phrase gate, for a deployment with no LLM making that call) lives in
`_engine_bridge.maybe_answer`/`ACTIVATION_MODE` and applies identically to both transports.

Uses the `mcp` package's `MCPServer` (`mcp.server.mcpserver`) -- note this is the current v2.0.0
API; older `mcp` releases (<2.0) exposed the same high-level decorator API under the name
`FastMCP` in `mcp.server.fastmcp` instead. If a different `mcp` version ends up pinned later,
re-check this import path against whatever's actually installed rather than assuming either name.

Run: `python3 api/mcp_server.py` (stdio transport, the standard shape for a local chat client to
launch as a subprocess). Or `mcp dev api/mcp_server.py` for the interactive inspector during
development (ships with the `mcp[cli]` extra).
"""
from __future__ import annotations

from _engine_bridge import (
    build_documents, build_polish_config, maybe_answer, new_answer_id_and_timestamp, run_polish,
    serialize, validate_question_length, MAX_DOCUMENTS,
)
from mcp.server.mcpserver import MCPServer


def _horizon_ask_impl(question: str, documents: list[str], include_sources: bool = False,
                       polish: bool = False, polish_model: str | None = None) -> dict:
    """The plain-Python implementation behind the `horizon_ask` tool -- kept separate from the
    `@mcp.tool()` decoration so it can be unit-tested directly, without any MCP transport."""
    if not question or not question.strip():
        raise ValueError("`question` is required")
    validate_question_length(question)
    if not documents:
        raise ValueError("`documents` must be a non-empty array of strings")
    if len(documents) > MAX_DOCUMENTS:
        raise ValueError(f"`documents` exceeds the {MAX_DOCUMENTS}-document limit")

    doc_tuple = build_documents(documents)

    body = {"polish": polish, "polish_model": polish_model}
    polish_config = build_polish_config(body)

    result = maybe_answer(question, doc_tuple)

    polished_answer, polish_state = None, None
    if result is not None and polish_config is not None:
        if result.state == "RESOLVED":
            polished_answer, polish_state = run_polish(question, result, polish_config)
        else:
            polish_state = "skipped_abstained"

    answer_id, created = new_answer_id_and_timestamp()
    return serialize(answer_id, created, result, include_sources, polished_answer, polish_state)


mcp = MCPServer("horizon-memory")


@mcp.tool()
async def horizon_ask(question: str, documents: list[str], include_sources: bool = False,
                      polish: bool = False, polish_model: str | None = None) -> dict:
    """Ask Horizon a question over a caller-supplied document set. Call this when the
    conversation genuinely calls for recalling something previously established -- deciding
    whether this moment warrants it is the calling agent's own judgment call; this tool does not
    second-guess that decision. Returns a deterministic, verified answer (`sources: null` unless
    `include_sources` is set), or `state: "not_activated"` if this deployment has its own
    server-side keyword gate enabled and the question didn't match it. Pass `polish: true` with
    `polish_model` to also get `polished_answer`: the same answer rewritten for fluency by an
    OpenAI-compatible model. The polish endpoint and API-key env var name are fixed by this
    server's own deployment config (HORIZON_POLISH_BASE_URL / HORIZON_POLISH_API_KEY_ENV), not
    caller-selectable -- letting a caller pick both let them redirect the server's outbound
    request and its stored credential to a host of their choosing."""
    # MCPServer offloads synchronous tools through AnyIO's worker-thread portal.  On the pinned
    # MCP/AnyIO + Python 3.14 combination that portal can wait forever even though the same plain
    # implementation already completed.  The operation is local CPU work, so expose an async tool
    # boundary and execute it directly on the server loop; stdio clients see the identical schema.
    return _horizon_ask_impl(question, documents, include_sources, polish, polish_model)


if __name__ == "__main__":
    mcp.run(transport="stdio")
