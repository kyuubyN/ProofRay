from dataclasses import replace
import itertools

from horizon_memory.explanatory_obligation_proof import (
    ExplanatoryIntent,
    ExplanatoryProofConfig,
    ExplanatorySource,
    _candidates,
    _candidate_universe,
    _environment_bridges,
    _pool_contradictions,
    _sigma_environments,
    compile_obligation_graph,
    solve_explanatory_obligations,
)
from horizon_memory._eop_claims import ClaimSource, extract_authorized_claims


def _source(identifier: str, text: str, turn: int, *, root: str | None = None):
    return ExplanatorySource.seal(
        identifier, text, turn_index=turn, session_id="episode-a",
        source_role="document", root_id=root)


def _chain(*, duplicate: bool = False):
    sources = [
        _source("s0", "Alpha supports Beta through a verified relay.", 0),
        _source("s1", "Beta enables Gamma through a stable bridge.", 1),
        _source("s2", "Gamma improves Delta through exact calibration.", 2),
    ]
    if duplicate:
        sources.append(_source(
            "s0-copy", "Alpha supports Beta through a verified relay.", 0, root="s0"))
    intents = (
        ExplanatoryIntent.seal(
            "i0", "How does Alpha support Beta?", turn_index=0,
            source_ids=tuple(s.source_id for s in sources if s.turn_index == 0)),
        ExplanatoryIntent.seal(
            "i1", "How does Beta enable Gamma?", turn_index=1, source_ids=("s1",)),
        ExplanatoryIntent.seal(
            "i2", "How does Gamma improve Delta?", turn_index=2, source_ids=("s2",)),
    )
    question = "How does Gamma improve Delta?"
    return question, intents, tuple(sources)


def test_multihop_obligation_dag_resolves_minimum_exact_proof_and_reopens():
    question, intents, sources = _chain()
    result = solve_explanatory_obligations(
        question=question, intents=intents, sources=sources)
    assert result.state == "resolved"
    assert len(result.graph.nodes) == 4
    assert len(result.bridges) == 2
    assert len(result.closures) == 1
    assert result.closures[0].mode == "reuse"
    assert result.text.splitlines() == [source.text for source in sources]
    assert result.proof_bytes == len(result.text.encode("utf-8"))
    assert result.certificate.reopen(
        question=question, intents=intents, sources=sources)
    by_obligation = {item.obligation_id: item for item in result.bindings}
    assert all(bridge.left_claim_id == by_obligation[bridge.left_obligation_id].claim_id
               and bridge.right_claim_id == by_obligation[bridge.right_obligation_id].claim_id
               for bridge in result.bridges)
    assert result.certificate.closure_digest


def test_how_why_and_compare_reject_topical_nonrelational_witnesses():
    cases = (
        ("How does Alpha support Beta?", "Alpha and Beta appear in a general framework."),
        ("Why does Alpha improve Beta?", "Alpha and Beta appear in an improvement study."),
        ("How does Alpha compare with Beta?", "Alpha and Beta are popular systems."),
    )
    for question, text in cases:
        source = _source("s0", text, 0)
        intent = ExplanatoryIntent.seal(
            "i0", question, turn_index=0, source_ids=("s0",))
        result = solve_explanatory_obligations(
            question=question, intents=(intent,), sources=(source,))
        assert result.state == "unsupported", (question, result.state, result.residual)


