# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
from horizon_memory.raw_causal_channels import (
    RawCausalDocument, RawCausalSyndromeIndex, is_cjk, observe_raw_text, segment_zh,
)


def test_raw_observables_preserve_charges_and_pragmatic_phase():
    value = observe_raw_text("Maya might not visit Porto on Monday with 3 friends.")
    assert value.polarity == "negative"
    assert value.modality == "modal"
    assert value.numbers == ("3",)
    assert value.temporal == ("monday",)
    assert "porto" in value.entities


def test_morphological_surface_projects_without_a_dictionary():
    index = RawCausalSyndromeIndex((
        RawCausalDocument(1, "Maya schedules the production deployment.", 0, 0),
        RawCausalDocument(2, "Liam bought a wooden table.", 0, 1),
    ))
    ranked = index.rank(index.components("Who deployed to production?"),
                        (1.0, 0.5, 0.5, 0.25, 0.5, 1.0))
    assert ranked[0].fact_id == 1
    assert ranked[0].sublexical > ranked[1].sublexical


def test_contradictory_declared_number_repels_but_absence_is_unknown():
    index = RawCausalSyndromeIndex((
        RawCausalDocument(1, "The team ordered 7 sensors.", 0, 0),
        RawCausalDocument(2, "The team ordered sensors.", 0, 1),
        RawCausalDocument(3, "The team ordered 9 sensors.", 0, 2),
    ))
    ranked = index.rank(index.components("Did the team order 7 sensors?"),
                        (1.0, 0.0, 0.0, 0.0, 0.0, 2.0))
    assert [item.fact_id for item in ranked] == [1, 2, 3]
    assert ranked[-1].contradiction == 1.0


def test_one_amplitude_contains_all_channels_not_independent_rank_votes():
    index = RawCausalSyndromeIndex((
        RawCausalDocument(1, "Nora visits Lisbon on Monday.", 0, 0),
        RawCausalDocument(2, "Nora considers a journey.", 0, 1),
    ))
    weights = (1.0, 0.5, 0.5, 0.25, 0.5, 1.0)
    result = index.rank(index.components("When did Nora visit Lisbon?"), weights)[0]
    expected = (weights[0] * result.lexical + weights[1] * result.sublexical +
                weights[2] * result.entity + weights[3] * result.relation +
                weights[4] * result.observable - weights[5] * result.contradiction)
    assert result.amplitude == expected


def test_cjk_clauses_segment_into_real_words_not_one_opaque_token():
    # `_WORD`'s regex has no concept of CJK word boundaries (CJK ideographs are `\w`, and Chinese
    # has no whitespace between words), so an entire punctuation-delimited clause used to match
    # as ONE token -- two sentences describing the same fact in different words shared exactly
    # zero lexical overlap (2026-08-19, found via code review, confirmed by a real end-to-end
    # HorizonAnswerEngine reproduction that fully abstained on a trivially answerable question).
    value = observe_raw_text("北京的地铁系统在2023年运送了超过一百万名乘客。")
    assert "北京" in value.lexical
    assert value.lexical != (
        "北京的地铁系统在2023年运送了超过一百万名乘客",
    ), "the whole clause must not still collapse into one opaque token"
    # A short (< 3 char) real dictionary word must survive -- the `len(token) >= 3` floor is a
    # Latin-script heuristic and does not apply to CJK, where most words are 2 characters.
    assert any(len(token) == 2 for token in value.lexical)


def test_cjk_related_sentences_share_real_lexical_overlap():
    a = observe_raw_text("北京的地铁系统在2023年运送了超过一百万名乘客。")
    b = observe_raw_text("根据数据，北京地铁在2023年的乘客运送量创下新高。")
    overlap = set(a.lexical) & set(b.lexical)
    assert overlap, "two related Chinese sentences must not have zero lexical overlap"


def test_cjk_numbers_are_detected_even_when_glued_to_surrounding_characters():
    # `\b\d+\b` requires a real Unicode word-boundary on both sides, but CJK ideographs are `\w`,
    # so "2023" glued directly to Chinese text on both sides (ordinary phrasing, not an
    # identifier) never had a `\b` transition and was invisible to this channel.
    value = observe_raw_text("在2023年发布")
    assert "2023" in value.numbers


def test_number_regex_still_does_not_split_out_digits_from_an_identifier():
    # The CJK fix must not regress the original purpose of requiring a boundary: "123" glued to
    # Latin letters on both sides (an identifier, not a standalone number) must still not match.
    value = observe_raw_text("see item abc123def for details")
    assert "123" not in value.numbers


def test_mixed_latin_and_cjk_token_keeps_the_latin_word_intact():
    # A `_WORD` match can glue a CJK run directly onto Latin letters with no separator (both
    # scripts are `\w`) -- segmentation must split back into same-script pieces first, or the
    # embedded Latin word gets shredded into individual letters.
    value = observe_raw_text("Meridian项目在2023年完成")
    assert "meridian" in value.lexical
    assert "项目" in value.lexical


def test_is_cjk_and_segment_zh_basic_behavior():
    assert is_cjk("北京")
    assert not is_cjk("Beijing")
    words = segment_zh("北京的地铁系统")
    assert "北京" in words
    assert "".join(words) == "北京的地铁系统"
