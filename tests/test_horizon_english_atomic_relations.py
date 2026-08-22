import hashlib
from pathlib import Path

import pytest

from horizon_memory import (
    EnglishAtomicRelationCompiler,
    OpenTextHorizonMemory,
    RouteDocument,
    VERB_EXCEPTIONS_SHA256,
    compact_english_atomic_relation,
    open_compact_english_atomic_relation,
)


@pytest.mark.parametrize(("source", "question", "expected"), (
    ("That's overstating it.", "What did That overstate?", "it"),
    ("You can buy me dinner.", "What did You buy?", "dinner"),
    ("I shall send you a copy.", "What did I send?", "copy"),
    ("You really got me thinking.", "What did You get?", "me"),
    ("Anne included this info.", "What did Anne include?", "info"),
    ("You will find these helpful.", "What did You find?", "these"),
    ("She makes every item fit.", "What did She make?", "item"),
    ("Transwestern will own and operate the interconnect.",
     "What did Transwestern own?", "interconnect"),
    ("You NEVER get a human.", "Who get human?", "you"),
))
def test_promoted_en_pack_returns_only_exact_reopenable_spans(source, question, expected):
    compiler = EnglishAtomicRelationCompiler()
    result = compiler.read(source, question)
    assert result.state == "resolved"
    assert result.answer == expected
    assert source[slice(*result.answer_span)].casefold() == expected
    assert result.proofs[0].reopen(source, question, compiler)


def test_promoted_en_pack_abstains_on_absent_known_operand_and_contests_two_values():
    compiler = EnglishAtomicRelationCompiler()
    absent = compiler.read("Aurelia admired Fiora.", "Who admired ZzAbsent?")
    assert absent.state == "abstain"
    contested = compiler.read(
        "Aurelia saw Fiora and Aurelia saw Selene.", "What did Aurelia see?")
    assert contested.state == "contested"
    assert not contested.proof_closed


@pytest.mark.parametrize(("source", "force"), (
    ("Aurelia moved the crate.", "asserted_candidate"),
    ("Did Aurelia move the crate?", "interrogative"),
    ("If Aurelia moves the crate, call me.", "conditional"),
    ("Aurelia might move the crate.", "modal"),
    ("Aurelia did not move the crate.", "negated"),
))
def test_promoted_en_pack_preserves_clause_force_instead_of_inventing_a_fact(source, force):
    result = EnglishAtomicRelationCompiler().read(source, "What did Aurelia move?")
    assert result.source_force == force


def test_compact_relation_is_140_bytes_and_reopens_against_source_query_and_pack():
    source = "You can buy me dinner."
    question = "What did You buy?"
    compiler = EnglishAtomicRelationCompiler()
    result = compiler.read(source, question)
    blob = compact_english_atomic_relation(result)
    assert len(blob) == 140
    assert open_compact_english_atomic_relation(
        blob, source=source, question=question, compiler=compiler) == result
    tampered = bytearray(blob); tampered[-1] ^= 1
    with pytest.raises(ValueError, match="integrity"):
        open_compact_english_atomic_relation(
            bytes(tampered), source=source, question=question, compiler=compiler)
    with pytest.raises(ValueError, match="authority"):
        open_compact_english_atomic_relation(
            blob, source=source + "x", question=question, compiler=compiler)


@pytest.mark.parametrize(("source", "question", "expected"), (
    # Real Gen-Z chat text: "The player got five one-tap headshots in under four seconds."
    # resolved to "five" (the numeral itself), never reaching the true object "headshots".
    ("The player got five one-tap headshots in under four seconds.",
     "What did player get?", "headshots"),
    # A digit-only numeral had the identical, pre-existing problem -- confirmed directly before
    # any fix, not something the spelled-numeral extension introduced.
    ("The player got 5 one-tap headshots in under 4 seconds.",
     "What did player get?", "headshots"),
    # A compound magnitude ("1,000") must be skipped as one unit, not stop mid-number.
    ("He entered school with exactly 1,000 positive aura points today.",
     "What did he enter?", "school"),
    # "45-pound" -- a hyphenated compound whose PREFIX is itself a digit -- must be skipped as a
    # single quantity unit, reaching "plates", not returned as the answer itself.
    ("Why couldn't he just slide the 45-pound plates off the collar?",
     "What did he slide?", "plates"),
    # "one-tap" -- "one" is unambiguous as a numeral only when it is the prefix of a hyphenated
    # compound (never added to the general numeral-word set, since a bare standalone "one" is also
    # the indefinite-pronoun head, "the one that got away", which must stay reachable on its own).
    ("The player got one-tap headshots consistently.",
     "What did player get?", "headshots"),
))
def test_numeral_head_shift_reaches_the_true_object(source, question, expected):
    compiler = EnglishAtomicRelationCompiler()
    result = compiler.read(source, question)
    assert result.state == "resolved"
    assert source[slice(*result.answer_span)].casefold() == expected