def test_compare_gate_handles_possessive_complex_np_and_rejects_unsafe_variants():
    accepted_question = (
        "How does AdaGradAttack's adaptive step size strategy compare to M&P "
        "in processing speed?")
    accepted = _source(
        "accepted",
        "AdaGradAttack's adaptive step size is faster than M&P in processing speed.", 0)
    intent = ExplanatoryIntent.seal(
        "accepted-intent", accepted_question, turn_index=0, source_ids=("accepted",))
    assert solve_explanatory_obligations(
        question=accepted_question, intents=(intent,), sources=(accepted,)).state == "resolved"

    controls = (
        # Operand order is a role, not a bag of shared names.
        ("How does Alpha compare with Beta in speed?", "Beta is faster than Alpha in speed."),
        # A numerically plausible comparison with a different unit cannot pay the slot.
        ("How does Alpha compare with Beta in milliseconds?",
         "Alpha is faster than Beta by 8 seconds."),
        # Nominal topicality is not a directional comparison carrier.
        ("How does Alpha compare with Beta in speed?",
         "Alpha and Beta occur in a comparison study about speed."),
        # Shared entities and metric without a comparative relation remain unsupported.
        ("How does Alpha compare with Beta in speed?",
         "Alpha and Beta are systems evaluated for speed."),
    )
    for index, (question, text) in enumerate(controls):
        source = _source(f"control-{index}", text, 0)
        control_intent = ExplanatoryIntent.seal(
            f"control-intent-{index}", question, turn_index=0,
            source_ids=(source.source_id,))
        result = solve_explanatory_obligations(
            question=question, intents=(control_intent,), sources=(source,))
        assert result.state == "unsupported", (index, result.state, result.residual)


def test_explain_fallback_requires_directional_asserted_causal_carrier():
    accepted_question = "What architectural mechanism enables Alpha to improve Beta?"
    accepted = _source(
        "causal", "The relay architecture enables Alpha to improve Beta reliably.", 0)
    intent = ExplanatoryIntent.seal(
        "causal-intent", accepted_question, turn_index=0, source_ids=("causal",))
    assert solve_explanatory_obligations(
        question=accepted_question, intents=(intent,), sources=(accepted,)).state == "resolved"

    controls = (
        # Cause and effect roles are directional.
        ("Why does Alpha cause Beta?", "Beta causes Alpha under calibration."),
        # A nominal mention of a reason is topical, not a causal edge.
        ("Why does Alpha improve Beta?", "Alpha and Beta appear in a reason study."),
        # Modal and negated causes cannot close an asserted explanation.
        ("Why does Alpha cause Beta?", "Alpha may cause Beta under calibration."),
        ("Why does Alpha cause Beta?", "Alpha does not cause Beta under calibration."),
        # Shared names without a carrier do not bind cause to effect.
        ("Why does Alpha improve Beta?", "Alpha and Beta are calibrated systems."),
    )
    for index, (question, text) in enumerate(controls):
        source = _source(f"causal-control-{index}", text, 0)
        control_intent = ExplanatoryIntent.seal(
            f"causal-control-intent-{index}", question, turn_index=0,
            source_ids=(source.source_id,))
        assert solve_explanatory_obligations(
            question=question, intents=(control_intent,), sources=(source,)).state == "unsupported"


def test_paired_homogeneous_compare_fallback_requires_one_complete_ordered_span():
    question = "How does Alpha compare with Beta in latency?"
    accepted = _source(
        "paired", "Alpha reached 10 ms latency while Beta reached 12 ms latency.", 0)
    intent = ExplanatoryIntent.seal(
        "paired-intent", question, turn_index=0, source_ids=("paired",))
    assert solve_explanatory_obligations(
        question=question, intents=(intent,), sources=(accepted,)).state == "resolved"

    controls = (
        # Units are part of the scalar type.
        ("unit-mismatch", ("Alpha reached 10 ms latency while Beta reached 12 seconds latency.",)),
        # Independent claims cannot be silently joined into one comparison witness.
        ("split", ("Alpha reached 10 ms latency.", "Beta reached 12 ms latency.")),
        # Operand order is re-openable and directional.
        ("reverse", ("Beta reached 12 ms latency while Alpha reached 10 ms latency.",)),
        # Two topical entities without two typed values are insufficient.
        ("topical", ("Alpha and Beta appear in a latency benchmark.",)),
        # A single value leaves one operand unbound.
        ("single-value", ("Alpha and Beta were tested; Alpha reached 10 ms latency.",)),
    )
    for label, texts in controls:
        sources = tuple(_source(f"{label}-{index}", text, 0)
                        for index, text in enumerate(texts))
        control_intent = ExplanatoryIntent.seal(
            f"{label}-intent", question, turn_index=0,
            source_ids=tuple(item.source_id for item in sources))
        result = solve_explanatory_obligations(
            question=question, intents=(control_intent,), sources=sources)
        assert result.state == "unsupported", (label, result.state, result.residual)


