# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import hashlib
import json

import pytest

from horizon_memory.event_compiler import (
    SourceAuthority, decode_event_batch, decode_event_proposal, decode_query_proposal,
)


def test_model_cannot_supply_authority_and_span_is_reopened():
    content = "Ana walked 3 miles yesterday."
    proposal = json.dumps({
        "event_id": "walk-1", "predicate": "walk", "roles": {"agent": "Ana"},
        "evidence": content, "quantities": [{"kind": "distance", "value": 3,
                                                    "unit": "miles"}],
    })
    event = decode_event_proposal(proposal, SourceAuthority("private-scope", 91, content))
    assert event.scope == "private-scope" and event.fact_id == 91
    assert event.parent_sha256 == hashlib.sha256(content.encode()).hexdigest()
    assert event.exact_span == (0, len(content))
    assert event.event_id == "91:walk-1"


def test_forged_quantity_and_unknown_authority_keys_fail_closed():
    authority = SourceAuthority("s", 1, "Ana walked 3 miles.")
    forged = json.dumps({
        "event_id": "e", "predicate": "walk", "roles": {"agent": "Ana"},
        "evidence": "Ana walked 3 miles.",
        "quantities": [{"kind": "distance", "value": 30, "unit": "miles"}],
    })
    with pytest.raises(ValueError, match="not present"):
        decode_event_proposal(forged, authority)
    with pytest.raises(ValueError, match="unknown event keys"):
        decode_event_proposal(json.dumps({
            "event_id": "e", "predicate": "walk", "roles": {"agent": "Ana"},
            "evidence": "Ana walked 3 miles.", "fact_id": 999,
        }), authority)
    with pytest.raises(ValueError, match="unknown event keys"):
        decode_event_proposal(json.dumps({
            "event_id": "e", "predicate": "walk", "roles": {"agent": "Ana"},
            "evidence": "Ana walked 3 miles.", "event_time": 999,
        }), authority)
    with pytest.raises(ValueError, match="role entity"):
        decode_event_proposal(json.dumps({
            "event_id": "e", "predicate": "walk", "roles": {"agent": "Bob"},
            "evidence": "Ana walked 3 miles.",
        }), authority)


def test_duplicate_json_keys_and_non_unique_quote_fail():
    authority = SourceAuthority("s", 1, "abc")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        decode_event_proposal(
            '{"event_id":"a","event_id":"b","predicate":"p","roles":{"agent":"a"},"evidence":"a"}',
            authority)
    with pytest.raises(ValueError, match="exactly once"):
        decode_event_proposal(json.dumps({
            "event_id": "e", "predicate": "p", "roles": {"agent": "a"}, "evidence": "z",
        }), authority)
    repeated = SourceAuthority("s", 2, "Ana walked. Ana walked.")
    with pytest.raises(ValueError, match="exactly once"):
        decode_event_proposal(json.dumps({
            "event_id": "e", "predicate": "walk", "roles": {"agent": "Ana"},
            "evidence": "Ana walked.",
        }), repeated)


def test_query_scope_is_authoritative_and_program_is_canonical():
    proposal = json.dumps({
        "operator": "count_distinct", "predicate": "visit", "distinct_by": "role:place",
        "constraints": [{"field": "role:actor", "value": "ana"}],
        "require_complete": True,
    })
    program = decode_query_proposal(proposal, "scope-from-session")
    assert program.scope == "scope-from-session"
    assert len(program.canonical_sha256()) == 64
    with pytest.raises(ValueError, match="unknown program keys"):
        decode_query_proposal(json.dumps({
            "operator": "exists", "predicate": "visit", "answer": "yes",
        }), "s")


def test_explicit_fail_closed_sentinels_and_bounded_event_batch():
    authority = SourceAuthority("s", 1, "Ana walked. Bob ran.")
    assert decode_event_proposal('{"state":"abstain"}', authority) is None
    assert decode_event_batch('{"state":"abstain"}', authority) == ()
    assert decode_query_proposal('{"state":"unsupported"}', "s") is None
    batch = json.dumps({"events": [
        {"event_id": "e1", "predicate": "walk", "roles": {"agent": "Ana"},
         "evidence": "Ana walked."},
        {"event_id": "e2", "predicate": "run", "roles": {"agent": "Bob"},
         "evidence": "Bob ran."},
    ]})
    assert [event.event_id for event in decode_event_batch(batch, authority)] == ["1:e1", "1:e2"]
    with pytest.raises(ValueError, match="event_ids"):
        decode_event_batch(json.dumps({"events": [
            {"event_id": "e", "predicate": "walk", "roles": {"agent": "Ana"},
             "evidence": "Ana walked."},
            {"event_id": "e", "predicate": "run", "roles": {"agent": "Bob"},
             "evidence": "Bob ran."},
        ]}), authority)
