# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic, zero-LLM content-safety screening for ingestion and retrieval.

Design intent, stated explicitly because this is a safety-critical module: this is a real,
visible, editable, auditable gate -- every pattern lives in this one file, in plain text,
versioned in git, exactly like every other part of this codebase. It is deliberately NOT hidden,
obfuscated, or resistant to discovery or removal -- a safety mechanism nobody can find, read, or
turn off is not a safety mechanism a project's own maintainers or users can trust or audit, and
an unauditable gate is itself a supply-chain risk, not a protection. Anyone running Horizon
Memory can read this file, see exactly what it checks for, and adjust `SafetyPolicy` to fit their
own deployment.

Honest scope, stated up front rather than implied: this is a KEYWORD/PATTERN heuristic, not a
semantic classifier -- consistent with this project's "no LLM in the memory core" ground rule
(see CLAUDE.md), it is fast, deterministic, and has no external dependency, but it will miss
content phrased to evade simple pattern matching, and it can false-positive on legitimate
content (security research, medical literature, historical/journalistic writing, fiction) that
happens to share vocabulary with the categories below. It is a first-line, best-effort filter,
not a guarantee -- and for CSAM specifically (see `_screen_csam`'s own docstring) a text
heuristic like this one is NOT sufficient on its own; real deployments handling that category
need dedicated infrastructure (hash-matching against known-content databases, e.g. services
built on PhotoDNA/NCMEC hash sets, plus mandatory legal reporting), not just this module.

A confidently WRONG safety verdict (silently allowing something dangerous, or silently blocking
something legitimate with no explanation) is worse than an honest, visible abstention -- the
same "abstention over guessing" principle this project already applies to answer generation
applies here to safety screening. `UnsafeContentError` always carries which category tripped and
a short, non-graphic reason, and never echoes the flagged text itself into logs/exceptions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class SafetyCategory(Enum):
    PHYSICAL_HARM = "physical_harm"
    MALWARE = "malware"
    SENSITIVE_PII = "sensitive_pii"
    CSAM = "csam"


class UnsafeContentError(ValueError):
    """Raised when text fails a safety screen. Carries the triggering category and a short,
    non-graphic reason -- never the matched text itself, so the exception's own message/traceback
    never becomes a copy of the flagged content."""

    def __init__(self, category: SafetyCategory, reason: str):
        self.category = category
        self.reason = reason
        super().__init__(f"unsafe content blocked: {category.value} ({reason})")


@dataclass(frozen=True)
class SafetyVerdict:
    safe: bool
    category: SafetyCategory | None = None
    reason: str | None = None


@dataclass(frozen=True)
class SafetyPolicy:
    """Every category below CSAM is individually toggleable -- a deployment doing legitimate
    security research or medical/pharmacological work may need to disable the corresponding
    check and rely on other controls instead; that is a real, defensible operational choice and
    this policy makes it explicit and auditable rather than forcing a silent workaround.

    CSAM has no corresponding flag: it is always checked, on every policy, with no override.
    That asymmetry is deliberate, not an oversight."""

    check_physical_harm: bool = True
    check_malware: bool = True
    check_sensitive_pii: bool = True


DEFAULT_POLICY = SafetyPolicy()
_ALL_CHECKS_OFF = SafetyPolicy(check_physical_harm=False, check_malware=False,
                               check_sensitive_pii=False)


# --------------------------------------------------------------------------------------------
# Physical harm: co-occurrence of an instructional/construction phrase with a weapon/explosive/
# poison noun. Requires BOTH halves in the same text -- a document that only names a weapon
# (news, history, fiction, policy discussion) or only uses a construction verb (cooking,
# electronics, carpentry) does not trip this on its own; only the combination does.
# --------------------------------------------------------------------------------------------
_HARM_INSTRUCTION_VERBS = re.compile(
    r"\b(how\s+to\s+(?:make|build|construct|assemble|synthesize|manufacture)|"
    r"step[\s-]by[\s-]step\s+(?:instructions?|guide)\s+(?:for|to)|"
    r"instructions?\s+(?:for|to)\s+(?:make|build|construct|assemble|synthesize))\b",
    re.IGNORECASE)
_HARM_NOUNS = re.compile(
    r"\b(pipe\s*bomb|explosive\s+device|homemade\s+explosive|detonator|nerve\s+agent|"
    r"chemical\s+weapon|biological\s+weapon|bioweapon|nail\s+bomb|improvised\s+explosive|"
    r"lethal\s+(?:gas|poison|toxin)|weaponi[sz]ed\s+(?:pathogen|virus|toxin))\b",
    re.IGNORECASE)


def _screen_physical_harm(text: str) -> SafetyVerdict:
    if _HARM_INSTRUCTION_VERBS.search(text) and _HARM_NOUNS.search(text):
        return SafetyVerdict(False, SafetyCategory.PHYSICAL_HARM,
                             "instructional phrasing co-occurring with a weapon/explosive/"
                             "poison term")
    return SafetyVerdict(True)


# --------------------------------------------------------------------------------------------
# Malware: co-occurrence of malicious-intent phrasing with attack-tool vocabulary or a known
# attack-tool code pattern (a reverse-shell one-liner, a destructive rm -rf root wipe, an
# obfuscated base64-then-eval loader). Legitimate security-research/defensive-tooling text that
# merely DISCUSSES these concepts (a paper explaining how ransomware works, a CVE writeup) does
# not trip this unless it also carries the intent phrasing OR the literal exploit pattern.
# --------------------------------------------------------------------------------------------
_MALWARE_INTENT = re.compile(
    r"\b(write|create|build)\s+(?:me\s+)?(?:a\s+)?(ransomware|keylogger|rootkit|worm|"
    r"trojan)\b|"
    r"\b(steal|exfiltrate)\s+(?:passwords|credentials|credit\s*card)\b.{0,40}\b(script|code|"
    r"payload)\b",
    re.IGNORECASE)
_MALWARE_CODE_PATTERN = re.compile(
    r"nc\s+-e\s+/bin/(?:sh|bash)|"                       # classic reverse-shell one-liner
    r"rm\s+-rf\s+/(?:\s|$)|"                              # destructive root wipe
    r"eval\s*\(\s*base64_decode\s*\(|"                    # obfuscated PHP loader
    r":(){ :\|:& };:",                                    # fork bomb
    re.IGNORECASE)


def _screen_malware(text: str) -> SafetyVerdict:
    if _MALWARE_CODE_PATTERN.search(text):
        return SafetyVerdict(False, SafetyCategory.MALWARE,
                             "literal known attack-tool code pattern")
    if _MALWARE_INTENT.search(text):
        return SafetyVerdict(False, SafetyCategory.MALWARE,
                             "malicious-intent phrasing targeting an attack tool")
    return SafetyVerdict(True)


# --------------------------------------------------------------------------------------------
# Sensitive PII / credentials: structural patterns (a private key header, a common cloud/API
# key prefix, a plausible credit-card digit run, an inline password= assignment) -- these are
# things that should never be persisted or echoed back by a memory system regardless of who is
# asking, independent of any "dangerous intent" framing.
# --------------------------------------------------------------------------------------------
_PII_PATTERNS = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"\b(?:sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,})\b|"
    r"password\s*[:=]\s*\S{4,}|"
    r"\b\d{3}-\d{2}-\d{4}\b|"                             # SSN-shaped
    r"\b(?:\d[ -]*?){13,16}\b(?=.{0,20}(?:card|cvv|exp))",  # card-number-shaped, near card context
    re.IGNORECASE)