def test_obligation_dag_conserves_all_seven_polyphonic_operation_charges():
    cases = {
        "lookup": "What method does Alpha use?",
        "compare": "How does Alpha compare with Beta?",
        "trace_evolution": "How did Alpha evolve over time?",
        "explain": "Why does Alpha improve Beta?",
        "quantify": "How many improvements did Alpha produce?",
        "integrate": "How do Alpha and Beta combine into a unified system?",
        "optimize": "How does Alpha optimize cost while preserving quality?",
    }
    source = _source(
        "s0", "Alpha uses a method that improves Beta and preserves quality.", 0)
    for expected, question in cases.items():
        intent = ExplanatoryIntent.seal(
            expected, question, turn_index=0, source_ids=("s0",))
        result = solve_explanatory_obligations(
            question=question, intents=(intent,), sources=(source,))
        assert any(expected in node.operations for node in result.graph.nodes)


def test_role_reversal_does_not_close_named_directed_obligation():
    source = _source("s0", "Beta supports Alpha through a verified relay.", 0)
    intent = ExplanatoryIntent.seal(
        "i0", "What does Alpha support?", turn_index=0, source_ids=("s0",))
    result = solve_explanatory_obligations(
        question="What does Alpha support?", intents=(intent,), sources=(source,))
    assert result.state in {"unsupported", "abstain"}
    assert result.text == ""


def test_intent_cannot_claim_sources_from_the_wrong_turn():
    question, intents, sources = _chain()
    wrong = replace(intents[0], source_ids=("s1",))
    result = solve_explanatory_obligations(
        question=question, intents=(wrong, *intents[1:]), sources=sources)
    assert result.state == "unsupported"
    assert "crosses its declared turn" in result.residual[0]


def test_missing_witnessed_bridge_abstains_instead_of_joining_by_adjacency():
    sources = (
        _source("s0", "Alpha supports Beta through a verified relay.", 0),
        _source("s1", "Quartz enables Topaz through a stable bridge.", 1),
    )
    intents = (
        ExplanatoryIntent.seal(
            "i0", "How does Alpha support Beta?", turn_index=0, source_ids=("s0",)),
        ExplanatoryIntent.seal(
            "i1", "How does Quartz enable Topaz?", turn_index=1, source_ids=("s1",)),
    )
    result = solve_explanatory_obligations(
        question="How does Quartz enable Topaz?", intents=intents, sources=sources)
    assert result.state == "abstain"
    assert result.residual == ("no_complete_bridged_environment",)


def test_incompatible_polarity_environments_are_contested_not_ranked():
    sources = (
        _source("positive", "Alpha supports Beta through a verified relay.", 0),
        _source("negative", "Alpha does not support Beta through a verified relay.", 0),
    )
    intent = ExplanatoryIntent.seal(
        "i0", "How does Alpha support Beta?", turn_index=0,
        source_ids=("positive", "negative"))
    result = solve_explanatory_obligations(
        question="How does Alpha support Beta?", intents=(intent,), sources=sources)
    assert result.state == "contested"
    assert len(result.alternatives) >= 2
    assert result.text == ""


def test_dependent_duplicate_does_not_gain_mass_or_duplicate_rendered_text():
    question, intents, sources = _chain(duplicate=True)
    result = solve_explanatory_obligations(
        question=question, intents=intents, sources=sources)
    assert result.state == "resolved"
    assert result.text.count("Alpha supports Beta") == 1
    assert len({item.genealogy_root for item in result.bindings
                if "Alpha supports Beta" in item.surface}) == 1


