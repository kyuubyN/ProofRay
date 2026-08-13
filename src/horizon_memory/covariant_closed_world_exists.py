# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""V62 successor: EXISTS addressing covariant to a declared morphology orbit."""
from __future__ import annotations
from dataclasses import replace
from .closed_world_exists import ClosedWorldExistsEngine
from .morphological_gauge import gauge_lemma, observe_gauge_lexical
from .strict_hssd_query_compiler import StrictStructuralHSSDQueryCompiler


class CovariantStrictHSSDQueryCompiler(StrictStructuralHSSDQueryCompiler):
    def compile(self, question: str):
        plan = super().compile(question)
        address = replace(plan.address_atoms, lexical=tuple(sorted({
            gauge_lemma(token) for token in plan.address_atoms.lexical})))
        return replace(plan, address_atoms=address)

class CovariantClosedWorldExistsEngine(ClosedWorldExistsEngine):
    def __init__(self, memory, facts, certificates):
        super().__init__(memory, facts, certificates)
        self.compiler = CovariantStrictHSSDQueryCompiler()

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return set(observe_gauge_lexical(text))
