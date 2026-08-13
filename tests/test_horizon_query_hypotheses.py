# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import json

import pytest

from horizon_memory.query_hypotheses import (
    PredicateSchema, generate_query_hypotheses,
)


SCHEMAS = (
    PredicateSchema("deploy", (("agent", ("Bruno",)), ("patient", ("server",)))),
    PredicateSchema("visit", (("agent", ("Ana",)), ("location", ("Porto", "Recife")))),
)


def _programs(question):
    return [json.loads(item.payload) for item in generate_query_hypotheses(question, SCHEMAS)
            if item.payload is not None]


def test_gold_free_population_contains_three_required_programs_from_schema_only():
    count = _programs("How many places did Ana visit?")
    assert {"operator": "count_distinct", "predicate": "visit",
            "constraints": [{"field": "role:agent", "value": "Ana"}],
            "distinct_by": "role:location", "require_complete": True} in count

    deploy = _programs("Who deployed the server?")
    assert {"operator": "project", "predicate": "deploy",
            "constraints": [{"field": "role:patient", "value": "server"}],
            "project": "role:agent"} in deploy

    latest = _programs("Where did Ana visit most recently?")
    assert {"operator": "argmax", "predicate": "visit",
            "constraints": [{"field": "role:agent", "value": "Ana"}],
            "project": "role:location", "clock": "event_time",
            "require_complete": True} in latest


def test_population_has_uniform_charges_unique_payloads_and_explicit_unsupported():
    hypotheses = generate_query_hypotheses("Did Ana visit Porto?", SCHEMAS)
    assert sum(item.payload is None for item in hypotheses) == 1
    assert len({item.payload for item in hypotheses}) == len(hypotheses)
    assert len({tuple(key for key, _ in item.charges) for item in hypotheses}) == 1


def test_population_limit_and_schema_canonicality_fail_closed():
    with pytest.raises(ValueError, match="exceeds"):
        generate_query_hypotheses("Did Ana visit?", SCHEMAS, max_hypotheses=2)
    with pytest.raises(ValueError, match="predicate-sorted"):
        generate_query_hypotheses("Did Ana visit?", tuple(reversed(SCHEMAS)))