def test_certificate_rejects_source_question_and_intent_tampering():
    question, intents, sources = _chain()
    result = solve_explanatory_obligations(
        question=question, intents=intents, sources=sources)
    tampered_source = replace(sources[0], text=sources[0].text + " altered")
    assert not result.certificate.reopen(
        question=question, intents=intents,
        sources=(tampered_source, *sources[1:]))
    assert not result.certificate.reopen(
        question=question + " altered", intents=intents, sources=sources)
    changed_intent = replace(intents[1], text=intents[1].text + " altered")
    assert not result.certificate.reopen(
        question=question, intents=(intents[0], changed_intent, intents[2]),
        sources=sources)
    assert not result.certificate.reopen(
        question=question, intents=intents, sources=sources,
        config=ExplanatoryProofConfig(max_output_bytes=8_192))


def test_distinct_complete_factual_signatures_are_not_collapsed_for_shortness():
    sources = (
        _source("short", "Alpha supports Beta.", 0),
        _source("long", "Alpha supports Beta now.", 0),
    )
    intent = ExplanatoryIntent.seal(
        "i0", "What does Alpha support?", turn_index=0,
        source_ids=("short", "long"))
    result = solve_explanatory_obligations(
        question="What does Alpha support?", intents=(intent,), sources=sources)
    assert result.state == "contested"
    assert result.text == ""


def test_full_factual_signature_retains_every_lexical_charge_without_truncation():
    source = _source(
        "s0", "Alpha supports Beta with alphaone betatwo gammathree deltafour zebrafive.", 0)
    intent = ExplanatoryIntent.seal(
        "i0", "What does Alpha support?", turn_index=0, source_ids=("s0",))
    graph = compile_obligation_graph(intent.text, (intent,), (source,))
    claims = extract_authorized_claims((ClaimSource.seal("s0", source.text),))
    universe = _candidate_universe(graph, claims, (source,))
    signature = next(iter(universe.values()))[0].value_signature
    assert "lexical:alphaone" in signature
    assert "lexical:betatwo" in signature
    assert "lexical:gammathree" in signature
    assert "lexical:deltafour" in signature
    assert "lexical:zebrafive" in signature


def test_contradiction_below_top_n_is_detected_over_full_post_genealogy_pool():
    sources = tuple(
        _source(f"positive-{index}",
                f"Alpha supports Beta using Meridian{index} through verified calibration.", 0)
        for index in range(5)) + (
            _source("negative", "Alpha does not support Beta using Quartz through verified calibration.", 0),
        )
    intent = ExplanatoryIntent.seal(
        "i0", "How does Alpha support Beta?", turn_index=0,
        source_ids=tuple(item.source_id for item in sources))
    result = solve_explanatory_obligations(
        question=intent.text, intents=(intent,), sources=sources,
        config=ExplanatoryProofConfig(max_candidates_per_obligation=1))
    assert result.state == "contested"
    assert any(item.startswith("pool_contradiction:") for item in result.residual)


def _synthetic_pool_conflicts(question, sources):
    intent = ExplanatoryIntent.seal(
        "conflict-intent", question, turn_index=0,
        source_ids=tuple(item.source_id for item in sources))
    graph = compile_obligation_graph(question, (intent,), sources)
    claims = extract_authorized_claims(tuple(
        ClaimSource.seal(item.source_id, item.text) for item in sources))
    return _pool_contradictions(_candidate_universe(graph, claims, sources))


