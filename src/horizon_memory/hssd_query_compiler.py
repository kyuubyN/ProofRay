# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Structural HSSD query compilation without models or case-specific catalogs.

Address atoms route the query.  Proof obligations authorize execution.  Keeping the
two sets distinct prevents lexical paraphrases from becoming false proof failures and
prevents retrieval scores from masquerading as typed evidence.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .raw_causal_channels import observe_raw_text


_WORDS = re.compile(r"[^\W_]+(?:['’][^\W_]+)?", re.UNICODE)
_UNIT = re.compile(
    r"\b(milliseconds?|seconds?|minutes?|hours?|days?|weeks?|months?|years?|"
    r"bytes?|kilobytes?|megabytes?|gigabytes?|grams?|kilograms?|meters?|kilometers?|"
    r"dollars?|euros?|percent|percentage)\b", re.IGNORECASE)
_SCAFFOLD = frozenset("""
what which when where who whom whose why how many much long did does do is are was were
has have had can could will would tell give find show identify determine calculate
the a an of in on at to from for about according please answer question
date day time person people place location number count total sum reason cause duration
between altogether
""".split())


@dataclass(frozen=True)
class HSSDAddressAtoms:
    lexical: tuple[str, ...]
    entities: tuple[str, ...]
    numbers: tuple[str, ...]
    temporal: tuple[str, ...]
    relations: tuple[str, ...]


@dataclass(frozen=True, order=True)
class HSSDObligation:
    key: str
    kind: str
    cardinality: int = 1

    def __post_init__(self) -> None:
        if not self.key or not self.kind or self.cardinality < 1:
            raise ValueError("invalid HSSD obligation")


@dataclass(frozen=True)
class HSSDQueryPlan:
    state: str
    operation: str
    target: str
    address_atoms: HSSDAddressAtoms
    obligations: tuple[HSSDObligation, ...]
    require_complete: bool
    reason: str


@dataclass(frozen=True)
class HSSDQueryLattice:
    """Finite operator interpretations; never a scored or silently chosen plan.

    ``compile`` remains the conservative single-plan API.  This companion surface is for
    consumers able to execute every surviving interpretation and accept only a proof-backed
    invariant answer.  Preserving COUNT/SUM and LOOKUP/SUM ambiguity is safer than guessing
    from an interrogative alone.
    """

    state: str
    plans: tuple[HSSDQueryPlan, ...]
    address_atoms: HSSDAddressAtoms
    reason: str


@dataclass(frozen=True)
class HSSDEvidenceObservation:
    fact_id: int
    lexical: tuple[str, ...] = ()
    entities: tuple[str, ...] = ()
    roles: tuple[str, ...] = ()
    clocks: tuple[str, ...] = ()
    quantities: tuple[str, ...] = ()
    units: tuple[str, ...] = ()
    causal_edges: int = 0
    distinct_keys: tuple[str, ...] = ()
    proof_verified: bool = False
    complete: bool = False
    conflict: bool = False

    def __post_init__(self) -> None:
        if self.fact_id < 0 or self.causal_edges < 0:
            raise ValueError("invalid HSSD evidence observation")


@dataclass(frozen=True)
class HSSDClosure:
    state: str
    closed: tuple[str, ...]
    residual: tuple[str, ...]
    fact_ids: tuple[int, ...]
    retrieval_closed: bool
    execution_ready: bool
    reason: str