def test_standalone_one_as_a_pronoun_head_is_not_treated_as_a_skippable_numeral():
    # Negative control: a bare, non-hyphenated "one" used as the indefinite-pronoun head of its
    # own noun phrase must still be reachable as the answer -- the hyphen-prefix numeral rule only
    # ever applies to a hyphenated compound, never to "one" standing alone.
    compiler = EnglishAtomicRelationCompiler()
    result = compiler.read("I want the one with red laces.", "What did I want?")
    assert result.state == "resolved"
    assert result.answer == "one"


def test_hyphenated_compound_noun_that_is_itself_the_head_is_not_skipped():
    # Negative control: a hyphenated compound whose own PREFIX is not a recognized numeral
    # ("half-court", "well-known") must never be treated as skippable just because it contains a
    # hyphen -- only a genuinely numeral-led compound ("45-pound", "one-tap") qualifies.
    compiler = EnglishAtomicRelationCompiler()
    result = compiler.read("I bought a half-court behind the house.", "What did I buy?")
    assert result.state == "resolved"
    assert result.answer == "half-court"


def test_known_gap_noun_noun_and_bare_adjective_compounds_stop_on_the_first_word():
    # Documents a real, NOT-yet-fixed limitation found via a real Gen-Z chat probe, pinned rather
    # than silently left unexplained: English attributive noun-noun ("barbell clips") and bare
    # adjective-noun ("direct skull blast", "large taro milk tea") compounds are head-FINAL --
    # `phrase_head`'s only compounding mechanism is the numeral/hyphen-numeral skip above, which
    # correctly reaches the head after a genuine quantity span ("three separate corsets" still
    # stops at "separate", the adjective right after the now-correctly-skipped numeral "three").
    # A general "keep walking right while adjacent and usable" rule was considered and rejected
    # without shipping it: the identical "adjacent + satisfies a simple per-token check" shape was
    # already tested directly for hyphenated compounds (see the negative control above) and found
    # to walk PAST the true object into unrelated adjacent content with no real signal to stop on
    # ("half-court" -> "behind"); a general noun-noun/adjective chain has no cheaper stopping
    # signal either (e.g. "I bought milk chocolate today." has no marker distinguishing "milk
    # chocolate" as one compound from "chocolate today" continuing the walk one word too far).
    # Fixing this properly needs real POS information or a much more careful design, not a
    # same-night patch -- see CLAUDE.md / UNIVERSAL_DETERMINISTIC_COMPILATION_PROGRAM.md.
    compiler = EnglishAtomicRelationCompiler()
    barbell = compiler.read(
        "He had clamped barbell clips tightly on both ends before starting.",
        "What did he clamp?")
    assert (barbell.state, barbell.answer) == ("resolved", "barbell")
    numeral_then_adjective = compiler.read(
        "I combined three separate corsets and a ruffled maxi skirt with silver jewelry.",
        "What did I combine?")
    assert (numeral_then_adjective.state, numeral_then_adjective.answer) == ("resolved", "separate")


def test_wordnet_exception_pack_is_exact_and_small():
    path = (Path(__file__).parents[1] / "src" / "horizon_memory" / "resources" /
            "wordnet-3.0" / "verb.exc")
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == VERB_EXCEPTIONS_SHA256
    assert len(raw) < 40_000


def test_open_text_memory_exposes_opt_in_attested_en_reader_without_changing_default_answer():
    memory = OpenTextHorizonMemory(scope_id=7, session_id="en-pack")
    document = RouteDocument(1, "You can buy me dinner.", 7, "en-pack", 1, "source:1")
    assert memory.ingest_documents((document,)).state == "APPLIED"
    result = memory.answer_atomic_relation_en("What did You buy?", fact_id=1)
    assert result.fact_id == 1 and result.source_id == "source:1"
    assert result.relation.answer == "dinner" and result.proof_closed
    with pytest.raises(ValueError, match="known document"):
        memory.answer_atomic_relation_en("What did You buy?", fact_id=999)
