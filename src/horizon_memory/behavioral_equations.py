# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bind agent, behavior and possibility into query-relative equations."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from .pragmatic_roles import observe_pragmatic_roles
from .raw_causal_channels import RawCausalDocument, RawCausalSyndromeIndex, observe_raw_text


_QUERY_EXTENSIONS = {
    "attitude": r"\b(?:stance|leaning)\b",
    "preference": r"\bwould .{0,40} enjoy\b",
    "activity": r"\bkind of (?:art|painting)\b",
}
_DOCUMENT_EXTENSIONS = {
    "emotion": r"\bappreciat\w*\b",
    "possession": r"\b(?:son|daughter|child|children|kids?|husband|wife|family|brother|sister)\b",
}


def _roles(text: str, *, question: bool) -> tuple[str, ...]:
    roles = set(observe_pragmatic_roles(text, question=question))
    patterns = _QUERY_EXTENSIONS if question else _DOCUMENT_EXTENSIONS
    lowered = text.casefold().replace("’", "'")
    roles.update(role for role, pattern in patterns.items() if re.search(pattern, lowered))
    return tuple(sorted(roles))


@dataclass(frozen=True)
class BehavioralQueryEquation:
    agents: tuple[str, ...]
    required_roles: tuple[str, ...]
    domain_tokens: tuple[str, ...]
    counterfactual: bool
    collective: bool


@dataclass(frozen=True)
class BehavioralScore:
    fact_id: int
    amplitude: float
    lexical: float
    agent_binding: float
    role_binding: float
    possibility: float
    witness_fact_ids: tuple[int, ...]


@dataclass(frozen=True)
class BehavioralClosure:
    state: str
    fact_id: int | None
    coverage: float
    margin: float
    evidence_fact_ids: tuple[int, ...]
    missing_slots: tuple[str, ...]
    reason: str


class BehavioralEquationIndex:
    """Score a fact as an equation, not as an unbound bag of signals.

    Speaker/body and pragmatic role must belong to the same FactId. Counterfactual
    possibility is released only by a verified disposition-like role in that fact. The
    scorer preserves raw FactId provenance and never turns a likely projection into proof.
    """

    def __init__(self, documents: tuple[RawCausalDocument, ...]):
        if not documents or tuple(document.fact_id for document in documents) != \
                tuple(sorted({document.fact_id for document in documents})):
            raise ValueError("behavior documents must be non-empty and canonical")
        self.documents = documents
        self.raw = RawCausalSyndromeIndex(documents)
        self.speakers = tuple(sorted({document.speaker for document in documents
                                      if document.speaker}))
        self.roles = {document.fact_id: set(_roles(document.text, question=False))
                      for document in documents}

    def equation(self, query_text: str) -> BehavioralQueryEquation:
        lowered = query_text.casefold().replace("’", "'")
        agents = tuple(speaker for speaker in self.speakers
                       if re.search(rf"(?<!\w){re.escape(speaker.casefold())}(?!\w)", lowered))
        roles = _roles(query_text, question=True)
        raw = observe_raw_text(query_text, question=True)
        agent_tokens = {token for speaker in agents
                        for token in observe_raw_text(speaker).lexical}
        domain = tuple(token for token in raw.lexical if token not in agent_tokens)
        counterfactual = bool(re.search(r"\b(?:likely|would|could|might|probably)\b", lowered))
        collective = bool(re.search(r"\b(?:what activities|which activities|how many|both|all)\b",
                                    lowered))
        return BehavioralQueryEquation(agents, roles, domain, counterfactual, collective)

    def rank(self, query_text: str, *, behavior_weight: float = 1.0,
             lexical_weight: float = 1.0, sublexical_weight: float = .25) \
            -> tuple[BehavioralScore, ...]:
        if min(behavior_weight, lexical_weight, sublexical_weight) < 0 \
                or behavior_weight + lexical_weight + sublexical_weight == 0:
            raise ValueError("behavior equation needs positive observable mass")
        equation = self.equation(query_text)
        components = {item.fact_id: item for item in self.raw.components(query_text)}
        disposition_roles = {"attitude", "emotion", "identity", "intent", "preference",
                             "purpose", "occupation"}
        result = []
        for document in self.documents:
            raw = components[document.fact_id]
            agent = (1.0 if not equation.agents else
                     float(document.speaker in equation.agents))
            roles = self.roles[document.fact_id]
            role = (0.0 if not equation.required_roles else
                    len(set(equation.required_roles) & roles) /
                    len(equation.required_roles))
            # Binding is conjunctive: another speaker's role cannot be borrowed.
            binding = math.sqrt(agent * role) if role else 0.0
            possibility = float(equation.counterfactual and agent > 0
                                and bool(roles & disposition_roles))
            amplitude = (lexical_weight * raw.lexical
                         + sublexical_weight * raw.sublexical
                         + behavior_weight * (binding + .5 * possibility))
            result.append(BehavioralScore(document.fact_id, amplitude, raw.lexical,
                                          agent, role, possibility, (document.fact_id,)))
        return tuple(sorted(result, key=lambda item: (-item.amplitude, -item.role_binding,
                                                       -item.agent_binding, item.fact_id)))

    def close(self, query_text: str, *, minimum_margin: float = .05) -> BehavioralClosure:
        if minimum_margin < 0:
            raise ValueError("behavior closure margin cannot be negative")
        equation = self.equation(query_text)
        components = {item.fact_id: item for item in self.raw.components(query_text)}
        measured = []
        for document in self.documents:
            raw = components[document.fact_id]
            agent = (1.0 if not equation.agents else
                     float(document.speaker in equation.agents))
            roles = self.roles[document.fact_id]
            role = (1.0 if not equation.required_roles else
                    len(set(equation.required_roles) & roles) /
                    len(equation.required_roles))
            # Sublexical resonance may retrieve candidates but cannot certify that a
            # causal answer concerns the queried object.
            domain = raw.lexical
            slots = {"agent": agent, "role": role, "domain": domain}
            missing = tuple(sorted(name for name, value in slots.items() if value <= 0))
            coverage = 1 - len(missing) / len(slots)
            strength = math.prod(max(value, 1e-12) for value in slots.values()) ** (1 / 3)
            measured.append((not missing, strength, coverage, document.fact_id, missing))
        measured.sort(key=lambda item: (-item[0], -item[1], item[3]))
        complete, strength, coverage, candidate, missing = measured[0]
        if equation.counterfactual:
            return BehavioralClosure("abstain", None, coverage, 0, (),
                                     ("external_possibility_model",),
                                     "a disposition projection is not a verified entailment")
        if equation.collective:
            return BehavioralClosure("abstain", None, coverage, 0, (),
                                     ("collective_completion",),
                                     "one fact cannot certify an open-world collective")
        if not complete:
            return BehavioralClosure("abstain", None, coverage, 0, (), missing,
                                     "agent, behavior and domain do not close on one FactId")
        runner_up = next((item[1] for item in measured[1:] if item[0]), 0.0)
        margin = (strength - runner_up) / max(strength, 1e-12)
        if margin < minimum_margin:
            return BehavioralClosure("contested", None, coverage, margin, (), (),
                                     "two complete behavioral equations remain plausible")
        return BehavioralClosure("committed", candidate, coverage, margin, (candidate,), (),
                                 "one FactId closes agent, behavior and domain")
