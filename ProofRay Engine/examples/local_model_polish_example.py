# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: Apache-2.0 OR AGPL-3.0-or-later
"""Polish a ProofRay answer with a LOCAL model server (Ollama, llama.cpp's server, vLLM, LM
Studio -- anything exposing an OpenAI-compatible `/chat/completions` endpoint).

Same `OpenAICompatiblePolishAdapter` as the hosted-API example -- only `base_url` and
`api_key_env` differ. Local servers are usually unauthenticated, so `api_key_env=None` (the
default) sends no `Authorization` header at all.

This directly exercises `src/horizon_memory/adapters/openai_compatible.py`, the dual-licensed
integration-boundary code -- SPDX header matches that module's own (see `LICENSE_POLICY.md`).

Safe to run as-is: `allow_network=False` by default means this prints a dry-run result and makes
no network call. To actually call a running local server, start one (e.g. `ollama serve`, which
exposes an OpenAI-compatible endpoint at `http://localhost:11434/v1/chat/completions`) and flip
`ALLOW_NETWORK = True` below.

Run: python3 "ProofRay Engine/examples/local_model_polish_example.py"
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from horizon_memory.adapters import OpenAICompatiblePolishAdapter, PolishConfig

ALLOW_NETWORK = False  # flip to True once a local OpenAI-compatible server is actually running

QUESTION = "What percent did the Meridian project reduce cost by?"
ANSWER_TEXT = (
    "The Meridian project reduced compute cost by exactly 42 percent compared to the previous "
    "baseline architecture across every workload. Meridian's cost reduction came from a "
    "redesigned caching layer that eliminated redundant recomputation across adjacent pipeline "
    "stages.")


def main() -> None:
    adapter = OpenAICompatiblePolishAdapter(allow_network=ALLOW_NETWORK)
    config = PolishConfig(
        model="llama3.1",  # whatever model name your local server serves under
        base_url="http://localhost:11434/v1/chat/completions",  # Ollama's OpenAI-compatible route
        api_key_env=None,  # no key for an unauthenticated local server
        reasoning_effort=None,  # most local models don't understand this Groq-specific field
    )
    result = adapter.polish(QUESTION, ANSWER_TEXT, config)

    print("state:", result.state)
    if result.state == "dry_run":
        print("(allow_network=False -- no request was sent; flip ALLOW_NETWORK to try a real "
              "local server)")
    elif result.state == "error":
        print("error_code:", result.error_code)
    else:
        print("polished text:", result.text)


if __name__ == "__main__":
    main()
