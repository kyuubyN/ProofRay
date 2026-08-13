# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Closed, observable pragmatic sockets for puzzle-piece transport."""
from __future__ import annotations

import re


_QUERY = {
    "identity": r"\b(?:identity|identify|gender|orientation)\b",
    "occupation": r"\b(?:career|job|profession|work(?:ing)?|venture|business)\b",
    "attitude": r"\b(?:think|opinion|view|reaction|react)\b",
    "emotion": r"\b(?:feel|felt|emotion|mood)\b",
    "counsel": r"\b(?:advice|advise|recommend|suggest|tip)\b",
    "preference": r"\b(?:favorite|prefer|like best|top pick)\b",
    "quantity": r"\b(?:how many|how much|number of)\b",
    "temporal": r"\b(?:when|how long|what year|what date)\b",
    "intent": r"\b(?:plan|intend|decision|decide|future|summer)\b",
    "purpose": r"\b(?:used for|purpose|why|what for)\b",
    "activity": r"\b(?:activities|activity|done|do with|went)\b",
    "relationship": r"\b(?:relationship|married|single|partner|husband|wife)\b",
    "possession": r"\b(?:pets?|have|has|own)\b",
}

_DOCUMENT = {
    "identity": r"\b(?:identity|transgender|trans |lgbtq|gay|lesbian|bisexual|nonbinary)\b",
    "occupation": r"\b(?:career|job|profession|work(?:ing)?|counsel(?:ing|or)?|therap|mental health|business|studio)\b",
    "attitude": r"\b(?:amazing|lovely|awesome|wonderful|great|proud|support|admire|appreciat|impress|inspir)\w*\b",
    "emotion": r"\b(?:feel|felt|awe|happy|sad|excited|nervous|scared|afraid|tiny|thankful|grateful|overwhelm)\w*\b",
    "counsel": r"\b(?:make sure|be sure|don't forget|do not forget|you should|need to|advice|recommend|suggest|remember to)\b",
    "preference": r"\b(?:favorite|prefer|top pick|love all|fan of|speaks? to me)\b",
    "quantity": r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b",
    "temporal": r"\b(?:today|tomorrow|yesterday|week|month|year|monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
    "intent": r"\b(?:plan|going to|gonna|want to|hope to|thinking of|decision|researching|looking forward)\b",
    "purpose": r"\b(?:used for|for walking|for running|because|so that|in order to|due to)\b",
    "activity": r"\b(?:went|took|made|painting|pottery|camping|swimming|museum|hike|reading|running)\b",
    "relationship": r"\b(?:single parent|married|husband|wife|partner|breakup|relationship)\b",
    "possession": r"\b(?:my (?:cat|dog|pet)|got (?:a|another)|have (?:a|an)|named \w+)\b",
}


def observe_pragmatic_roles(text: str, *, question: bool = False) -> tuple[str, ...]:
    if not isinstance(text, str):
        raise TypeError("pragmatic role surface must be text")
    patterns = _QUERY if question else _DOCUMENT
    lowered = text.casefold().replace("’", "'")
    return tuple(sorted(role for role, pattern in patterns.items()
                        if re.search(pattern, lowered, re.IGNORECASE)))
