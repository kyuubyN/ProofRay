# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Auditable surface transport for deterministic lexical addressing."""
from __future__ import annotations
from .raw_causal_channels import observe_raw_text

def gauge_lemma(token: str) -> str:
    """Coarsen a raw-text stem by one silent-e gauge transformation.

    This is an address orbit, never a semantic equivalence or proof charge.
    """
    value=token.casefold().replace("’", "'").strip("'")
    if value.endswith("ies") and len(value) > 4:
        value = value[:-3] + "y"
    elif value.endswith("ed") and len(value) > 4:
        value = value[:-2]
    elif value.endswith("ing") and len(value) > 5:
        value = value[:-3]
    elif value.endswith("s") and len(value) > 4:
        value = value[:-1]
    if len(value) >= 3 and value[-1].isalpha() and value[-1] == value[-2] \
            and value[-1] not in "aeiou":
        value = value[:-1]
    return value[:-1] if len(value) >= 4 and value.endswith("e") else value

def observe_gauge_lexical(text: str) -> frozenset[str]:
    return frozenset(gauge_lemma(token) for token in observe_raw_text(text).lexical)