def test_conflict_identity_requires_same_subject_predicate_and_roles():
    different_subject = (
        _source("a", "Alpha supports Beta at 10 units.", 0),
        _source("g", "Gamma does not support Delta at 12 units.", 0),
    )
    assert not _synthetic_pool_conflicts("What systems support performance?", different_subject)

    different_predicate = (
        _source("support", "Alpha supports Beta at 10 units.", 0),
        _source("enable", "Alpha does not enable Beta at 12 units.", 0),
    )
    assert not _synthetic_pool_conflicts("What does Alpha do for Beta?", different_predicate)


def test_same_proposition_polarity_and_typed_numeric_slot_remain_conflicts():
    polarity = (
        _source("yes", "Alpha supports Beta through a relay.", 0),
        _source("no", "Alpha does not support Beta through a relay.", 0),
    )
    assert _synthetic_pool_conflicts("What does Alpha support?", polarity)

    numeric_slot = (
        _source("ten", "Alpha supports Beta at 10 units.", 0),
        _source("twelve", "Alpha supports Beta at 12 units.", 0),
    )
    assert _synthetic_pool_conflicts("What does Alpha support?", numeric_slot)


def test_scalar_attachment_preserves_compound_metric_object_and_unit_scope():
    different_compound_metrics = (
        _source("power",
                "Bandwidth Utilization Index achieved 34% improvement in power utilization ratio.",
                0),
        _source("accuracy",
                "Bandwidth Utilization Index achieved 47% improvement in predictive accuracy.",
                0),
    )
    assert not _synthetic_pool_conflicts(
        "What improvement did Bandwidth Utilization Index achieve?",
        different_compound_metrics)

    same_metric_and_unit = (
        _source("ten-ms", "Alpha supports Beta at 10 milliseconds latency.", 0),
        _source("twelve-ms", "Alpha supports Beta at 12 milliseconds latency.", 0),
    )
    assert _synthetic_pool_conflicts("What does Alpha support?", same_metric_and_unit)

    different_units = (
        _source("milliseconds", "Alpha supports Beta at 10 milliseconds latency.", 0),
        _source("seconds", "Alpha supports Beta at 12 seconds latency.", 0),
    )
    assert not _synthetic_pool_conflicts("What does Alpha support?", different_units)


def test_generic_improvement_head_does_not_merge_separate_measurement_contexts():
    sources = (
        _source("throughput", "Alpha supports Beta with 10% improvement in throughput.", 0),
        _source("accuracy", "Alpha supports Beta with 12% improvement in accuracy.", 0),
    )
    assert not _synthetic_pool_conflicts("What does Alpha support?", sources)


def test_generic_topic_with_different_numbers_or_anchors_is_not_a_value_conflict():
    sources = (
        _source("one", "LinguaEval evaluates 10 datasets for sentiment research.", 0),
        _source("two", "FeatureBench evaluates 12 corpora for opinion mining.", 0),
    )
    assert not _synthetic_pool_conflicts(
        "What evaluation framework is used for sentiment analysis?", sources)


def test_weak_topical_candidates_cannot_veto_the_strongest_structural_tier():
    sources = (
        _source("top",
                "Composite classifier systems reduce perturbation magnitude through verified calibration.",
                0),
        _source("weak-negative",
                "Standard perturbation-based hardening improves robustness without reducing clean precision.",
                0),
        _source("weak-positive",
                "Standard perturbation-based hardening provides modest robustness gains.", 0),
    )
    intent = ExplanatoryIntent.seal(
        "i0", "What perturbation strategy reduces magnitude?", turn_index=0,
        source_ids=tuple(item.source_id for item in sources))
    result = solve_explanatory_obligations(
        question=intent.text, intents=(intent,), sources=sources)
    assert result.state == "resolved"
    assert result.text == sources[0].text


