# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
import hashlib

from horizon_memory.authorized_fiber_search import AuthorizedFiberSearchEngine
from horizon_memory.raw_causal_channels import RawCausalDocument
from horizon_memory.typed_causal_program import TypedCausalFact


def _fact(fact_id, subject, predicate, value):
    return TypedCausalFact(
        fact_id, "scope", subject, predicate, value, 1, 1,
        event_id=f"e:{fact_id}", source_id=f"s:{fact_id}",
        source_sha256=hashlib.sha256(value.encode()).hexdigest(),
        source_span=(0, len(value)))


def _engine(extra=0):
    facts = [_fact(1, "CommitABC123", "author", "Ada"),
             _fact(2, "CommitABC123", "changed_file", "a.py")]
    docs = [RawCausalDocument(1, "CommitABC123 author Ada", 0, 0),
            RawCausalDocument(2, "CommitABC123 changed file a.py", 0, 1)]
    for offset in range(extra):
        fact_id = offset + 3
        subject = f"CommitZZZ{offset}"
        facts.append(_fact(fact_id, subject, "changed_file", f"x{offset}.py"))
        docs.append(RawCausalDocument(
            fact_id, f"{subject} changed file x{offset}.py", 0, fact_id))
    return AuthorizedFiberSearchEngine(tuple(docs), tuple(facts))


def test_exact_subject_routes_only_its_fiber_and_predicate_first():
    engine = _engine(100)
    route = engine.route("How many changed files did CommitABC123 record?")
    assert route.state == "routed" and route.subject == "CommitABC123"
    assert route.candidate_fact_ids == (2, 1) and route.inspected_fact_ids == 2


def test_unrelated_history_does_not_change_route_work_or_result():
    query = "What author does CommitABC123 have?"
    small, large = _engine(), _engine(100)
    assert small.route(query) == large.route(query)
    assert small.search(query).fact_ids == large.search(query).fact_ids == (1, 2)


def test_missing_exact_identity_abstains_without_fuzzy_fallback():
    route = _engine().route("What author does an unknown commit have?")
    assert route.state == "abstain" and route.candidate_fact_ids == ()


def test_literal_identifier_survives_morphological_suffix_collision():
    fact = _fact(1, "CommitF3AF698D1DED", "author", "Ada")
    doc = RawCausalDocument(1, "CommitF3AF698D1DED author Ada", 0, 0)
    route = AuthorizedFiberSearchEngine((doc,), (fact,)).route(
        "What author does CommitF3AF698D1DED have?")
    assert route.state == "routed" and route.subject == "CommitF3AF698D1DED"
