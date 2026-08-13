# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compile observable behavior into minimal causal transitions and execute a tiny DSL."""
from __future__ import annotations

import re
from dataclasses import dataclass

from .behavioral_equations import _roles
from .raw_causal_channels import RawCausalDocument, observe_raw_text


_CAUSE = re.compile(r"\b(?:because|since|in order to|so that|'cause|cause)\b", re.IGNORECASE)
_NUMBER = re.compile(r"\b(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)\b",
                     re.IGNORECASE)
_TIME_UNIT = re.compile(r"\b(?:day|week|month|year)s?\b", re.IGNORECASE)


@dataclass(frozen=True, order=True)
class BehaviorTransition:
    fact_id: int
    agent: str
    action_tokens: tuple[str, ...]
    cause_tokens: tuple[str, ...]
    roles: tuple[str, ...]
    quantities: tuple[str, ...]
    time_units: tuple[str, ...]
    exact_cause_span: str | None


@dataclass(frozen=True, order=True)
class BehaviorProgram:
    operator: str
    agents: tuple[str, ...]
    target_tokens: tuple[str, ...]
    required_roles: tuple[str, ...]


@dataclass(frozen=True)
class BehaviorExecution:
    state: str
    operator: str
    fact_ids: tuple[int, ...]
    value_spans: tuple[str, ...]
    missing_slots: tuple[str, ...]
    reason: str


class ObservableBehaviorMachine:
    """Execute only relations materialized in source transitions.

    The compiler is intentionally incomplete. Unsupported projection/aggregation remains
    explicit rather than being approximated by similarity.
    """

    def __init__(self, documents: tuple[RawCausalDocument, ...]):
        if not documents or tuple(document.fact_id for document in documents) != \
                tuple(sorted({document.fact_id for document in documents})):
            raise ValueError("behavior machine documents must be FactId-canonical")
        self.documents = documents
        self.speakers = tuple(sorted({document.speaker for document in documents
                                      if document.speaker}))
        self.transitions = tuple(self._compile(document) for document in documents)

    @staticmethod
    def _tokens(text: str) -> tuple[str, ...]:
        return observe_raw_text(text).lexical

    def _compile(self, document: RawCausalDocument) -> BehaviorTransition:
        match = _CAUSE.search(document.text)
        if match:
            action_text = document.text[:match.start()]
            cause_text = document.text[match.end():].strip(" ,.!?;:-")
            cause_span = cause_text or None
        else:
            action_text, cause_text, cause_span = document.text, "", None
        return BehaviorTransition(
            document.fact_id, document.speaker, self._tokens(action_text),
            self._tokens(cause_text), _roles(document.text, question=False),
            tuple(match.group(0).casefold() for match in _NUMBER.finditer(document.text)),
            tuple(match.group(0).casefold() for match in _TIME_UNIT.finditer(document.text)),
            cause_span)

    def compile_query(self, query_text: str) -> BehaviorProgram:
        lowered = query_text.casefold().replace("’", "'")
        agents = tuple(speaker for speaker in self.speakers
                       if re.search(rf"(?<!\w){re.escape(speaker.casefold())}(?!\w)", lowered))
        roles = _roles(query_text, question=True)
        raw_tokens = set(observe_raw_text(query_text, question=True).lexical)
        for speaker in agents:
            raw_tokens.difference_update(observe_raw_text(speaker).lexical)
        if re.search(r"\b(?:likely|would|could|might|probably)\b", lowered):
            operator = "PROJECT_DISPOSITION"
        elif re.search(r"\b(?:what activities|which activities|how many|both|all)\b", lowered):
            operator = "AGGREGATE"
        elif re.search(r"\bwhy\b", lowered):
            operator = "EXPLAIN_CAUSE"
        elif re.search(r"\bhow long\b", lowered):
            operator = "DURATION"
        else:
            operator = "LOOKUP_STATE"
        return BehaviorProgram(operator, agents, tuple(sorted(raw_tokens)), roles)

    def rank_structural_candidates(self, query_text: str) -> tuple[int, ...]:
        """Rank sources that can physically instantiate the compiled operator.

        This is candidate generation, not execution: missing lexical overlap lowers a
        transition but does not erase it.  The hard admission gate is structural
        (cause span or quantity+time unit), while agent, target and role bindings only
        order admitted transitions.  That keeps semantic paraphrases reachable without
        allowing an operator-incompatible source to masquerade as evidence.
        """
        program = self.compile_query(query_text)
        required_roles = set(program.required_roles)
        candidates = []
        for transition in self.transitions:
            if program.operator == "EXPLAIN_CAUSE":
                structurally_complete = bool(transition.cause_tokens)
            elif program.operator == "DURATION":
                structurally_complete = bool(transition.quantities and transition.time_units)
            else:
                structurally_complete = True
            if not structurally_complete:
                continue
            agent = int(not program.agents or transition.agent in program.agents)
            target = int(self._overlap(program.target_tokens, transition.action_tokens))
            role_coverage = (1.0 if not required_roles else
                             len(required_roles & set(transition.roles)) / len(required_roles))
            candidates.append((agent, target, role_coverage, transition.fact_id))
        candidates.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]))
        return tuple(item[3] for item in candidates)

    @staticmethod
    def _overlap(target: tuple[str, ...], observed: tuple[str, ...]) -> bool:
        return bool(set(target) & set(observed))

    def execute(self, query_text: str) -> BehaviorExecution:
        program = self.compile_query(query_text)
        if program.operator == "PROJECT_DISPOSITION":
            return BehaviorExecution("unsupported", program.operator, (), (),
                                     ("authorized_possibility_edge",),
                                     "counterfactual projection needs an explicit ontology/gauge edge")
        if program.operator == "AGGREGATE":
            return BehaviorExecution("unsupported", program.operator, (), (),
                                     ("closed_world_certificate",),
                                     "aggregation cannot close from an open conversation")
        candidates = []
        required_roles = set(program.required_roles)
        for transition in self.transitions:
            if program.agents and transition.agent not in program.agents:
                continue
            role_coverage = (1.0 if not required_roles else
                             len(required_roles & set(transition.roles)) / len(required_roles))
            target = self._overlap(program.target_tokens, transition.action_tokens)
            if program.operator == "EXPLAIN_CAUSE":
                complete = target and bool(transition.cause_tokens) and role_coverage > 0
                value = transition.exact_cause_span
            elif program.operator == "DURATION":
                complete = target and bool(transition.quantities) and bool(transition.time_units)
                value = " ".join((*transition.quantities, *transition.time_units))
            else:
                complete = target and role_coverage > 0
                value = None
            if complete:
                candidates.append((role_coverage, transition.fact_id, value))
        candidates.sort(key=lambda item: (-item[0], item[1]))
        if not candidates:
            return BehaviorExecution("abstain", program.operator, (), (),
                                     ("complete_transition",),
                                     "no source transition satisfies every program slot")
        best = candidates[0][0]
        tied = [item for item in candidates if item[0] == best]
        if len(tied) > 1 and program.operator != "AGGREGATE":
            return BehaviorExecution("contested", program.operator,
                                     tuple(item[1] for item in tied),
                                     tuple(item[2] for item in tied if item[2]), (),
                                     "multiple complete transitions remain equally typed")
        _, fact_id, value = candidates[0]
        return BehaviorExecution("committed", program.operator, (fact_id,),
                                 (value,) if value else (), (),
                                 "minimal observable transition executed")