def test_genealogical_duplicates_collapse_before_conflict_and_top_n_accounting():
    sources = tuple(_source(
        f"copy-{index}", "Alpha supports Beta through a verified relay.", 0,
        root="one-root") for index in range(6))
    intent = ExplanatoryIntent.seal(
        "i0", "How does Alpha support Beta?", turn_index=0,
        source_ids=tuple(item.source_id for item in sources))
    graph = compile_obligation_graph(intent.text, (intent,), sources)
    claims = extract_authorized_claims(tuple(
        ClaimSource.seal(item.source_id, item.text) for item in sources))
    universe = _candidate_universe(graph, claims, sources)
    assert all(len(candidates) == 1 for node, candidates in zip(graph.nodes, universe.values())
               if node.closure_mode == "witness")
    assert all(not universe[node.obligation_id] for node in graph.nodes
               if node.closure_mode != "witness")


def test_every_declared_predecessor_needs_its_own_witnessed_bridge():
    sources = (
        _source("a", "Alpha supports Beta through a verified relay.", 0),
        _source("o", "Omega enables Sigma through exact calibration.", 0),
        _source("b", "Beta improves Delta through a stable bridge.", 1),
    )
    intents = (
        ExplanatoryIntent.seal(
            "i0", "How does Alpha support Beta, and how does Omega enable Sigma?",
            turn_index=0, source_ids=("a", "o")),
        ExplanatoryIntent.seal(
            "i1", "How does Beta improve Delta?", turn_index=1, source_ids=("b",)),
    )
    result = solve_explanatory_obligations(
        question="How does Beta improve Delta?", intents=intents, sources=sources)
    assert result.state == "abstain"
    assert result.residual == ("no_complete_bridged_environment",)
    assert not result.closures


def test_output_and_claim_budgets_fail_closed():
    question, intents, sources = _chain()
    by_bytes = solve_explanatory_obligations(
        question=question, intents=intents, sources=sources,
        config=ExplanatoryProofConfig(max_output_bytes=20))
    assert by_bytes.state == "abstain"
    by_claims = solve_explanatory_obligations(
        question=question, intents=intents, sources=sources,
        config=ExplanatoryProofConfig(max_claims=2))
    assert by_claims.state == "abstain"


def test_environment_budget_exhaustion_abstains_even_with_a_possible_proof():
    sources = (
        _source("a", "Alpha supports Beta through a verified relay.", 0),
        _source("b", "Alpha supports Beta through an audited relay.", 0),
    )
    intent = ExplanatoryIntent.seal(
        "i0", "How does Alpha support Beta?", turn_index=0,
        source_ids=("a", "b"))
    result = solve_explanatory_obligations(
        question="How does Alpha support Beta?", intents=(intent,), sources=sources,
        config=ExplanatoryProofConfig(max_environments=1))
    assert result.state == "abstain"
    assert result.residual == ("environment_budget_exhausted",)


def test_permutation_is_deterministic_for_sources_and_intents():
    question, intents, sources = _chain()
    first = solve_explanatory_obligations(
        question=question, intents=intents, sources=sources)
    second = solve_explanatory_obligations(
        question=question, intents=tuple(reversed(intents)),
        sources=tuple(reversed(sources)))
    assert first.state == second.state == "resolved"
    assert first.text == second.text
    assert first.certificate == second.certificate


