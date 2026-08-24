from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "HorizonAI Engine/examples/gemini_horizon_tool_call.py"


def _load_example():
    spec = importlib.util.spec_from_file_location("gemini_horizon_tool_call", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _text_response(text: str) -> dict:
    return {"candidates": [{
        "content": {"role": "model", "parts": [{"text": text}]},
    }]}


class _Cursor(list):
    def sort(self, field, direction):
        assert (field, direction) == ("_id", 1)
        return _Cursor(sorted(self, key=lambda item: str(item[field])))


class _Collection:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query, projection):
        assert query == {}
        assert projection == {"_id": 1, "body": 1}
        return _Cursor(self.rows)


def test_polish_sends_only_horizon_result_and_uses_one_model_call():
    module = _load_example()
    payloads = []

    def generate(payload):
        payloads.append(payload)
        return _text_response("Meridian reduced compute cost by 42 percent.")

    horizon = {
        "state": "resolved", "authority": "verified_evidence",
        "answer": "Meridian reduced compute cost by exactly 42 percent.",
        "backend": "mongomock", "source_count": 3,
    }
    output = module.run_polish("What was the reduction?", horizon, generate)
    assert output == "Meridian reduced compute cost by 42 percent."
    assert len(payloads) == 1
    encoded = json.dumps(payloads)
    assert horizon["answer"] in encoded
    assert "Solstice" not in encoded
    assert "source_count" not in encoded


def test_native_tool_cycle_calls_horizon_locally_then_renders_without_tool_loop():
    module = _load_example()
    question = "What percent did Meridian reduce cost by?"
    payloads = []
    tool_questions = []

    def generate(payload):
        payloads.append(payload)
        if len(payloads) == 1:
            return {"candidates": [{"content": {
                "role": "model",
                "parts": [{"functionCall": {
                    "name": "query_horizon_memory",
                    "args": {"question": question},
                }}],
            }}]}
        return _text_response("Meridian reduced compute cost by 42 percent.")

    def horizon_tool(value):
        tool_questions.append(value)
        return {
            "state": "resolved", "authority": "verified_evidence",
            "answer": "Meridian reduced compute cost by exactly 42 percent.",
            "backend": "mongomock", "source_count": 3,
        }

    output = module.run_tool_call(question, horizon_tool, generate)
    assert len(payloads) == 2
    assert tool_questions == [question]
    assert output == {
        "tool_question": question,
        "horizon_state": "resolved",
        "horizon_authority": "verified_evidence",
        "answer": "Meridian reduced compute cost by 42 percent.",
    }
    assert payloads[0]["toolConfig"]["functionCallingConfig"]["mode"] == "ANY"
    assert payloads[1]["toolConfig"]["functionCallingConfig"]["mode"] == "NONE"
    assert "functionResponse" in payloads[1]["contents"][2]["parts"][0]
    assert "Solstice" not in json.dumps(payloads)


def test_local_mongo_tool_returns_verified_horizon_evidence_without_optional_dependency(
        monkeypatch):
    module = _load_example()
    collection = _Collection([
        {"_id": "mongo-a", "body": (
            "The Meridian project reduced compute cost by exactly 42 percent compared "
            "to the previous baseline architecture across every workload.")},
        {"_id": "mongo-b", "body": (
            "Meridian's cost reduction came from a redesigned caching layer that eliminated "
            "redundant recomputation across adjacent pipeline stages.")},
        {"_id": "mongo-c", "body": (
            "The Solstice project is unrelated to Meridian and focuses on latency.")},
    ])
    monkeypatch.setattr(module.mongo, "_get_collection", lambda: (collection, True))
    result = module.query_horizon_memory(module.DEFAULT_QUESTION)
    assert result["state"] == "resolved"
    assert result["authority"] in {"direct_proof", "verified_evidence"}
    assert "42" in result["answer"]
    assert result["backend"] == "mongomock"
    assert result["source_count"] == 3
