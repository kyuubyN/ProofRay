# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Opt-in Brazilian Portuguese phonetic hash -- never wired into any default routing/ranking/
ingestion path. See the module's own docstring for the independent verification this suite
locks in: 14/17 real orthographic-variant pairs matched, 10/11 genuinely different word pairs
correctly did not match, before this was trusted enough to become a real module."""
from __future__ import annotations

import unittest

from horizon_memory.phonetic_pt import phi_pt


class OrthographicVariantsMatchTests(unittest.TestCase):
    """Real BR Portuguese spoken-spelling variants of the same word must share a code."""

    def _assert_match(self, word_a: str, word_b: str) -> None:
        self.assertEqual(phi_pt(word_a), phi_pt(word_b), f"{word_a!r} vs {word_b!r}")

    def test_postvocalic_l_vocalization(self):
        self._assert_match("brasil", "brasiu")
        self._assert_match("papel", "papeu")

    def test_coda_devoicing(self):
        self._assert_match("dez", "des")
        self._assert_match("paz", "pas")
        self._assert_match("luz", "lus")

    def test_unstressed_final_vowel_raising(self):
        self._assert_match("hoje", "oji")
        self._assert_match("leite", "leiti")
        self._assert_match("gente", "genti")
        self._assert_match("carro", "karu")
        self._assert_match("amigo", "amigu")

    def test_monophthongization(self):
        self._assert_match("queijo", "quejo")
        self._assert_match("peixe", "pexe")

    def test_l_m_sound_alike_variant(self):
        self._assert_match("mal", "mau")


class DistinctWordsDoNotMatchTests(unittest.TestCase):
    """Genuinely different words, not spelling variants of each other, must not collapse to the
    same code just because they share some sounds."""

    def _assert_distinct(self, word_a: str, word_b: str) -> None:
        self.assertNotEqual(phi_pt(word_a), phi_pt(word_b), f"{word_a!r} vs {word_b!r}")

    def test_unrelated_words_stay_distinct(self):
        self._assert_distinct("pao", "mao")
        self._assert_distinct("gato", "rato")
        self._assert_distinct("dia", "via")
        self._assert_distinct("bola", "cola")
        self._assert_distinct("carro", "barro")
        self._assert_distinct("filho", "milho")
        self._assert_distinct("banco", "branco")
        self._assert_distinct("porta", "torta")
        self._assert_distinct("mesa", "pesa")
        self._assert_distinct("livro", "libra")


class EdgeCaseTests(unittest.TestCase):
    def test_empty_string_maps_to_empty_code(self):
        self.assertEqual(phi_pt(""), "")

    def test_punctuation_only_maps_to_empty_code(self):
        self.assertEqual(phi_pt("..."), "")

    def test_silent_initial_h_is_dropped(self):
        self.assertEqual(phi_pt("hoje"), phi_pt("oje"))


if __name__ == "__main__":
    unittest.main()