class StructuralHSSDQueryCompiler:
    """Compile query structure from conserved interrogative and operator syntax."""

    _PATTERNS = (
        ("duration", re.compile(
            r"\b(?:how\s+long|duration|how\s+much\s+time|"
            r"how\s+many\s+(?:minutes?|hours?|days?|weeks?|months?|years?)\s+"
            r"(?:ago|had\s+passed|have\s+passed|have\s+i\s+been))\b", re.I)),
        ("interval", re.compile(r"\b(?:time|duration|difference)\s+between\b", re.I)),
        ("count_distinct", re.compile(r"\b(?:how\s+many|number\s+of|count\s+of)\b", re.I)),
        ("sum", re.compile(r"\b(?:total|sum|altogether)\b", re.I)),
        ("explain_cause", re.compile(r"\b(?:why|what\s+caused|reason\s+for|because\s+of)\b", re.I)),
        ("lookup_time", re.compile(r"\b(?:when|what\s+(?:date|day|year|time))\b", re.I)),
        ("lookup_person", re.compile(r"\b(?:who|whom|whose|which\s+person)\b", re.I)),
        ("lookup_place", re.compile(r"\b(?:where|which\s+(?:place|location))\b", re.I)),
        ("exists", re.compile(r"^\s*(?:did|does|do|is|are|was|were|has|have|had|can|could|will|would)\b", re.I)),
        ("lookup", re.compile(r"\b(?:what|which)\b", re.I)),
    )
    _LATTICE_LOOKUP = re.compile(
        r"\b(?:remind\s+me|how\s+often|recommend(?:ation|ations)?|suggest(?:ion|ions)?|"
        r"any\s+(?:tips|advice|ideas)|helpful\s+tips)\b", re.I)
    _LATTICE_AMOUNT = re.compile(r"\bhow\s+much\b", re.I)

    _TARGET = {
        "duration": "duration",
        "interval": "interval",
        "count_distinct": "count",
        "sum": "quantity",
        "explain_cause": "cause",
        "lookup_time": "time",
        "lookup_person": "person",
        "lookup_place": "place",
        "exists": "boolean",
        "lookup": "value",
    }

    @staticmethod
    def _address(question: str) -> HSSDAddressAtoms:
        observed = observe_raw_text(question, question=True)
        lexical = tuple(sorted({token for token in observed.lexical if token not in _SCAFFOLD}))
        return HSSDAddressAtoms(
            lexical=lexical,
            entities=observed.entities,
            numbers=observed.numbers,
            temporal=observed.temporal,
            relations=observed.relations,
        )

    def compile(self, question: str) -> HSSDQueryPlan:
        if not isinstance(question, str) or not question.strip() or len(question) > 4096:
            raise ValueError("question must be a non-empty bounded string")
        address = self._address(question)
        matched = [name for name, pattern in self._PATTERNS if pattern.search(question)]
        # More specific patterns dominate their generic lexical containment.
        if any(name not in ("lookup", "exists") for name in matched):
            matched = [name for name in matched if name != "lookup"]
        if "duration" in matched:
            matched = [name for name in matched if name != "sum"]
        if "interval" in matched:
            matched = [name for name in matched if name != "duration"]
        if len(matched) != 1:
            return HSSDQueryPlan(
                "abstain", "unsupported", "none", address, (), False,
                "operator structure is absent or conflicting",
            )
        return self._build_plan(matched[0], address, question)

    @classmethod
    def _build_plan(cls, operation: str, address: HSSDAddressAtoms,
                    question: str) -> HSSDQueryPlan:
        target = cls._TARGET[operation]
        obligations = [
            HSSDObligation("proof:identity", "proof"),
            HSSDObligation("support:selector", "selector"),
        ]
        if address.entities:
            obligations.append(HSSDObligation("support:entity", "entity"))
        require_complete = operation in ("count_distinct", "sum")
        if operation == "lookup_time":
            obligations.append(HSSDObligation("slot:clock", "clock"))
        elif operation == "lookup_person":
            obligations.append(HSSDObligation("slot:person", "role"))
        elif operation == "lookup_place":
            obligations.append(HSSDObligation("slot:location", "role"))
        elif operation == "count_distinct":
            obligations.extend((HSSDObligation("slot:distinct_key", "distinct"),
                                HSSDObligation("proof:complete", "complete")))
        elif operation == "sum":
            units = tuple(sorted({value.casefold() for value in _UNIT.findall(question)}))
            obligations.extend((HSSDObligation("slot:quantity", "quantity"),
                                HSSDObligation("slot:unit", "unit"),
                                HSSDObligation("proof:complete", "complete")))
            address = HSSDAddressAtoms(address.lexical, address.entities, address.numbers,
                                       address.temporal, address.relations + units)
        elif operation in ("duration", "interval"):
            obligations.append(HSSDObligation("slot:clock_pair", "clock", 2))
        elif operation == "explain_cause":
            obligations.append(HSSDObligation("slot:causal_edge", "cause"))
        elif operation == "lookup":
            obligations.append(HSSDObligation("slot:value", "value"))
        elif operation == "exists":
            obligations.append(HSSDObligation("slot:boolean_support", "boolean"))
        return HSSDQueryPlan(
            "compiled", operation, target, address, tuple(sorted(obligations)),
            require_complete, "unique structural operator syndrome",
        )

    def compile_lattice(self, question: str) -> HSSDQueryLattice:
        """Preserve a bounded set of structurally possible programs.

        The method deliberately does not rank alternatives.  A downstream executor must
        either eliminate plans with typed proof obligations or show that every complete plan
        yields the same scalar answer.  The legacy ``compile`` path remains unchanged.
        """
        if not isinstance(question, str) or not question.strip() or len(question) > 4096:
            raise ValueError("question must be a non-empty bounded string")
        address = self._address(question)
        matched = [name for name, pattern in self._PATTERNS if pattern.search(question)]
        if any(name not in ("lookup", "exists") for name in matched):
            matched = [name for name in matched if name != "lookup"]
        if "duration" in matched:
            matched = [name for name in matched if name != "sum"]
        if "interval" in matched:
            matched = [name for name in matched if name != "duration"]

        # These are proposal gauges, not decisions.  "How much" may request a stored scalar
        # or an exhaustive sum; advice/reminder surfaces request retrieval of prior content.
        if self._LATTICE_LOOKUP.search(question):
            # Modal "Can you remind/recommend..." is a request to retrieve prior content,
            # not a yes/no EXISTS query.
            matched = [name for name in matched if name != "exists"]
            matched.append("lookup")
        if self._LATTICE_AMOUNT.search(question) and "duration" not in matched:
            matched.extend(("lookup", "sum"))

        operations = tuple(sorted(set(matched)))
        if not operations:
            return HSSDQueryLattice(
                "unsupported", (), address, "no bounded operator interpretation")
        plans = tuple(self._build_plan(operation, address, question)
                      for operation in operations)
        state = "compiled" if len(plans) == 1 else "ambiguous"
        return HSSDQueryLattice(
            state, plans, address,
            "unique structural operator syndrome" if len(plans) == 1 else
            "multiple interpretations preserved until proof convergence",
        )

    @staticmethod
    def assess(plan: HSSDQueryPlan,
               evidence: tuple[HSSDEvidenceObservation, ...]) -> HSSDClosure:
        if plan.state != "compiled":
            return HSSDClosure("unsupported", (), (), (), False, False, plan.reason)
        if not evidence:
            residual = tuple(item.key for item in plan.obligations)
            return HSSDClosure("incomplete", (), residual, (), False, False, "no evidence")
        if len({item.fact_id for item in evidence}) != len(evidence):
            raise ValueError("HSSD evidence must be FactId-deduplicated")
        if any(item.conflict for item in evidence):
            return HSSDClosure("conflict", (), tuple(item.key for item in plan.obligations),
                               tuple(sorted(item.fact_id for item in evidence)), False, False,
                               "hard evidence conflict")

        lexical = {token for item in evidence for token in item.lexical}
        entities = {value.casefold() for item in evidence for value in item.entities}
        roles = {value for item in evidence for value in item.roles}
        clocks = {value for item in evidence for value in item.clocks}
        quantities = {value for item in evidence for value in item.quantities}
        units = {value.casefold() for item in evidence for value in item.units}
        distinct = {value for item in evidence for value in item.distinct_keys}
        query_entities = {value.casefold() for value in plan.address_atoms.entities}
        query_units = {value.casefold() for value in plan.address_atoms.relations if _UNIT.fullmatch(value)}
        closed = set()
        if any(item.proof_verified for item in evidence):
            closed.add("proof:identity")
        if set(plan.address_atoms.lexical).intersection(lexical):
            closed.add("support:selector")
        if query_entities and query_entities.intersection(entities):
            closed.add("support:entity")
        if plan.operation == "lookup_time" and clocks:
            closed.add("slot:clock")
        elif plan.operation == "lookup_person" and "person" in roles:
            closed.add("slot:person")
        elif plan.operation == "lookup_place" and "location" in roles:
            closed.add("slot:location")
        elif plan.operation == "count_distinct" and distinct:
            closed.add("slot:distinct_key")
        elif plan.operation == "sum":
            if quantities:
                closed.add("slot:quantity")
            if units and (not query_units or query_units.intersection(units)):
                closed.add("slot:unit")
        elif plan.operation in ("duration", "interval") and len(clocks) >= 2:
            closed.add("slot:clock_pair")
        elif plan.operation == "explain_cause" and sum(item.causal_edges for item in evidence) > 0:
            closed.add("slot:causal_edge")
        elif plan.operation == "lookup" and any(roles):
            closed.add("slot:value")
        elif plan.operation == "exists" and lexical:
            closed.add("slot:boolean_support")
        if any(item.complete for item in evidence):
            closed.add("proof:complete")

        required = {item.key for item in plan.obligations}
        residual = tuple(sorted(required.difference(closed)))
        retrieval_required = {"proof:identity", "support:selector"}
        if "support:entity" in required:
            retrieval_required.add("support:entity")
        retrieval_closed = retrieval_required <= closed
        execution_ready = not residual
        return HSSDClosure(
            "ready" if execution_ready else "incomplete",
            tuple(sorted(closed)), residual,
            tuple(sorted(item.fact_id for item in evidence)),
            retrieval_closed, execution_ready,
            "all noncompensable obligations closed" if execution_ready
            else "typed obligations remain open",
        )
