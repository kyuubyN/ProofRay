# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path

from horizon_memory import HorizonAnswerEngine


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "HorizonAI Engine/examples/mongodb_documents_example.py"


def _load_example():
    spec = importlib.util.spec_from_file_location("mongodb_documents_example", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Cursor(list):
    def sort(self, field, direction):
        assert field == "_id" and direction == 1
        return _Cursor(sorted(self, key=lambda item: str(item[field])))


class _Collection:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query, projection):
        assert query == {} and projection == {"_id": 1, "body": 1}
        return _Cursor(self.rows)


def test_mongodb_projection_has_stable_factids_and_answers_through_real_engine():
    module = _load_example()
    rows = [
        {"_id": "mongo-b", "body": "Unrelated Solstice latency notes."},
        {"_id": "mongo-a", "body": (
            "The Meridian project reduced compute cost by exactly 42 percent compared "
            "to the previous baseline architecture across every workload.")},
    ]
    documents = module._documents_from_mongo(_Collection(rows))
    assert tuple(document.source for document in documents) == (
        "articles:mongo-a", "articles:mongo-b")
    assert documents[0].fact_id == int.from_bytes(
        hashlib.sha256(b"mongo-a").digest()[:8], "big") & ((1 << 62) - 1)
    assert module._documents_from_mongo(_Collection(rows)) == documents

    result = HorizonAnswerEngine(
        scope_id=module.SCOPE_ID, session_id=module.SESSION_ID).answer(
            "What percent did the Meridian project reduce cost by?", documents)
    assert result.resolved and "42" in result.final_answer_text
