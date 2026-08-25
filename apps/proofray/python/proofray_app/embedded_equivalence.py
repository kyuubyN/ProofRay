from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Mapping

from .memory_service import ConversationMemoryService


SCHEMA = "proofray.app.embedded-equivalence-result.v1"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def run_frozen_cases(path: Path | None = None) -> dict[str, object]:
    cases_path = path or Path(__file__).with_name("frozen_equivalence_cases.json")
    manifest = json.loads(cases_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "proofray.app.embedded-equivalence-cases.v1":
        raise ValueError("embedded equivalence case schema mismatch")
    raw_cases = manifest.get("cases")
    if not isinstance(raw_cases, list) or len(raw_cases) != 20:
        raise ValueError("embedded equivalence suite must contain exactly 20 cases")
    results = []
    for case in raw_cases:
        if not isinstance(case, Mapping):
            raise ValueError("embedded equivalence case must be an object")
        service = ConversationMemoryService(
            profile_name=str(case["profile"]),
            timezone_name=str(case["timezone"]),
        )
        conversation_id = ""
        for document in case["documents"]:
            conversation_id, message_id, text, sequence, timestamp = document
            service.remember_user_message(
                conversation_id=conversation_id,
                message_id=message_id,
                text=text,
                sequence=sequence,
                timestamp=datetime.fromisoformat(timestamp),
            )
        memory = service._field
        if memory is None:
            raise RuntimeError("frozen case produced no authoritative memory")
        question = str(case["question"])
        raw = memory.answer_excluding_sources(question, ())
        if raw is None:
            raise RuntimeError("frozen case unexpectedly has no source snapshot")
        reply = service.answer_prior(conversation_id, question)
        results.append({
            "id": case["id"],
            "state": raw.state,
            "direct_state": raw.direct_answer.state,
            "claim_ranking_fact_ids": [item.fact_id for item in raw.claims],
            "answer_line_fact_ids": [item.fact_id for item in raw.answer_lines],
            "authority": reply.authority,
            "answer": reply.text,
            "certified_text": reply.certified_text,
            "certificate_hex": reply.certificate_hex,
            "proof_method": reply.proof_method,
            "sources": list(reply.sources),
            "documents_considered": reply.documents_considered,
            "verified_candidates": reply.verified_candidates,
            "answer_bytes": reply.answer_bytes,
        })
    payload = {"schema": SCHEMA, "cases": results}
    payload["result_sha256"] = hashlib.sha256(_canonical(payload)).hexdigest()
    return payload


def verify_desktop_reference(path: Path | None = None) -> dict[str, object]:
    """Require the complete canonical result to match the frozen desktop run."""
    cases_path = path or Path(__file__).with_name("frozen_equivalence_cases.json")
    manifest = json.loads(cases_path.read_text(encoding="utf-8"))
    expected = manifest.get("desktop_result_sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        raise ValueError("embedded equivalence desktop reference is absent")
    result = run_frozen_cases(cases_path)
    if result["result_sha256"] != expected:
        raise RuntimeError("embedded equivalence differs from frozen desktop result")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--verify-desktop", action="store_true")
    arguments = parser.parse_args()
    result = verify_desktop_reference() if arguments.verify_desktop else run_frozen_cases()
    encoded = _canonical(result) + b"\n"
    if arguments.write is not None:
        arguments.write.write_bytes(encoded)
    if arguments.compare is not None:
        expected = arguments.compare.read_bytes()
        if expected != encoded:
            raise SystemExit("embedded equivalence mismatch")
    if arguments.write is None and arguments.compare is None and not arguments.verify_desktop:
        print(encoded.decode("utf-8"), end="")


if __name__ == "__main__":
    main()
