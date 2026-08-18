from horizon_memory.claim_composer import ClaimSource, ContextIntent
from horizon_memory.proof_dossier import build_proof_dossier


def _fixture():
    sources = (
        ClaimSource.seal("s1", "Aldren activates Zephyra. Zephyra stabilizes Meridian."),
        ClaimSource.seal("s2", "Meridian reduces errors by 18 percent. Unrelated noise persists."),
    )
    intents = (
        ContextIntent.seal("i1", "How does Aldren activate Zephyra?", ("s1",)),
        ContextIntent.seal("i2", "How are Meridian errors reduced?", ("s2",)),
    )
    return sources, intents


def test_horizon_and_bm25_share_units_budgets_and_provenance():
    sources, intents = _fixture()
    for strategy in ("horizon", "bm25"):
        first = build_proof_dossier(
            sources=sources, intents=intents, strategy=strategy,
            per_fiber=2, max_bytes=1024)
        second = build_proof_dossier(
            sources=sources, intents=intents, strategy=strategy,
            per_fiber=2, max_bytes=1024)
        assert first.verify(sources, 1024)
        assert first.digest == second.digest
        assert first.evidence_bytes <= 1024


def test_tampered_source_breaks_dossier_verification():
    sources, intents = _fixture()
    dossier = build_proof_dossier(
        sources=sources, intents=intents, strategy="horizon",
        per_fiber=2, max_bytes=1024)
    tampered = (ClaimSource.seal("s1", sources[0].content + " changed"), sources[1])
    assert not dossier.verify(tampered, 1024)


def test_default_dedup_threshold_none_preserves_prior_digest():
    # D135 correction: dedup_threshold defaults to None so every frozen digest/reproducibility
    # check computed before this parameter existed stays byte-identical.
    sources, intents = _fixture()
    with_default = build_proof_dossier(
        sources=sources, intents=intents, strategy="horizon", per_fiber=2, max_bytes=1024)
    explicit_none = build_proof_dossier(
        sources=sources, intents=intents, strategy="horizon", per_fiber=2, max_bytes=1024,
        dedup_threshold=None)
    assert with_default.digest == explicit_none.digest


def test_source_priority_reorders_merge_within_rank_tier():
    sources = (
        ClaimSource.seal("s1", "Aldren once mentioned Zephyra in an unrelated meeting."),
        ClaimSource.seal("s2", "Aldren activates Zephyra directly, stabilizing Meridian."),
    )
    intents = (
        ContextIntent.seal("i1", "How does Aldren activate Zephyra?", ("s1", "s2")),
    )
    # Both claims fit the budget; what changes is merge ORDER (claims[0] = first selected).
    default_order = build_proof_dossier(
        sources=sources, intents=intents, strategy="bm25", per_fiber=1, max_bytes=1024)
    prioritized = build_proof_dossier(
        sources=sources, intents=intents, strategy="bm25", per_fiber=1, max_bytes=1024,
        source_priority={"s2": 10.0, "s1": 0.0})
    assert prioritized.claims[0].source_id == "s2"
    # Default (no priority) falls back to alphabetical source_id order at the same rank tier.
    assert default_order.claims[0].source_id == "s1"


def test_source_priority_none_preserves_prior_digest():
    sources, intents = _fixture()
    with_default = build_proof_dossier(
        sources=sources, intents=intents, strategy="horizon", per_fiber=2, max_bytes=1024)
    explicit_none = build_proof_dossier(
        sources=sources, intents=intents, strategy="horizon", per_fiber=2, max_bytes=1024,
        source_priority=None)
    assert with_default.digest == explicit_none.digest


def test_global_sort_alpha_none_preserves_prior_digest():
    sources, intents = _fixture()
    with_default = build_proof_dossier(
        sources=sources, intents=intents, strategy="horizon", per_fiber=2, max_bytes=1024)
    explicit_none = build_proof_dossier(
        sources=sources, intents=intents, strategy="horizon", per_fiber=2, max_bytes=1024,
        global_sort_alpha=None)
    assert with_default.digest == explicit_none.digest


def test_global_sort_alpha_requires_bounded_value():
    sources, intents = _fixture()
    for bad_alpha in (-0.1, 1.1, 2.0):
        try:
            build_proof_dossier(sources=sources, intents=intents, strategy="horizon",
                                per_fiber=2, max_bytes=1024, global_sort_alpha=bad_alpha)
            assert False, f"expected ValueError for global_sort_alpha={bad_alpha}"
        except ValueError:
            pass


def test_global_sort_alpha_lets_high_priority_source_second_claim_beat_low_priority_first_claim():
    sources = (
        ClaimSource.seal(
            "sA",
            "Aldren activates Zephyra directly within the core control loop of the entire "
            "distributed system architecture. Aldren personally verified the activation "
            "through extensive manual testing procedures across every subsystem."),
        ClaimSource.seal(
            "sB",
            "Weather patterns rarely relate to Zephyra style network topics discussed "
            "today in this unrelated technical document."),
    )
    intents = (
        ContextIntent.seal("i1", "How does Aldren activate Zephyra?", ("sA", "sB")),
    )
    priority = {"sA": 10.0, "sB": 0.01}
    # Budget (the enforced 256B minimum) fits any two of the three claims but not all three.
    rank_major = build_proof_dossier(
        sources=sources, intents=intents, strategy="bm25", per_fiber=2, max_bytes=256,
        source_priority=priority)
    global_sort = build_proof_dossier(
        sources=sources, intents=intents, strategy="bm25", per_fiber=2, max_bytes=256,
        source_priority=priority, global_sort_alpha=0.7)
    rank_major_sources = {claim.source_id for claim in rank_major.claims}
    global_sort_sources = {claim.source_id for claim in global_sort.claims}
    # Rank-major (even priority-tiebroken) always admits every source's own rank-1 claim before
    # any source's rank-2 -- so sB's single claim gets in alongside sA's best claim.
    assert "sB" in rank_major_sources
    # Global sort can let sA's own 2nd-best claim outrank sB's only (marginal) claim entirely.
    assert global_sort_sources == {"sA"}


