from __future__ import annotations

from dataclasses import dataclass
import re


_NUMBER = re.compile(r"(?<!\w)[+-]?(?:\d+(?:[.,]\d+)?%?)(?!\w)")
_WORD = re.compile(r"[^\W\d_]+(?:[-'][^\W\d_]+)*", re.UNICODE)
_NEGATIONS = frozenset({
    "not", "no", "never", "without", "none", "não", "nao", "nunca", "sem", "nenhum",
})
_FUNCTION_WORDS = frozenset({
    "a", "an", "and", "as", "at", "be", "been", "by", "da", "das", "de",
    "do", "dos", "e", "em", "for", "from", "foi", "is", "it", "na", "nas",
    "no", "nos", "o", "of", "on", "or", "os", "para", "por", "que", "the",
    "to", "um", "uma", "was", "were", "with", "é",
})


@dataclass(frozen=True)
class RewriteDecision:
    accepted: bool
    reason: str


def _numbers(text: str) -> tuple[str, ...]:
    return tuple(item.replace(",", ".") for item in _NUMBER.findall(text.casefold()))


def _negations(text: str) -> frozenset[str]:
    return frozenset(word.casefold() for word in _WORD.findall(text)
                     if word.casefold() in _NEGATIONS)


def _proper_tokens(text: str) -> frozenset[str]:
    words = _WORD.findall(text)
    return frozenset(word.casefold() for word in words
                     if word[:1].isupper() and len(word) > 1)


def _content_lexemes(text: str) -> tuple[str, ...]:
    return tuple(sorted(
        word.casefold() for word in _WORD.findall(text)
        if len(word) >= 3 and word.casefold() not in _FUNCTION_WORDS))


def guard_rewrite(certified: str, candidate: str) -> RewriteDecision:
    """Conservatively protect literals; this never certifies the rewritten text.

    Acceptance means the candidate may be displayed as a rewrite. The certificate
    continues to bind only `certified` and the UI must keep that text reopenable.
    """
    if not certified.strip() or not candidate.strip():
        return RewriteDecision(False, "empty_text")
    if len(candidate.encode("utf-8")) > min(
            24_576, max(512, len(certified.encode("utf-8")) * 2)):
        return RewriteDecision(False, "rewrite_expanded")
    if _numbers(certified) != _numbers(candidate):
        return RewriteDecision(False, "numeric_literals_changed")
    if _negations(certified) != _negations(candidate):
        return RewriteDecision(False, "polarity_changed")
    if _proper_tokens(certified) != _proper_tokens(candidate):
        return RewriteDecision(False, "named_literals_changed")
    if _content_lexemes(certified) != _content_lexemes(candidate):
        return RewriteDecision(False, "protected_details_changed")
    return RewriteDecision(True, "protected_literals_preserved")


__all__ = ["RewriteDecision", "guard_rewrite"]
