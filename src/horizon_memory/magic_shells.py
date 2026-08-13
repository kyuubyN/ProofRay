# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Observer-relative shell closure and deterministic audit of apparent luck."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class ShellCandidate:
    fact_id: int
    amplitude: float
    cost: int
    hard_excluded: bool = False

    def __post_init__(self) -> None:
        if self.fact_id < 0 or self.cost < 1:
            raise ValueError("shell candidate requires FactId and positive cost")


@dataclass(frozen=True)
class DegenerateShell:
    level: int
    amplitude: float
    fact_ids: tuple[int, ...]
    cost: int


@dataclass(frozen=True)
class ShellClosure:
    admitted_fact_ids: tuple[int, ...]
    closed_shells: tuple[DegenerateShell, ...]
    residual_shell: DegenerateShell | None
    used_cost: int
    magic_numbers: tuple[int, ...]


class ObserverShellClosure:
    """Never split a gauge-degenerate evidence shell to manufacture a lucky top-k."""

    def __init__(self, tolerance: float = 1e-12):
        if tolerance < 0:
            raise ValueError("shell tolerance must be non-negative")
        self.tolerance = tolerance

    def shells(self, candidates: tuple[ShellCandidate, ...]) -> tuple[DegenerateShell, ...]:
        if len({item.fact_id for item in candidates}) != len(candidates):
            raise ValueError("shell candidates must have unique FactIds")
        viable = sorted((item for item in candidates if not item.hard_excluded),
                        key=lambda item: (-item.amplitude, item.fact_id))
        groups = []
        for candidate in viable:
            if not groups or abs(groups[-1][0].amplitude - candidate.amplitude) > self.tolerance:
                groups.append([candidate])
            else:
                groups[-1].append(candidate)
        return tuple(DegenerateShell(
            level, group[0].amplitude, tuple(item.fact_id for item in group),
            sum(item.cost for item in group)) for level, group in enumerate(groups, 1))

    def close(self, candidates: tuple[ShellCandidate, ...], budget: int) -> ShellClosure:
        if budget < 1:
            raise ValueError("shell budget must be positive")
        admitted, closed, used, magic = [], [], 0, []
        residual = None
        for shell in self.shells(candidates):
            if used + shell.cost > budget:
                residual = shell
                break
            admitted.extend(shell.fact_ids)
            used += shell.cost
            closed.append(shell)
            magic.append(len(admitted))
        return ShellClosure(tuple(admitted), tuple(closed), residual, used, tuple(magic))

    @staticmethod
    def quantize(candidates: tuple[ShellCandidate, ...], levels: int) \
            -> tuple[ShellCandidate, ...]:
        """Controlled decoherence; levels are explicit and never learned from labels."""
        if levels < 2:
            raise ValueError("at least two amplitude levels are required")
        if not candidates:
            return ()
        low = min(item.amplitude for item in candidates)
        high = max(item.amplitude for item in candidates)
        if high == low:
            return candidates
        step = (high - low) / (levels - 1)
        return tuple(ShellCandidate(
            item.fact_id, low + round((item.amplitude - low) / step) * step,
            item.cost, item.hard_excluded) for item in candidates)

    @staticmethod
    def lucky_trajectory(shell: DegenerateShell, seed: int) -> tuple[int, ...]:
        """Expose, never legitimize, one arbitrary gauge choice inside a shell."""
        return tuple(sorted(shell.fact_ids, key=lambda fact_id: hashlib.sha256(
            f"horizon-luck-audit-v1:{seed}:{fact_id}".encode()).digest()))

    @classmethod
    def gauge_consensus(cls, shell: DegenerateShell, seeds: tuple[int, ...], top_n: int = 1) \
            -> tuple[int, ...]:
        if not seeds or top_n < 1:
            raise ValueError("consensus requires seeds and positive top_n")
        selections = [set(cls.lucky_trajectory(shell, seed)[:top_n]) for seed in seeds]
        return tuple(sorted(set.intersection(*selections)))
