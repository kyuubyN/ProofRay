# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: Apache-2.0 OR AGPL-3.0-or-later
"""Polish a ProofRay answer with a HOSTED API model (Groq, OpenAI -- anything exposing an
OpenAI-compatible `/chat/completions` endpoint).

Identical adapter and code path to `local_model_polish_example.py` -- only `base_url` and
`api_key_env` differ. The API key itself is NEVER passed as a literal string anywhere in this
file: `api_key_env` names an environment variable, read only at call time by the adapter.

This directly exercises `src/horizon_memory/adapters/openai_compatible.py`, the dual-licensed
integration-boundary code -- SPDX header matches that module's own (see `LICENSE_POLICY.md`).

Safe to run as-is: `allow_network=False` by default means this prints a dry-run result and makes
no network call, no key required. To actually call Groq, `export GROQ_KEY=...` in your shell and
flip `ALLOW_NETWORK = True` below.

Run: python3 "ProofRay Engine/examples/api_model_polish_example.py"
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from horizon_memory.adapters import OpenAICompatiblePolishAdapter, PolishConfig

ALLOW_NETWORK = False  # flip to True once GROQ_KEY is exported in your environment

QUESTION = "What percent did the Meridian project reduce cost by?"
ANSWER_TEXT = (
    "The Meridian project reduced compute cost by exactly 42 percent compared to the previous "
    "baseline architecture across every workload. Meridian's cost reduction came from a "
    "redesigned caching layer that eliminated redundant recomputation across adjacent pipeline "
    "stages.")


def main() -> None:
    adapter = OpenAICompatiblePolishAdapter(allow_network=ALLOW_NETWORK)
    config = PolishConfig(
        model="qwen/qwen3.6-27b",
        base_url="https://api.groq.com/openai/v1/chat/completions",  # the PolishConfig default
        api_key_env="GROQ_KEY",  # the name of the env var, never the key itself
    )
    result = adapter.polish(QUESTION, ANSWER_TEXT, config)

    print("state:", result.state)
    if result.state == "dry_run":
        print("(allow_network=False -- no request was sent; export GROQ_KEY and flip "
              "ALLOW_NETWORK to try a real call)")
    elif result.state == "error":
        print("error_code:", result.error_code)
    else:
        print("polished text:", result.text)


if __name__ == "__main__":
    main()