def test_generated_256_case_multihop_contract_reopens_for_two_through_six_hops():
    entities = ("Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta")
    relations = ("supports", "enables", "improves", "connects", "stabilizes")
    solved = 0
    observed_hops = set()
    for seed in range(256):
        hops = 2 + seed % 5
        relation = relations[(seed // 5) % len(relations)]
        sources = tuple(_source(
            f"case:{seed}:source:{turn}",
            f"{entities[turn]} {relation} {entities[turn + 1]} through a verified relay.",
            turn)
            for turn in range(hops))
        intents = tuple(ExplanatoryIntent.seal(
            f"case:{seed}:intent:{turn}",
            f"How does {entities[turn]} {relation} {entities[turn + 1]}?",
            turn_index=turn, source_ids=(sources[turn].source_id,))
            for turn in range(hops))
        question = intents[-1].text
        result = solve_explanatory_obligations(
            question=question, intents=intents, sources=sources)
        assert result.state == "resolved", (seed, hops, result.residual)
        assert result.certificate.reopen(
            question=question, intents=intents, sources=sources)
        observed_hops.add(hops)
        solved += 1
    assert solved == 256
    assert observed_hops == {2, 3, 4, 5, 6}


def test_sigma_environment_set_equals_exact_cartesian_csp_reference():
    sources = (
        _source("a0", "Alpha supports Beta through a verified relay.", 0),
        _source("a1", "Alpha supports Beta through an audited relay.", 0),
        _source("b0", "Beta enables Gamma through a verified relay.", 1),
        _source("b1", "Beta enables Gamma through an audited relay.", 1),
    )
    intents = (
        ExplanatoryIntent.seal(
            "i0", "How does Alpha support Beta?", turn_index=0,
            source_ids=("a0", "a1")),
        ExplanatoryIntent.seal(
            "i1", "How does Beta enable Gamma?", turn_index=1,
            source_ids=("b0", "b1")),
    )
    question = "How does Beta enable Gamma?"
    config = ExplanatoryProofConfig()
    graph = compile_obligation_graph(question, intents, sources)
    claims = extract_authorized_claims(tuple(
        ClaimSource.seal(item.source_id, item.text) for item in sources))
    offered = _candidates(graph, claims, sources, config)
    state, sigma, _examined, _reason = _sigma_environments(
        graph, offered, sources, config)
    pools = tuple(offered[node.obligation_id] for node in graph.nodes
                  if node.closure_mode == "witness")
    reference = tuple(environment for environment in itertools.product(*pools)
                      if _environment_bridges(
                          tuple(item.binding for item in environment), graph,
                          max_hops=config.max_bridge_hops) is not None)
    signature = lambda rows: tuple(item.binding.claim_id for item in rows)
    assert state in {"resolved", "contested"}
    assert {signature(item) for item in sigma} == {signature(item) for item in reference}


def test_synthetic_final_is_a_certified_join_not_a_required_global_claim():
    question, intents, sources = _chain()
    synthesis = "How do Alpha and Delta combine into the complete process?"
    result = solve_explanatory_obligations(
        question=synthesis, intents=intents, sources=sources)
    assert result.state == "resolved"
    final = tuple(node for node in result.graph.nodes if node.layer == "final")
    assert final and all(node.closure_mode == "join" for node in final)
    assert result.closures and all(item.mode == "join" for item in result.closures)
    assert all(not binding.obligation_id.startswith("final:") for binding in result.bindings)
    assert result.certificate.reopen(
        question=synthesis, intents=intents, sources=sources)
    tampered_intents = intents[:-1] + (ExplanatoryIntent.seal(
        "tampered-predecessor", intents[-1].text,
        turn_index=intents[-1].turn_index, source_ids=intents[-1].source_ids),)
    assert not result.certificate.reopen(
        question=synthesis, intents=tampered_intents, sources=sources)
    assert not replace(
        result.certificate, closure_digest="0" * 64).reopen(
            question=synthesis, intents=intents, sources=sources)


def test_join_does_not_close_when_a_declared_predecessor_is_unsupported():
    sources = (
        _source("a", "Alpha supports Beta through a relay.", 0),
        _source("b", "Beta enables Gamma through a bridge.", 1),
    )
    intents = (
        ExplanatoryIntent.seal(
            "i0", "How does Alpha support Beta?", turn_index=0, source_ids=("a",)),
        ExplanatoryIntent.seal(
            "i1", "How does Beta enable Gamma, and how does Omega improve Sigma?",
            turn_index=1, source_ids=("b",)),
    )
    result = solve_explanatory_obligations(
        question="How do Alpha and Sigma combine?", intents=intents, sources=sources)
    assert result.state == "unsupported"
    assert any(item.startswith("missing_witness:") for item in result.residual)
