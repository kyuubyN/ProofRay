# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Launches the MCP server (`api/mcp_server.py`) from this folder -- for a chat client's
`mcpServers` config to point `command`/`args` at directly. This file just imports and runs
`api/mcp_server.py`'s already-built `mcp` object in-process; it carries an AGPL header, honestly,
because that is exactly what it executes (see this folder's own
`LICENSE_COMMERCIAL_PLACEHOLDER.md` for why that's stated explicitly rather than left implicit).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

from mcp_server import mcp  # noqa: E402

if __name__ == "__main__":
    mcp.run(transport="stdio")
