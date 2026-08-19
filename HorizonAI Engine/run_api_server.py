# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Launches the HTTP API (`api/server.py`) from this folder. Imports and runs `api/server.py`'s
already-built Flask `app` in-process -- see `run_mcp_server.py`'s own docstring for why this
launcher carries an honest AGPL header rather than the placeholder-commercial one.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api"))

from server import app  # noqa: E402

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8420, debug=False)
