# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Use Gemini only after Horizon has consulted MongoDB and verified its memory.

Two modes are demonstrated:

``polish``
    Horizon queries MongoDB first. Gemini receives only Horizon's verified result and may rewrite
    it for presentation. The database corpus never leaves the process.

``tool``
    Gemini receives the user's question and must call ``query_horizon_memory``. The application
    executes that tool locally against MongoDB/Horizon, returns only its verified result, and lets
    Gemini write the final response.

With no ``MONGODB_URI``, the sibling MongoDB example uses an in-process ``mongomock`` fixture. Set
the variable to query a real MongoDB deployment. Gemini credentials are read only from
``GEMINI_API_KEY`` and sent in the ``x-goog-api-key`` header, never in a URL or prompt.

Run from the repository root:

    python3 "HorizonAI Engine/examples/gemini_horizon_tool_call.py" --mode polish
    python3 "HorizonAI Engine/examples/gemini_horizon_tool_call.py" --mode tool
    python3 "HorizonAI Engine/examples/gemini_horizon_tool_call.py" --mode both
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from typing import Callable
import urllib.error
import urllib.request


HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mongodb_documents_example as mongo  # noqa: E402


DEFAULT_QUESTION = "What percent did the Meridian project reduce cost by?"
DEFAULT_MODEL = "gemini-3.1-flash-lite"
GeminiGenerate = Callable[[dict], dict]
HorizonTool = Callable[[str], dict]


def query_horizon_memory(question: str) -> dict:
    """Execute the only authority-bearing step locally against MongoDB and Horizon."""
    collection, is_mock = mongo._get_collection()
    documents = mongo._documents_from_mongo(collection)
    engine = mongo.HorizonAnswerEngine(
        profile=mongo.DEFAULT_PROFILE,
        scope_id=mongo.SCOPE_ID,
        session_id=mongo.SESSION_ID,
    )
    result = engine.answer(question, documents)
    if not result.resolved or not result.final_answer_text.strip():
        return {
            "state": "abstain",
            "authority": "none",
            "answer": "",
            "backend": "mongomock" if is_mock else "mongodb",
        }
    direct = result.direct_answer
    authority = (
        "direct_proof" if direct.state == "resolved" and direct.proof_closed
        else "verified_evidence"
    )
    return {
        "state": "resolved",
        "authority": authority,
        "answer": result.final_answer_text,
        "backend": "mongomock" if is_mock else "mongodb",
        "source_count": len(result.sources),
    }


def _response_parts(response: dict) -> list[dict]:
    try:
        return list(response["candidates"][0]["content"]["parts"])
    except (IndexError, KeyError, TypeError) as exc:
        raise RuntimeError("Gemini returned an invalid response schema") from exc


def _response_text(response: dict) -> str:
    return "".join(part.get("text", "") for part in _response_parts(response)).strip()


def _tool_declaration() -> list[dict]:
    return [{"functionDeclarations": [{
        "name": "query_horizon_memory",
        "description": (
            "Query the local proof-carrying Horizon memory. Call this before answering a factual "
            "memory question."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {"question": {"type": "STRING"}},
            "required": ["question"],
        },
    }]}]


_SYSTEM = {
    "parts": [{"text": (
        "You are only a presentation layer and have no memory authority. Use only the result from "
        "query_horizon_memory. If it returns state=abstain, say there is not enough verified "
        "memory. Never add or change a fact or number."
    )}]
}


def run_polish(question: str, horizon: dict, generate: GeminiGenerate) -> str:
    """One model call: rewrite only the already-verified Horizon result."""
    payload = {
        "systemInstruction": {"parts": [{"text": (
            "You are only a presentation layer. Rewrite the verified Horizon result into one "
            "concise answer. Do not add, infer, calculate, or change any fact or number. If its "
            "state is abstain, answer that there is not enough verified memory."
        )}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps({
            "question": question,
            "horizon_state": horizon["state"],
            "horizon_authority": horizon["authority"],
            "horizon_answer": horizon["answer"],
        }, ensure_ascii=False)}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 256},
    }
    return _response_text(generate(payload))


def run_tool_call(question: str, horizon_tool: HorizonTool,
                  generate: GeminiGenerate) -> dict:
    """Two model calls: request one native function call, execute it locally, then render."""
    tools = _tool_declaration()
    initial_contents = [{"role": "user", "parts": [{"text": question}]}]
    first = generate({
        "systemInstruction": _SYSTEM,
        "contents": initial_contents,
        "tools": tools,
        "toolConfig": {"functionCallingConfig": {
            "mode": "ANY",
            "allowedFunctionNames": ["query_horizon_memory"],
        }},
        "generationConfig": {"temperature": 0, "maxOutputTokens": 256},
    })
    calls = [part["functionCall"] for part in _response_parts(first)
             if "functionCall" in part]
    if len(calls) != 1 or calls[0].get("name") != "query_horizon_memory":
        raise RuntimeError("Gemini did not request exactly one Horizon tool call")
    tool_question = calls[0].get("args", {}).get("question")
    if not isinstance(tool_question, str) or not tool_question.strip():
        raise RuntimeError("Gemini supplied an invalid Horizon tool question")
    horizon = horizon_tool(tool_question)
    model_content = first["candidates"][0]["content"]
    second = generate({
        "systemInstruction": _SYSTEM,
        "contents": initial_contents + [
            model_content,
            {"role": "user", "parts": [{"functionResponse": {
                "name": "query_horizon_memory",
                "response": horizon,
            }}]},
        ],
        "tools": tools,
        # Once the authority result exists, require presentation instead of another tool loop.
        "toolConfig": {"functionCallingConfig": {"mode": "NONE"}},
        "generationConfig": {"temperature": 0, "maxOutputTokens": 256},
    })
    return {
        "tool_question": tool_question,
        "horizon_state": horizon["state"],
        "horizon_authority": horizon["authority"],
        "answer": _response_text(second),
    }


class GeminiREST:
    """Minimal Gemini Developer API transport; counts generateContent calls explicitly."""

    def __init__(self, *, model: str):
        if any(tag in model.casefold() for tag in ("latest", "preview", "experimental", "-exp")):
            raise ValueError("pin a specific non-preview Gemini model")
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is required")
        self._key = key
        self.model = model
        self.call_count = 0

    def generate(self, payload: dict) -> dict:
        endpoint = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )
        request = urllib.request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(),
            method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": self._key},
        )
        self.call_count += 1
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            # Do not echo headers, request bytes, credentials or provider body.
            exc.read()
            raise RuntimeError(f"Gemini HTTP {exc.code}") from None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("polish", "tool", "both"), default="both")
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--model", default=os.environ.get("GEMINI_MODEL", DEFAULT_MODEL))
    args = parser.parse_args()
    transport = GeminiREST(model=args.model)
    output = {"question": args.question, "model": args.model}
    if args.mode in ("polish", "both"):
        horizon = query_horizon_memory(args.question)
        output["horizon"] = {
            key: value for key, value in horizon.items() if key != "answer"}
        output["polished_answer"] = run_polish(
            args.question, horizon, transport.generate)
    if args.mode in ("tool", "both"):
        output["tool_call"] = run_tool_call(
            args.question, query_horizon_memory, transport.generate)
    output["gemini_generate_content_calls"] = transport.call_count
    print(json.dumps(output, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
