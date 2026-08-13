# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Boundary selection that conserves independently certified causal fluxes."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FluxSelection:
    fact_ids: tuple[int, ...]
    admissions: tuple[tuple[str, int], ...]
    excluded: tuple[int, ...]


class ConservedFluxSelector:
    """Interleave causal modes without letting one silently erase another.

    The schedule is a conservation law, not score fusion. A candidate can disappear from
    a mode only through a declared hard exclusion; exhaustion merely transfers unused flux
    to the other modes. Duplicate FactIds retain one identity at their first arrival.
    """

    def __init__(self, schedule: tuple[str, ...]):
        if not schedule or any(not name for name in schedule):
            raise ValueError("a non-empty causal flux schedule is required")
        self.schedule = schedule

    def select(self, modes: tuple[tuple[str, tuple[int, ...]], ...], limit: int, *,
               hard_exclusions: tuple[int, ...] = ()) -> FluxSelection:
        if limit < 1:
            raise ValueError("flux limit must be positive")
        if modes != tuple(sorted(modes)) or len({name for name, _ in modes}) != len(modes):
            raise ValueError("modes must be unique and canonically sorted")
        by_name = {name: candidates for name, candidates in modes}
        if any(name not in by_name for name in self.schedule):
            raise ValueError("schedule references an absent mode")
        if any(len(values) != len(set(values)) for values in by_name.values()):
            raise ValueError("each mode must be FactId-deduplicated")
        excluded = set(hard_exclusions)
        positions = {name: 0 for name in by_name}
        admitted = {name: 0 for name in by_name}
        selected, selected_set = [], set()
        while len(selected) < limit:
            progress = False
            for name in self.schedule:
                candidates = by_name[name]
                while positions[name] < len(candidates):
                    fact_id = candidates[positions[name]]
                    positions[name] += 1
                    if fact_id in excluded or fact_id in selected_set:
                        continue
                    selected.append(fact_id)
                    selected_set.add(fact_id)
                    admitted[name] += 1
                    progress = True
                    break
                if len(selected) >= limit:
                    break
            if not progress:
                break
        return FluxSelection(tuple(selected), tuple(sorted(admitted.items())),
                             tuple(sorted(excluded)))


class CoreHaloFluxSelector:
    """Protect a ballistic causal core, then mix the remaining resonant halo."""

    def __init__(self, core_mode: str, core_width: int, halo_schedule: tuple[str, ...]):
        if not core_mode or core_width < 1:
            raise ValueError("core mode and positive width are required")
        self.core_mode = core_mode
        self.core_width = core_width
        self.halo = ConservedFluxSelector(halo_schedule)

    def select(self, modes: tuple[tuple[str, tuple[int, ...]], ...], limit: int, *,
               hard_exclusions: tuple[int, ...] = ()) -> FluxSelection:
        if modes != tuple(sorted(modes)):
            raise ValueError("modes must be canonically sorted")
        by_name = dict(modes)
        if self.core_mode not in by_name or limit < 1:
            raise ValueError("core mode must exist and limit must be positive")
        excluded = set(hard_exclusions)
        core = tuple(fact_id for fact_id in by_name[self.core_mode]
                     if fact_id not in excluded)[:min(self.core_width, limit)]
        if len(core) == limit:
            return FluxSelection(core, ((self.core_mode, len(core)),), tuple(sorted(excluded)))
        core_set = set(core)
        halo_modes = tuple((name, tuple(fact_id for fact_id in facts
                                       if fact_id not in core_set)) for name, facts in modes)
        halo = self.halo.select(halo_modes, limit - len(core), hard_exclusions=hard_exclusions)
        admissions = dict(halo.admissions)
        admissions[self.core_mode] = admissions.get(self.core_mode, 0) + len(core)
        return FluxSelection(core + halo.fact_ids, tuple(sorted(admissions.items())),
                             tuple(sorted(excluded)))
