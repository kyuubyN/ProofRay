from dataclasses import replace

from horizon_memory.claim_composer import (
    ClaimSource, compile_question_obligations, extract_authorized_claims,
)


def test_question_coordination_preserves_exact_spans():
    question = ("How does the Aldren method improve Zephyra stability, and what "
                "change occurs in Meridian errors?")
    obligations = compile_question_obligations(question)
    assert len(obligations) == 2
    assert all(item.verify(question) for item in obligations)


def test_claims_reopen_exact_sources_and_tampering_fails():
    sources = (
        ClaimSource.seal("s1", "The Aldren method improves Zephyra stability. Noise remains unrelated."),
        ClaimSource.seal("s2", "Zephyra stability reduces Meridian errors by 18 percent."),
    )
    claims = extract_authorized_claims(sources)
    mapping = {item.source_id: item for item in sources}
    assert all(item.verify(mapping) for item in claims)
    broken = replace(sources[0], content=sources[0].content + " changed")
    assert not claims[0].verify({"s1": broken})


def test_sentence_split_preserves_decimal_numbers():
    source = ClaimSource.seal(
        "s1", "SeqFlow-Net achieves 3.8x faster inference than the baseline. "
              "It also cuts memory use by 5.3 percent.")
    claims = extract_authorized_claims((source,))
    surfaces = [item.surface for item in claims]
    assert any("achieves 3.8x faster" in surface for surface in surfaces)
    assert not any(surface.rstrip().endswith("achieves 3.") for surface in surfaces)
    assert not any(surface.lstrip().startswith("8x faster") for surface in surfaces)
    assert any("cuts memory use by 5.3 percent" in surface for surface in surfaces)


def test_sentence_split_preserves_code_punctuation():
    source = ClaimSource.seal(
        "s1", "Setting DATABASE_URL in cargo.toml fixed the connection error. "
              "We also enabled optional chaining (?.) to avoid null pointer bugs.")
    claims = extract_authorized_claims((source,))
    surfaces = [item.surface for item in claims]
    assert any("cargo.toml fixed the connection error" in surface for surface in surfaces)
    assert not any(surface.rstrip().endswith("cargo.") for surface in surfaces)
    assert not any(surface.lstrip().startswith("toml") for surface in surfaces)
    assert any("optional chaining (?.) to avoid null pointer bugs" in surface
              for surface in surfaces)
    assert not any(surface.lstrip().startswith(")") for surface in surfaces)


def test_sentence_split_under_segments_consecutive_terminal_quotes_by_design():
    """Documented trade-off: requiring trailing whitespace after a sentence terminator (needed to
    stop splitting mid `cargo.toml`/`(?.)`) means a period directly before a closing quote no
    longer counts as a terminator either -- three real sentences each ending in a quoted single
    word collapse into one claim instead of into three corrupted fragments. Accepted: the prior
    behavior produced invalid fragments, not real sentence boundaries."""
    source = ClaimSource.seal(
        "s1", 'He said "no." She said "yes." They disagreed in the end.')
    claims = extract_authorized_claims((source,))
    surfaces = [item.surface for item in claims]
    assert len(surfaces) == 1
    assert surfaces[0] == 'He said "no." She said "yes." They disagreed in the end.'


def test_short_fragments_are_dropped():
    source = ClaimSource.seal("s1", "Ok. This is a proper sentence with real content.")
    claims = extract_authorized_claims((source,))
    surfaces = [item.surface for item in claims]
    assert not any(surface == "Ok." for surface in surfaces)
    assert any("proper sentence with real content" in surface for surface in surfaces)


def test_duplicate_source_ids_rejected():
    import pytest
    sources = (
        ClaimSource.seal("s1", "First real sentence about something specific."),
        ClaimSource.seal("s1", "Second real sentence about something else."),
    )
    with pytest.raises(ValueError):
        extract_authorized_claims(sources)
