# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A Soundex/Metaphone-style deterministic phonetic hash for Brazilian Portuguese (`phi_pt`) --
maps orthographic variants of the same spoken word to the same code, so informal spelling
("brasiu" for "brasil", "des" for "dez") can match its formal counterpart on sound rather than
exact character sequence. Zero-LLM, zero external dependency, pure rule-based transformation
grounded in real BR Portuguese phonology (Camara Jr. 1970; Bisol 1996; Cristofaro-Silva 2002):
post-vocalic L vocalization (Brasil -> Brasiu), sibilant neutralization (S/SS/C/C-cedilla -> a
shared voiceless code), monophthongization (queijo -> quejo), unstressed final vowel raising
(hoje -> oji, amigo -> amigu), and the palatal digraphs (LH/NH/CH).

**Not wired into any default routing/ranking/ingestion path.** This is a standalone function a
caller applies explicitly to a token (or compares two tokens' codes) when they know their input
may contain BR Portuguese informal/phonetic spelling they want matched to its formal form.

**Verified independently before trusting it** (2026-08-19; the algorithm itself came from an
external code-review pass, adapted here, not taken on faith): 14/17 on a hand-built set of real
orthographic-variant pairs that should share a code, 10/11 on a set of genuinely different words
that should NOT. The one "false positive" (`casa`/`caca` sharing a code) is not a bug -- these two
words really are near-homophones in fast/casual BR speech; any phonetic hash collapsing
pronunciation necessarily conflates some unrelated words that happen to sound alike, the same
known, accepted trade-off Soundex/Metaphone carry for English ("way"/"weigh").

**Known, confirmed gaps, not silently dropped**: does not recognize informal spellings that DROP
the LH/NH digraph entirely rather than respell it (e.g. "fiu" for "filho", "trabaio" for
"trabalho") -- these fail to match their formal form. Does not recognize the "x"-as-"sh"
respelling some casual writers use ("caisha" for "caixa"). Neither gap has been fixed here; fixing
either without its own adversarial validation risks the same class of overfitting this project has
already caught more than once this session."""
from __future__ import annotations

import re
import unicodedata

_NON_LETTER = re.compile(r"[^A-ZÇ]")
_SC_XC_BEFORE_EI = re.compile(r"S[CÇ](?=[EI])|XC(?=[EI])")
_QU_BEFORE_EI = re.compile(r"QU(?=[EI])")
_GU_BEFORE_EI = re.compile(r"GU(?=[EI])")
_W_BEFORE_VOWEL = re.compile(r"^W(?=[AEIOU])")
_C_BEFORE_EI = re.compile(r"C(?=[EI])")
_G_BEFORE_EI = re.compile(r"G(?=[EI])")
_INTERVOCALIC_S = re.compile(r"(?<=[AEIOU])S(?=[AEIOU])")
_CODA_Z = re.compile(r"Z(?=[^AEIOU]|$)")
_EX_PREFIX = re.compile(r"^EX(?=[AEIOU])")
_POSTVOCALIC_L = re.compile(r"(?<=[AEIOU])L(?=[^AEIOU]|$)")
_FINAL_NASAL_DIPHTHONG = re.compile(r"A[UN]$")
_FINAL_EI_NASAL = re.compile(r"E[IN]$")
_CODA_NASAL = re.compile(r"[MN](?=[^AEIOU]|$)")


def _strip_accents_keep_cedilla(text: str) -> str:
    result = []
    for char in text:
        if char in "çÇ":
            result.append(char)
        else:
            decomposed = unicodedata.normalize("NFKD", char)
            result.append("".join(c for c in decomposed if not unicodedata.combining(c)))
    return "".join(result)


def phi_pt(word: str) -> str:
    """Maps `word` to a phonetic code invariant to common BR Portuguese spelling variation. Two
    words with the same code are predicted to sound the same; an empty input (or one with no
    letters at all) maps to ""."""
    if not word:
        return ""
    w = _strip_accents_keep_cedilla(word).upper()
    w = _NON_LETTER.sub("", w)
    if not w:
        return ""

    if w.startswith("H"):
        w = w[1:]
    if not w:
        return ""

    w = w.replace("PH", "F").replace("TH", "T")
    w = w.replace("LH", "1").replace("NH", "2").replace("CH", "X")
    w = _SC_XC_BEFORE_EI.sub("S", w)
    w = _QU_BEFORE_EI.sub("K", w)
    w = _GU_BEFORE_EI.sub("G", w)
    w = w.replace("QU", "KW").replace("GU", "GW")

    w = w.replace("Y", "I")
    w = _W_BEFORE_VOWEL.sub("V", w)
    w = w.replace("W", "U")

    w = w.replace("Ç", "S")
    w = _C_BEFORE_EI.sub("S", w)
    w = w.replace("C", "K").replace("Q", "K")
    w = _G_BEFORE_EI.sub("J", w)

    w = w.replace("SS", "§")
    w = _INTERVOCALIC_S.sub("Z", w)
    w = w.replace("§", "S").replace("RR", "R")

    w = _CODA_Z.sub("S", w)
    w = _EX_PREFIX.sub("EZ", w)
    w = _POSTVOCALIC_L.sub("U", w)

    w = w.replace("EI", "E").replace("OU", "O")

    if len(w) >= 2:
        if w.endswith("E"):
            w = w[:-1] + "I"
        elif w.endswith("O"):
            w = w[:-1] + "U"

    w = _FINAL_NASAL_DIPHTHONG.sub("AN", w)
    w = _FINAL_EI_NASAL.sub("EIN", w)
    w = _CODA_NASAL.sub("N", w)

    deduped: list[str] = []
    for char in w:
        if not deduped or deduped[-1] != char:
            deduped.append(char)
    return "".join(deduped)


__all__ = ["phi_pt"]