def test_global_sort_alpha_prefers_asserted_claim_over_modal_distractor_with_similar_lexical_overlap():
    # D138 regression: real LongMemEval failure. "How long did it take me to assemble the IKEA
    # bookshelf?" (gold "4 hours") composed a distractor first -- "IKEA coffee tables... might
    # take around 1-2 hours" -- ahead of the real answer "I ... assembled an IKEA bookshelf ...
    # it took me 4 hours." Pure lexical overlap nearly ties the two (a light-stemmer artifact
    # costs the answer a hit: "assemble" in the question vs "assembl" from "assembled" in the
    # claim don't share a token); modality (modal "might" vs asserted "took") must break the tie.
    sources = (
        ClaimSource.seal(
            "s1",
            "Some IKEA coffee tables with storage, like the LACK series, are relatively simple "
            "to assemble and might take around 1-2 hours to put together. I just assembled an "
            "IKEA bookshelf recently and it took me 4 hours, which was longer than expected."),
    )
    intents = (
        ContextIntent.seal(
            "i1", "How long did it take me to assemble the IKEA bookshelf?", ("s1",)),
    )
    dossier = build_proof_dossier(
        sources=sources, intents=intents, strategy="bm25", per_fiber=8, max_bytes=1024,
        source_priority={"s1": 1.0}, global_sort_alpha=0.3)
    assert "4 hours" in dossier.claims[0].surface
    assert dossier.claims[0].modality == "asserted"


def test_submodular_budget_fill_false_preserves_prior_digest():
    sources, intents = _fixture()
    with_default = build_proof_dossier(
        sources=sources, intents=intents, strategy="horizon", per_fiber=2, max_bytes=1024)
    explicit_false = build_proof_dossier(
        sources=sources, intents=intents, strategy="horizon", per_fiber=2, max_bytes=1024,
        submodular_budget_fill=False)
    assert with_default.digest == explicit_false.digest


def test_submodular_budget_fill_rejects_conflicting_params():
    sources, intents = _fixture()
    for kwargs in (
        {"submodular_budget_fill": True, "dedup_threshold": 0.6},
        {"submodular_budget_fill": True, "source_priority": {"s1": 1.0}},
        {"submodular_budget_fill": True, "global_sort_alpha": 0.5},
    ):
        try:
            build_proof_dossier(sources=sources, intents=intents, strategy="horizon",
                                per_fiber=2, max_bytes=1024, **kwargs)
            assert False, f"expected ValueError for {kwargs}"
        except ValueError:
            pass


def test_submodular_budget_fill_stops_at_redundant_claims_even_with_budget_to_spare():
    # "and fast" is a near-duplicate of the first claim (same facet, no new obligation tokens);
    # the Meridian/errors claim covers a genuinely different facet of the same obligation.
    sources = (
        ClaimSource.seal(
            "s1",
            "Aldren activates Zephyra directly. Aldren activates Zephyra directly and fast. "
            "Meridian reduces total errors by 18 percent."),
    )
    intents = (
        ContextIntent.seal(
            "i1", "How does Aldren activate Zephyra and what happens to errors?", ("s1",)),
    )
    # Budget (256B, the enforced minimum) comfortably fits all three short claims.
    rank_major = build_proof_dossier(
        sources=sources, intents=intents, strategy="bm25", per_fiber=8, max_bytes=256)
    submodular = build_proof_dossier(
        sources=sources, intents=intents, strategy="bm25", per_fiber=8, max_bytes=256,
        submodular_budget_fill=True)
    # Rank-major has no reason to stop early -- it admits the redundant claim too.
    assert len(rank_major.claims) == 3
    # Submodular greedy stops once no candidate offers positive marginal coverage gain --
    # it picks the two claims that together cover both facets, and skips the redundant third.
    assert len(submodular.claims) == 2
    surfaces = {claim.surface for claim in submodular.claims}
    assert "Aldren activates Zephyra directly." in surfaces
    assert "Meridian reduces total errors by 18 percent." in surfaces
    assert "Aldren activates Zephyra directly and fast." not in surfaces


def test_dedup_threshold_rejects_near_duplicate_claims():
    sources = (
        ClaimSource.seal(
            "s1",
            "Aldren activates Zephyra directly. Aldren activates Zephyra directly and fast. "
            "Meridian reduces total errors by 18 percent."),
    )
    intents = (
        ContextIntent.seal(
            "i1", "How does Aldren activate Zephyra and what happens to errors?", ("s1",)),
    )
    no_dedup = build_proof_dossier(
        sources=sources, intents=intents, strategy="horizon", per_fiber=8, max_bytes=4096)
    deduped = build_proof_dossier(
        sources=sources, intents=intents, strategy="horizon", per_fiber=8, max_bytes=4096,
        dedup_threshold=0.6)
    assert len(deduped.claims) < len(no_dedup.claims)
    surfaces = [claim.surface for claim in deduped.claims]
    assert len(surfaces) == len(set(surfaces))
