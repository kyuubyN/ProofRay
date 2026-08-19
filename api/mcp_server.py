# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""HorizonAPI over MCP -- connect a chat client (Claude Desktop, Cursor, any MCP client)
directly to the deterministic `HorizonAnswerEngine`, exposing one tool: `horizon_ask`.

Same request/response contract as `POST /v1/answers` in `server.py`, same shared logic via
`_engine_bridge.py` -- one implementation behind two transports (HTTP and MCP), not two
drifting copies. AGPL, not the `Apache-2.0 OR AGPL-3.0-or-later` carve-out reserved for
`src/horizon_memory/adapters/`: this is a transport, structurally the same role as `server.py`,
not a model-reader integration boundary (see `LICENSE_POLICY.md`).

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
    build_documents, build_polish_config, new_answer_id_and_timestamp, run_polish, serialize,
    ENGINE, MAX_DOCUMENTS,
)
from mcp.server.mcpserver import MCPServer


def _horizon_ask_impl(question: str, documents: list[str], include_sources: bool = False,
                       polish: bool = False, polish_model: str | None = None,
                       polish_base_url: str | None = None,
                       polish_api_key_env: str | None = None) -> dict:
    """The plain-Python implementation behind the `horizon_ask` tool -- kept separate from the
    `@mcp.tool()` decoration so it can be unit-tested directly, without any MCP transport."""
    if not question or not question.strip():
        raise ValueError("`question` is required")
    if not documents:
        raise ValueError("`documents` must be a non-empty array of strings")
    if len(documents) > MAX_DOCUMENTS:
        raise ValueError(f"`documents` exceeds the {MAX_DOCUMENTS}-document limit")

    doc_tuple = build_documents(documents)

    body = {"polish": polish, "polish_model": polish_model,
            "polish_base_url": polish_base_url, "polish_api_key_env": polish_api_key_env}
    polish_config = build_polish_config(body)

    result = ENGINE.answer(question, doc_tuple)

    polished_answer, polish_state = None, None
    if polish_config is not None:
        if result.state == "RESOLVED":
            polished_answer, polish_state = run_polish(question, result, polish_config)
        else:
            polish_state = "skipped_abstained"

    answer_id, created = new_answer_id_and_timestamp()
    return serialize(answer_id, created, result, include_sources, polished_answer, polish_state)


mcp = MCPServer("horizon-memory")


@mcp.tool()
def horizon_ask(question: str, documents: list[str], include_sources: bool = False,
                 polish: bool = False, polish_model: str | None = None,
                 polish_base_url: str | None = None,
                 polish_api_key_env: str | None = None) -> dict:
    """Ask Horizon a question over a caller-supplied document set. Returns a deterministic,
    verified answer (`sources: null` unless `include_sources` is set). Pass `polish: true` with
    `polish_model` to also get `polished_answer`: the same answer rewritten for fluency by an
    OpenAI-compatible model (`polish_base_url` for a local/alternate endpoint, `polish_api_key_env`
    for the name of an environment variable holding an API key -- never pass a literal key)."""
    return _horizon_ask_impl(question, documents, include_sources, polish, polish_model,
                              polish_base_url, polish_api_key_env)


if __name__ == "__main__":
    mcp.run(transport="stdio")
