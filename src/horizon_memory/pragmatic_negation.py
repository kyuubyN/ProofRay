# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pragmatic negation / sarcasm detection -- opt-in, deterministic, zero-LLM signal for whether
an utterance's literal positive wording should be read as its own genuine assertion or as an
ironic comment on a negative state of affairs (Sperber & Wilson 1981/2012, echoic mention theory
of irony).

**Not wired into any default routing/ranking/ingestion path.** This is a standalone function a
caller applies explicitly when they know their input may contain sarcastic/ironic praise they
want flagged (or its polarity inverted) rather than ingested as a literal positive assertion.

**Origin and a real, corrected design mistake (2026-08-19)**: a first version (from an external
code-review pass) scored "positive word + negative word anywhere in the same text" as sarcasm via
a hand-tuned weighted sigmoid (5 continuous weights, 2 thresholds -- all apparently calibrated
against its own author's test set). Verified against an independently-constructed adversarial set
before trusting it and found decisively wrong 4/7 -- it flagged ordinary adversity-then-recovery
narratives ("The deploy crashed, but the team recovered brilliantly") as sarcasm, because it never
checked WHAT the positive word was evaluating. The fix is not more lexicon, it's scope: a positive
word landing on the SAME clause/referent as the negative event is the real echoic-mention
signature of irony; a positive word on the OTHER side of a contrastive ("but"/"mas"/"however") or
concessive ("apesar de"/"although") clause boundary is evaluating something else entirely (the
response to the problem, not the problem itself) -- and that is sincere narrative structure, not
sarcasm. Re-tested against a 29-case adversarial set (EN+PT, both directions, both marker types,
explicit-inversion and incongruent-emoji paths included) after this fix: 29/29. Replaced the
sigmoid/weight/threshold scoring with a plain boolean-OR of distinct, individually-testable
signals -- no continuous parameters left to (over)fit against any one test set.

**Explicitly out of scope, not attempted**: Chinese. Sarcasm markers and clause-contrast
structure are not simply transliterations of the PT/EN patterns here, and this module has not
been validated on CJK text at all -- `is_sarcastic` will not fire on Chinese input, by
construction (the lexicons below are PT/EN only), which is the safe (fail-closed), not the
correct, behavior for that language.

**Still not solved**: sarcasm that uses no lexicon word from either list at all -- e.g. "Wow,
another 3-hour meeting that could've been an email. Living the dream." -- relies on hyperbole/
irony this module has no signal for. Confirmed as a real, distinct, unsolved gap during
development, not silently dropped."""
from __future__ import annotations

import re
from dataclasses import dataclass

# Lexicons adapted from an external code-review pass's own word lists (the scoring logic around
# them, not the lists themselves, was the defect) -- English/Portuguese only, see module docstring.
_POSITIVE_HYPERBOLE_PT = frozenset((
    "maravilha", "maravilhoso", "maravilhosa", "ótimo", "otimo", "ótima", "otima",
    "excelente", "perfeito", "perfeita", "perfeitamente", "adorei", "adoro", "amei",
    "super útil", "super util", "muito útil", "muito util", "genial", "muito bom",
    "muito boa", "sensacional", "show", "parabéns", "parabens", "lindo", "linda",
    "incrível", "incrivel", "brilhante", "sucesso", "ajudou muito", "sempre bom",
    "uma beleza", "beleza pura", "um amor", "super rápido", "super rapido", "campeão",
    "campeao", "fantástico", "fantastico",
))
_POSITIVE_HYPERBOLE_EN = frozenset((
    "great", "wonderful", "fantastic", "awesome", "brilliant", "perfect", "perfectly",
    "love it", "loved", "super useful", "so useful", "very useful", "amazing", "genius",
    "good job", "nice work", "congratulations", "totally", "so helpful", "always fun",
    "pure joy", "super fast", "masterpiece", "thrilled", "delightful", "impressed",
    "flawless", "spectacular",
))
_NEGATIVE_SITUATION_PT = frozenset((
    "caiu", "quebrou", "travou", "morreu", "falhou", "falha", "erro", "bug",
    "preso", "presa", "trânsito", "transito", "atraso", "atrasou", "atrasado", "atrasada",
    "perdi", "perdeu", "estragou", "deu ruim", "lento", "lenta", "horrível",
    "horrivel", "lixo", "desastre", "não funciona", "nao funciona", "não serve",
    "nao serve", "parou", "cancelou", "cancela", "cancelado", "cancelada", "apagou", "sumiu",
    "bloqueou", "bloqueado", "estourou", "queimou", "perdemos", "prejuízo", "prejuizo",
    "derrota", "nem compila", "apagaram", "destruiu",
))
_NEGATIVE_SITUATION_EN = frozenset((
    "crashed", "broke", "broken", "failed", "failure", "freeze", "frozen", "froze",
    "stuck", "traffic", "delayed", "late", "lost", "ruined", "slow", "horrible",
    "garbage", "trash", "disaster", "doesn't work", "doesnt work", "not working",
    "stopped", "down", "bug", "error", "offline", "deleted", "vanished", "blocked",
    "exploded", "bricked", "ruin", "waste", "useless", "nightmare", "wiped out",
    "wiped", "nothing works",
))

# "zero reported bugs", "sem falhas conhecidas" -- a negative-situation word inside its own
# negated-count context is a real success metric, not a negative situation. Masked out before
# lexicon scoring so it never contributes to either side.
_NEGATED_NEGATIVE_PT = re.compile(
    r"\b(zero|sem|nenhum|nenhuma|0|sem\s+nenhum)\s+(?:reportado|conhecido|aberto)?\s*"
    r"(bug|bugs|erro|erros|falha|falhas|problema|problemas)\b", re.IGNORECASE)
_NEGATED_NEGATIVE_EN = re.compile(
    r"\b(zero|no|without|0)\s+(?:reported|known|open|unresolved|identified|fatal)?\s*"
    r"(bug|bugs|error|errors|failure|failures|issues|problem|problems)\b", re.IGNORECASE)

# Concessive markers introduce a subordinate clause, conventionally closed by a comma before the
# main clause: "Apesar de X, Y" -- X and Y are different referents even though both technically
# fall "after" the marker in a naive before/after split.
_CONCESSIVE = re.compile(r"\b(apesar de|apesar do|apesar da|embora|although|despite)\b",
                         re.IGNORECASE)
# Coordinating markers sit BETWEEN two already-separate clauses -- a plain before/after split at
# the marker itself is enough.
_COORDINATING = re.compile(
    r"\b(but|however|mas|por[eé]m|nevertheless|nonetheless|though|honestly|felizmente|"
    r"ainda bem que)\b", re.IGNORECASE)

_EXPLICIT_INVERSION_PT = re.compile(
    r"\b(sqn|s[oó]\s+que\s+n[aã]o|s[oó]qn|confia|vai\s+nessa|senta\s+l[aá]\s+cl[aá]udia|"
    r"aham\s+sei|conta\s+outra|t[oô]\s+sabendo|t[aá]\s+serto|t[aá]\s+serto\s+n[eé]|"
    r"imagina\s+se\s+n[aã]o|aham\s+claro|s[oó]\s+acredito\s+vendo)\b", re.IGNORECASE)
_EXPLICIT_INVERSION_EN = re.compile(
    r"\b(sqn|as\s+if|yeah\s+right|yeah\s+sure|like\s+that['’]?s\s+ever\s+gonna\s+happen|"
    r"like\s+that\s+will\s+work|in\s+your\s+dreams|sure\s+thing\s+buddy|"
    r"oh\s+sure\s+thing|suuure|riiiight|no\s+way\s+that['’]?s\s+true)\b", re.IGNORECASE)

_INCONGRUENT_EMOJIS = frozenset(("🙃", "🙄", "😒", "😏", "🤡", "🤦", "💩", "👀"))
_IRONY_EMOTICONS = frozenset(("¬¬", "¬_¬", "-_-", ":/", ":-/"))


def _lexicon_scores(text: str) -> tuple[int, int]:
    lowered = text.lower()
    lowered = _NEGATED_NEGATIVE_PT.sub(" ", lowered)
    lowered = _NEGATED_NEGATIVE_EN.sub(" ", lowered)
    pos = sum(1 for phrase in _POSITIVE_HYPERBOLE_PT if phrase in lowered)
    pos += sum(1 for phrase in _POSITIVE_HYPERBOLE_EN
              if re.search(r"\b" + re.escape(phrase) + r"\b", lowered))
    neg = sum(1 for phrase in _NEGATIVE_SITUATION_PT if phrase in lowered)
    neg += sum(1 for phrase in _NEGATIVE_SITUATION_EN
              if re.search(r"\b" + re.escape(phrase) + r"\b", lowered))
    return pos, neg


def _referent_segments(text: str) -> tuple[str, ...]:
    """Splits `text` around the first contrastive/concessive marker so each segment roughly
    corresponds to one referent -- see the module docstring for why concessive and coordinating
    markers need different split shapes."""
    concessive = _CONCESSIVE.search(text)
    if concessive:
        rest = text[concessive.end():]
        comma_index = rest.find(",")
        if comma_index != -1:
            return (text[:concessive.start()], rest[:comma_index], rest[comma_index + 1:])
        return (text[:concessive.start()], rest)
    coordinating = _COORDINATING.search(text)
    if coordinating:
        return (text[:coordinating.start()], text[coordinating.end():])
    return (text,)


def _has_same_referent_disparity(text: str) -> bool:
    pos, neg = _lexicon_scores(text)
    if pos == 0 or neg == 0:
        return False
    return any(_lexicon_scores(segment) == (segment_pos, segment_neg)
              and segment_pos > 0 and segment_neg > 0
              for segment in _referent_segments(text)
              for segment_pos, segment_neg in (_lexicon_scores(segment),))


@dataclass(frozen=True)
class PragmaticNegationResult:
    is_sarcastic: bool
    reason: str  # "explicit_marker" | "incongruent_emoji" | "same_referent_disparity" | "none"


def detect_pragmatic_negation(text: str) -> PragmaticNegationResult:
    """Deterministic sarcasm/irony signal for PT/EN text (see module docstring for CJK scope).
    Checked in order of confidence: an explicit inversion phrase ("yeah right", "aham sei") is
    checked first since it needs no other signal to be trustworthy; an incongruent emoji/emoticon
    only counts alongside a real lexicon hit (an emoji alone is too weak on its own); same-
    referent disparity (a positive word evaluating the very clause/event a negative word also
    describes, with no contrastive/concessive marker separating them onto different referents) is
    checked last, since it depends on the other two having already ruled out more specific cues."""
    if _EXPLICIT_INVERSION_PT.search(text) or _EXPLICIT_INVERSION_EN.search(text):
        return PragmaticNegationResult(True, "explicit_marker")
    has_ironic_symbol = (any(symbol in text for symbol in _INCONGRUENT_EMOJIS) or
                        any(symbol in text for symbol in _IRONY_EMOTICONS))
    if has_ironic_symbol:
        pos, neg = _lexicon_scores(text)
        if pos > 0 or neg > 0:
            return PragmaticNegationResult(True, "incongruent_emoji")
    if _has_same_referent_disparity(text):
        return PragmaticNegationResult(True, "same_referent_disparity")
    return PragmaticNegationResult(False, "none")


__all__ = ["PragmaticNegationResult", "detect_pragmatic_negation"]