def _screen_sensitive_pii(text: str) -> SafetyVerdict:
    if _PII_PATTERNS.search(text):
        return SafetyVerdict(False, SafetyCategory.SENSITIVE_PII,
                             "structural pattern matching a private key, API credential, "
                             "password assignment, or government/financial ID")
    return SafetyVerdict(True)


# --------------------------------------------------------------------------------------------
# CSAM: zero-tolerance, always checked, no policy override anywhere in this module.
#
# Stated honestly rather than implied: a text keyword heuristic is NOT adequate protection for
# this category on its own -- real-world evasion of keyword matching is trivial, and the
# consequence of a false negative here is categorically worse than in any other category. This
# check exists as a narrow, best-effort, always-on FIRST-LINE signal (deliberately using
# generic, non-graphic, industry-standard trigger terms, not an exhaustive list), and any
# production deployment handling untrusted content at scale MUST pair it with dedicated
# infrastructure this module does not attempt to replace: hash-matching against known-content
# databases (e.g. PhotoDNA/NCMEC-derived hash sets), specialized classifiers, and mandatory
# legal reporting obligations in the operator's own jurisdiction. Treat a SAFE verdict from this
# function as "did not trip the narrow first-line signal," never as "confirmed safe."
# --------------------------------------------------------------------------------------------
# 2026-08-18: first version used a bare `sexual(?:ly)?` trigger and false-positived on real,
# entirely legitimate MemGym-DR corpus text discussing the EU's "Child Sexual Abuse Regulation"
# and the UK's "EARN IT Act" -- policy/legal-instrument names, not exploitative content. Caught
# by running the real corpus through the pipeline before trusting this module, not assumed safe
# from unit tests alone. Fixed two ways: (1) strip known legal-instrument name patterns before
# matching, so a law's own name never contributes either signal; (2) require a genuinely graphic
# indicator (sexually explicit / pornographic / nude images), not the bare word "sexual," which
# appears constantly in completely unrelated legitimate contexts (policy, law, health, biology).
_CSAM_AGE_INDICATOR = re.compile(
    r"\b(?:child|minor|underage|pre[\s-]?teen|toddler)\b", re.IGNORECASE)
