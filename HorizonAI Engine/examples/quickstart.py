# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Quickstart: the minimum code to ask Horizon a question.

This example calls `HorizonAnswerEngine` (the AGPL core) directly, so it carries an AGPL header
honestly, matching what it actually invokes -- see `../LICENSE_COMMERCIAL_PLACEHOLDER.md`.

Run: python3 "HorizonAI Engine/examples/quickstart.py"
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from horizon_memory import DEFAULT_PROFILE, HorizonAnswerEngine, RouteDocument

SCOPE_ID = 1
SESSION_ID = "quickstart"


def main() -> None:
    documents = (
        RouteDocument(1, "The Meridian project reduced compute cost by exactly 42 percent "
                         "compared to the previous baseline architecture across every "
                         "workload.", SCOPE_ID, SESSION_ID, 1, "doc:1"),
        RouteDocument(2, "Meridian's cost reduction came from a redesigned caching layer that "
                         "eliminated redundant recomputation across adjacent pipeline stages.",
                      SCOPE_ID, SESSION_ID, 1, "doc:2"),
    )

    engine = HorizonAnswerEngine(profile=DEFAULT_PROFILE, scope_id=SCOPE_ID, session_id=SESSION_ID)
    result = engine.answer("What percent did the Meridian project reduce cost by?", documents)

    print("state:", result.state)
    print("answer:", result.answer_text)
    print(f"({result.verified_candidates} verified claims out of "
          f"{result.documents_considered} documents considered)")


if __name__ == "__main__":
    main()
