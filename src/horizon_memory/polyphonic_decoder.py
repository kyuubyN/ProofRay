# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compose inverse exclusions, direct relational music and latent closure."""
from __future__ import annotations

from dataclasses import dataclass

from .inverse_boundary import ExclusionCertificate, InverseBoundaryField
from .latent_relational_dynamics import LatentRelationalField, LatentResolution
from .relational_music import MusicResolution, RelationalMusicField


@dataclass(frozen=True)
class PolyphonicResolution:
    state: str
    canonical: str | None
    channel: str
    evidence_fact_ids: tuple[int, ...]
    reason: str
    exclusion: ExclusionCertificate
    direct: MusicResolution | None
    latent: LatentResolution | None


class PolyphonicGaugeDecoder:
    """Negative constraints dominate; latent structure is fallback, never a voter."""

    def __init__(self, inverse: InverseBoundaryField, direct: RelationalMusicField,
                 latent: LatentRelationalField):
        self._inverse = inverse
        self._direct = direct
        self._latent = latent

    def listen(self, scope: str, surface: str, companions: tuple[str, ...], clock: int,
               *, pragmatic: str = "literal") -> PolyphonicResolution:
        exclusion = self._inverse.emit(scope, clock, companions)
        if exclusion.excluded_edges:
            return PolyphonicResolution(
                "abstain", None, "inverse_boundary", exclusion.evidence_fact_ids,
                "active exclusion emitted before positive readout", exclusion, None, None)
        direct = self._direct.listen(scope, surface, companions, clock, pragmatic=pragmatic)
        if direct.state == "resolved":
            return PolyphonicResolution(
                "resolved", direct.canonical, "direct_music", direct.evidence_fact_ids,
                direct.reason, exclusion, direct, None)
        if pragmatic != "literal":
            return PolyphonicResolution(
                "abstain", None, "pragmatic_boundary", direct.evidence_fact_ids,
                direct.reason, exclusion, direct, None)
        latent = self._latent.listen(scope, surface, companions, clock)
        if latent.state == "resolved":
            return PolyphonicResolution(
                "resolved", latent.canonical, "latent_mediator", latent.evidence_fact_ids,
                latent.reason, exclusion, direct, latent)
        return PolyphonicResolution(
            "abstain", None, "unresolved", (), latent.reason, exclusion, direct, latent)