_CSAM_EXPLICIT_INDICATOR = re.compile(
    r"\bsexually\s+explicit\b|\bexplicit\s+(?:sexual\s+)?(?:image|content|material|photo|"
    r"video)s?\b|\bpornograph|\bnude\s+(?:image|photo|picture)s?\b|\bsexual\s+abuse\s+"
    r"material\b(?!\s*(?:act|regulation|law|bill|directive))", re.IGNORECASE)
_CSAM_LEGAL_INSTRUMENT = re.compile(
    r"child\s+sexual\s+abuse\b\s*(?:material\s+)?(?:act|regulation|law|bill|directive|"
    r"prevention|protection|legislation|policy|task\s*force|hotline|initiative)\b",
    re.IGNORECASE)


def _screen_csam(text: str) -> SafetyVerdict:
    stripped = _CSAM_LEGAL_INSTRUMENT.sub(" ", text)
    if _CSAM_AGE_INDICATOR.search(stripped) and _CSAM_EXPLICIT_INDICATOR.search(stripped):
        return SafetyVerdict(False, SafetyCategory.CSAM,
                             "age indicator co-occurring with sexually explicit content "
                             "indicator")
    return SafetyVerdict(True)


def screen_text(text: str, policy: SafetyPolicy = DEFAULT_POLICY) -> SafetyVerdict:
    """Runs every check the policy enables, plus CSAM unconditionally, in a fixed order (CSAM
    first -- the zero-tolerance category should never be shadowed by an earlier, lower-severity
    match). Returns the FIRST failing verdict; does not aggregate multiple simultaneous
    categories, since a single reason is what `UnsafeContentError` needs to report."""
    if not isinstance(text, str):
        raise TypeError("text must be str")
    for check, enabled in (
        (_screen_csam, True),
        (_screen_physical_harm, policy.check_physical_harm),
        (_screen_malware, policy.check_malware),
        (_screen_sensitive_pii, policy.check_sensitive_pii),
    ):
        if not enabled:
            continue
        verdict = check(text)
        if not verdict.safe:
            return verdict
    return SafetyVerdict(True)


__all__ = [
    "SafetyCategory", "SafetyPolicy", "SafetyVerdict", "UnsafeContentError",
    "DEFAULT_POLICY", "screen_text",
]
