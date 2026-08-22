# Copyright (c) 2026 kyuubyN
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Strict, opt-in structured sidecar authority for causal memory.

The sidecar does not claim to infer semantics from arbitrary text.  A host explicitly
authorizes a versioned adapter/schema to declare them.  Horizon then binds every declared
field to that authority and to an exact source span, enforces its capabilities, and commits
the whole batch atomically.  No model, embedding service or network call is involved.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import re
from typing import Protocol, runtime_checkable

from .causal_adapter_protocol import CausalAdapterBatch, CausalIngestAdapter
from .standalone_causal_memory import StandaloneCausalMemory
from .typed_causal_ingest import (
    CausalSourceEnvelope, DeterministicCausalCompiler, StructuredCausalDeclaration,
)
from .typed_causal_program import TypedCausalFact, TypedCausalProgram, TypedCausalResult


_SHA256 = re.compile(r"[0-9a-f]{64}")


def _canonical_json(value: object) -> bytes:
    """Serialize a restricted data object without platform-dependent whitespace."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(domain: bytes, value: object) -> str:
    return hashlib.sha256(domain + b"\x00" + _canonical_json(value)).hexdigest()


@dataclass(frozen=True)
class SidecarAuthority:
    """A host-approved, least-privilege semantic declaration capability."""

    adapter_id: str
    rule_id: str
    rule_version: int
    schema_sha256: str
    allowed_scopes: tuple[str, ...]
    allowed_predicates: tuple[str, ...]
    purpose: str
    closed_world_predicates: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.adapter_id or not self.rule_id or self.rule_version < 1 or not self.purpose:
            raise ValueError("sidecar authority needs adapter, rule, version and purpose")
        if not _SHA256.fullmatch(self.schema_sha256):
            raise ValueError("sidecar schema requires a canonical SHA-256")
        for values, label in ((self.allowed_scopes, "scopes"),
                              (self.allowed_predicates, "predicates")):
            if not values or values != tuple(sorted(set(values))) or any(not item for item in values):
                raise ValueError(f"sidecar authority {label} must be non-empty and canonical")
        if (self.closed_world_predicates != tuple(sorted(set(self.closed_world_predicates))) or
                any(item not in self.allowed_predicates
                    for item in self.closed_world_predicates)):
            raise ValueError("closed-world predicates must be a canonical subset of capabilities")

    def canonical_payload(self) -> dict[str, object]:
        return {
            "adapter_id": self.adapter_id,
            "allowed_predicates": list(self.allowed_predicates),
            "allowed_scopes": list(self.allowed_scopes),
            "closed_world_predicates": list(self.closed_world_predicates),
            "purpose": self.purpose,
            "rule_id": self.rule_id,
            "rule_version": self.rule_version,
            "schema_sha256": self.schema_sha256,
        }

    @property
    def authority_sha256(self) -> str:
        return _digest(b"HORIZON-SIDECAR-AUTHORITY-v1", self.canonical_payload())

    def permits(self, fact: TypedCausalFact) -> bool:
        return fact.scope in self.allowed_scopes and fact.predicate in self.allowed_predicates


def _fact_payload(fact: TypedCausalFact) -> dict[str, object]:
    payload = asdict(fact)
    payload["causes"] = list(fact.causes)
    payload["source_span"] = list(fact.source_span)
    return payload


@dataclass(frozen=True)
class SidecarLifecycle:
    """Purpose, authorization, validity interval and explicit update lineage."""

    valid_from: int
    valid_until: int | None
    purpose: str
    authorization_reference: str
    supersedes: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if (self.valid_from < 0 or
                (self.valid_until is not None and self.valid_until <= self.valid_from) or
                not self.purpose or not self.authorization_reference or
                self.supersedes != tuple(sorted(set(self.supersedes))) or
                any(item < 0 for item in self.supersedes)):
            raise ValueError("invalid sidecar lifecycle policy")

    def is_valid_at(self, instant: int) -> bool:
        return self.valid_from <= instant and (
            self.valid_until is None or instant < self.valid_until)


@dataclass(frozen=True)
class AttestedSidecarFact:
    """A fact whose entire semantic declaration is bound to one authority manifest."""

    fact: TypedCausalFact
    lifecycle: SidecarLifecycle
    authority_sha256: str
    attestation_sha256: str

    @classmethod
    def seal(cls, fact: TypedCausalFact,
             authority: SidecarAuthority,
             lifecycle: SidecarLifecycle | None = None) -> "AttestedSidecarFact":
        if lifecycle is None:
            lifecycle = SidecarLifecycle(
                fact.observed_at, None, authority.purpose,
                f"authority-manifest:{authority.authority_sha256}")
        if lifecycle.purpose != authority.purpose or fact.fact_id in lifecycle.supersedes:
            raise ValueError("fact lifecycle is outside its authority or self-superseding")
        authority_sha256 = authority.authority_sha256
        attestation = _digest(b"HORIZON-SIDECAR-FACT-v1", {
            "authority_sha256": authority_sha256,
            "fact": _fact_payload(fact),
            "lifecycle": asdict(lifecycle),
        })
        return cls(fact, lifecycle, authority_sha256, attestation)

    def verify(self, authority: SidecarAuthority,
               source: CausalSourceEnvelope) -> bool:
        if self.authority_sha256 != authority.authority_sha256:
            return False
        try:
            expected = AttestedSidecarFact.seal(self.fact, authority, self.lifecycle)
        except ValueError:
            return False
        return (self.attestation_sha256 == expected.attestation_sha256 and
                authority.permits(self.fact) and
                DeterministicCausalCompiler.verify(self.fact, source))


@dataclass(frozen=True)
class AttestedCompletenessClaim:
    """An authority says that FactIds enumerate one current selector population."""

    scope: str
    subject: str
    predicate: str
    fact_ids: tuple[int, ...]
    source_id: str
    source_sha256: str
    authority_sha256: str
    attestation_sha256: str

    @classmethod
    def seal(cls, authority: SidecarAuthority, source: CausalSourceEnvelope,
             subject: str, predicate: str,
             fact_ids: tuple[int, ...]) -> "AttestedCompletenessClaim":
        if len(authority.allowed_scopes) != 1:
            raise ValueError("explicit scope is required for multi-scope completeness")
        return cls.seal_for_scope(authority, source, authority.allowed_scopes[0],
                                  subject, predicate, fact_ids)

    @classmethod
    def seal_for_scope(cls, authority: SidecarAuthority, source: CausalSourceEnvelope,
                       scope: str, subject: str, predicate: str,
                       fact_ids: tuple[int, ...]) -> "AttestedCompletenessClaim":
        if (not scope or scope not in authority.allowed_scopes or not subject or
                predicate not in authority.closed_world_predicates or
                fact_ids != tuple(sorted(set(fact_ids))) or any(item < 0 for item in fact_ids)):
            raise ValueError("invalid or unauthorized sidecar completeness claim")
        payload = {
            "authority_sha256": authority.authority_sha256,
            "fact_ids": list(fact_ids), "predicate": predicate, "scope": scope,
            "source_id": source.source_id, "source_sha256": source.sha256,
            "subject": subject,
        }
        digest = _digest(b"HORIZON-SIDECAR-COMPLETENESS-v1", payload)
        return cls(scope, subject, predicate, fact_ids, source.source_id, source.sha256,
                   authority.authority_sha256, digest)

    def verify(self, authority: SidecarAuthority, source: CausalSourceEnvelope) -> bool:
        if (self.scope not in authority.allowed_scopes or
                self.predicate not in authority.closed_world_predicates or
                self.source_id != source.source_id or self.source_sha256 != source.sha256 or
                self.authority_sha256 != authority.authority_sha256 or not source.verify()):
            return False
        try:
            expected = AttestedCompletenessClaim.seal_for_scope(
                authority, source, self.scope, self.subject, self.predicate, self.fact_ids)
        except ValueError:
            return False
        return self.attestation_sha256 == expected.attestation_sha256


@dataclass(frozen=True)
class SidecarCompilation:
    facts: tuple[AttestedSidecarFact, ...]
    completeness_claims: tuple[AttestedCompletenessClaim, ...] = ()


@dataclass(frozen=True)
class CompletenessCertificate:
    scope: str
    subject: str
    predicate: str
    fact_ids: tuple[int, ...]
    fact_attestations_sha256: str
    claim_attestation_sha256: str
    evaluated_at: int | None
    certificate_sha256: str


@dataclass(frozen=True)
class SidecarFactDeclaration:
    declaration: StructuredCausalDeclaration
    lifecycle: SidecarLifecycle | None = None


@dataclass(frozen=True)
class SidecarCompletenessDeclaration:
    subject: str
    predicate: str
    fact_ids: tuple[int, ...]


class DeclarativeSidecarAdapter:
    """Ready-to-use adapter for hosts that already possess typed declarations."""

    def __init__(self, authority: SidecarAuthority):
        self.authority = authority

    def compile_sidecar(self, batch: CausalAdapterBatch) -> SidecarCompilation:
        source = CausalSourceEnvelope.seal(batch.source_id, batch.content)
        facts = []
        closure_specs = []
        for item in batch.declarations:
            if isinstance(item, StructuredCausalDeclaration):
                item = SidecarFactDeclaration(item)
            if isinstance(item, SidecarFactDeclaration):
                fact = DeterministicCausalCompiler.compile(source, item.declaration)
                facts.append(AttestedSidecarFact.seal(fact, self.authority, item.lifecycle))
            elif isinstance(item, SidecarCompletenessDeclaration):
                closure_specs.append(item)
            else:
                raise TypeError("declarative sidecar accepts only fact or completeness declarations")
        claims = tuple(AttestedCompletenessClaim.seal_for_scope(
            self.authority, source, batch.scope, item.subject, item.predicate, item.fact_ids)
                       for item in closure_specs)
        return SidecarCompilation(tuple(sorted(facts, key=lambda item: item.fact.fact_id)), claims)


class AuthorizedAdapterBridge:
    """Attest an existing deterministic causal adapter without reimplementing it.

    The authority adapter ID must exactly match the delegate. A lifecycle factory is the
    only optional policy hook; it cannot change fact semantics or source spans.
    """

    def __init__(self, authority: SidecarAuthority, delegate: CausalIngestAdapter,
                 lifecycle_factory=None):
        delegate_id = getattr(delegate, "adapter_id", "")
        if (not delegate_id or not isinstance(delegate, CausalIngestAdapter) or
                delegate_id != authority.adapter_id):
            raise ValueError("sidecar authority must name the exact deterministic delegate")
        self.authority = authority
        self.delegate = delegate
        self.lifecycle_factory = lifecycle_factory

    def compile_sidecar(self, batch: CausalAdapterBatch) -> SidecarCompilation:
        facts = self.delegate.compile_batch(batch)
        attested = []
        for fact in facts:
            lifecycle = None if self.lifecycle_factory is None else self.lifecycle_factory(fact)
            if lifecycle is not None and not isinstance(lifecycle, SidecarLifecycle):
                raise TypeError("sidecar lifecycle factory must return SidecarLifecycle or None")
            attested.append(AttestedSidecarFact.seal(fact, self.authority, lifecycle))
        return SidecarCompilation(tuple(attested))


@runtime_checkable
class SidecarIngestAdapter(Protocol):
    """A removable producer whose authority must already be trusted by the host."""

    authority: SidecarAuthority

    def compile_sidecar(self, batch: CausalAdapterBatch) \
            -> tuple[AttestedSidecarFact, ...] | SidecarCompilation: ...


@dataclass(frozen=True)
class SidecarIngestReceipt:
    state: str
    adapter_id: str
    authority_sha256: str
    fact_ids: tuple[int, ...]
    source_sha256: str
    reason: str


@dataclass(frozen=True)
class SidecarLimits:
    max_authorities: int = 1024
    max_source_bytes: int = 16 * 1024 * 1024
    max_facts_per_batch: int = 100_000
    max_completeness_claims_per_batch: int = 10_000

    def __post_init__(self) -> None:
        if not (1 <= self.max_authorities <= 1_000_000 and
                1 <= self.max_source_bytes <= 1_073_741_824 and
                1 <= self.max_facts_per_batch <= 10_000_000 and
                1 <= self.max_completeness_claims_per_batch <= 1_000_000):
            raise ValueError("sidecar resource limits are outside supported bounds")


class _FrozenFactAdapter:
    """Private bridge; strict validation has completed before this object exists."""

    def __init__(self, adapter_id: str, facts: tuple[TypedCausalFact, ...]):
        self.adapter_id = adapter_id
        self._facts = facts

    def compile_batch(self, batch: CausalAdapterBatch) -> tuple[TypedCausalFact, ...]:
        return self._facts


class AuthorizedSidecarMemory:
    """Standalone causal memory whose only ingest route is an authorized sidecar."""

    def __init__(self, scope: str, authorities: tuple[SidecarAuthority, ...], *,
                 limits: SidecarLimits | None = None):
        self.limits = limits or SidecarLimits()
        if not authorities:
            raise ValueError("authorized sidecar memory needs at least one authority")
        if len(authorities) > self.limits.max_authorities:
            raise ValueError("sidecar authority registry exceeds resource limit")
        if any(scope not in authority.allowed_scopes for authority in authorities):
            raise ValueError("every authority must explicitly permit the memory scope")
        by_id = {authority.adapter_id: authority for authority in authorities}
        if len(by_id) != len(authorities):
            raise ValueError("adapter ids must be unique in an authority registry")
        if len({authority.authority_sha256 for authority in authorities}) != len(authorities):
            raise ValueError("sidecar authorities must be unique")
        purposes = {authority.purpose for authority in authorities}
        if len(purposes) != 1:
            raise ValueError("one sidecar memory boundary may serve exactly one purpose")
        self.scope = scope
        self.purpose = next(iter(purposes))
        self._authorities = by_id
        self._memory = StandaloneCausalMemory(scope)
        self._attestations: dict[int, AttestedSidecarFact] = {}
        self._sources: dict[str, CausalSourceEnvelope] = {}
        self._completeness: dict[tuple[str, str], AttestedCompletenessClaim] = {}
        self._superseded_by: dict[int, int] = {}

    @staticmethod
    def _selected_population(facts: tuple[TypedCausalFact, ...], subject: str,
                             predicate: str) -> tuple[int, ...] | None:
        by_orbit: dict[str, list[TypedCausalFact]] = {}
        for fact in facts:
            if fact.subject == subject and fact.predicate == predicate:
                by_orbit.setdefault(fact.orbit, []).append(fact)
        selected = []
        for orbit in sorted(by_orbit):
            rows = by_orbit[orbit]
            clock = max((fact.version, fact.observed_at) for fact in rows)
            latest = [fact for fact in rows if (fact.version, fact.observed_at) == clock]
            signatures = {(fact.value, fact.unit, fact.polarity, fact.asserted,
                           fact.event_time, fact.causes) for fact in latest}
            if len(signatures) != 1:
                return None
            selected.append(min(latest, key=lambda fact: fact.fact_id).fact_id)
        return tuple(sorted(selected))

    def ingest(self, adapter: SidecarIngestAdapter,
               batch: CausalAdapterBatch) -> SidecarIngestReceipt:
        if (not isinstance(batch, CausalAdapterBatch) or
                not isinstance(batch.source_id, str) or
                not isinstance(batch.content, str) or
                not isinstance(batch.scope, str) or
                not isinstance(batch.declarations, tuple)):
            return SidecarIngestReceipt("REJECTED_ADAPTER", "", "", (), "",
                                        "batch must be a canonical CausalAdapterBatch")
        if len(batch.content.encode("utf-8")) > self.limits.max_source_bytes:
            return SidecarIngestReceipt("REJECTED_RESOURCE_LIMIT", "", "", (),
                                        batch.source_sha256,
                                        "sidecar source exceeds configured byte limit")
        supplied = getattr(adapter, "authority", None)
        adapter_id = getattr(supplied, "adapter_id", "")
        authority_sha256 = getattr(supplied, "authority_sha256", "")
        source_sha256 = batch.source_sha256
        if not isinstance(adapter, SidecarIngestAdapter) or not isinstance(supplied, SidecarAuthority):
            return SidecarIngestReceipt("REJECTED_ADAPTER", adapter_id, authority_sha256, (),
                                        source_sha256, "adapter must implement sidecar protocol")
        trusted = self._authorities.get(adapter_id)
        if trusted is None or supplied != trusted:
            return SidecarIngestReceipt("REJECTED_AUTHORITY", adapter_id, authority_sha256, (),
                                        source_sha256, "authority is absent or differs from registry")
        if batch.scope != self.scope or batch.scope not in trusted.allowed_scopes:
            return SidecarIngestReceipt("REJECTED_SCOPE", adapter_id, authority_sha256, (),
                                        source_sha256, "batch scope is outside authority capability")
        try:
            source = CausalSourceEnvelope.seal(batch.source_id, batch.content)
            compiled = adapter.compile_sidecar(batch)
        except Exception as error:
            return SidecarIngestReceipt("REJECTED_ADAPTER", adapter_id, authority_sha256, (),
                                        source_sha256, str(error))
        if isinstance(compiled, SidecarCompilation):
            proposed, claims = compiled.facts, compiled.completeness_claims
        elif isinstance(compiled, tuple):
            proposed, claims = compiled, ()
        else:
            return SidecarIngestReceipt("REJECTED_ADAPTER", adapter_id, authority_sha256, (),
                                        source.sha256, "sidecar compilation must be immutable")
        if (len(proposed) > self.limits.max_facts_per_batch or
                len(claims) > self.limits.max_completeness_claims_per_batch):
            return SidecarIngestReceipt("REJECTED_RESOURCE_LIMIT", adapter_id,
                                        authority_sha256, (), source.sha256,
                                        "sidecar compilation exceeds configured limits")
        if (not proposed and not claims) or any(
                not isinstance(item, AttestedSidecarFact) for item in proposed):
            return SidecarIngestReceipt("REJECTED_ADAPTER", adapter_id, authority_sha256, (),
                                        source.sha256,
                                        "sidecar output must contain facts or completeness claims")
        facts = tuple(item.fact for item in proposed)
        if (
                tuple(fact.fact_id for fact in facts) !=
                tuple(sorted({fact.fact_id for fact in facts}))):
            return SidecarIngestReceipt("REJECTED_ADAPTER", adapter_id, authority_sha256, (),
                                        source.sha256,
                                        "sidecar output must be non-empty and FactId-canonical")
        if any(fact.scope != self.scope or fact.source_id != source.source_id or
               fact.source_sha256 != source.sha256 for fact in facts):
            return SidecarIngestReceipt("REJECTED_AUTHORITY", adapter_id, authority_sha256, (),
                                        source.sha256, "sidecar cannot forge scope or source")
        if any(not trusted.permits(fact) for fact in facts):
            return SidecarIngestReceipt("REJECTED_CAPABILITY", adapter_id, authority_sha256, (),
                                        source.sha256, "predicate or scope is not allowlisted")
        if any(not item.verify(trusted, source) for item in proposed):
            return SidecarIngestReceipt("REJECTED_ATTESTATION", adapter_id, authority_sha256, (),
                                        source.sha256, "fact attestation or microcitation failed")
        conflicts = tuple(item.fact.fact_id for item in proposed
                          if item.fact.fact_id in self._attestations and
                          self._attestations[item.fact.fact_id] != item)
        if conflicts:
            return SidecarIngestReceipt("REJECTED_ATTESTATION_COLLISION", adapter_id,
                                        authority_sha256, conflicts, source.sha256,
                                        "FactId is already bound to another attestation")
        proposed_orbits = tuple((item.fact.subject, item.fact.predicate, item.fact.orbit)
                                for item in proposed)
        if len(set(proposed_orbits)) != len(proposed_orbits):
            return SidecarIngestReceipt("REJECTED_UPDATE", adapter_id, authority_sha256, (),
                                        source.sha256,
                                        "one atomic batch may update an event orbit only once")
        pending_superseded: dict[int, int] = {}
        existing_facts = tuple(item.fact for item in self._attestations.values())
        for item in proposed:
            fact = item.fact
            if self._attestations.get(fact.fact_id) == item:
                continue
            previous = tuple(sorted(old.fact_id for old in existing_facts
                                    if old.subject == fact.subject and
                                    old.predicate == fact.predicate and
                                    old.orbit == fact.orbit and
                                    old.fact_id not in self._superseded_by))
            if item.lifecycle.supersedes != previous:
                return SidecarIngestReceipt("REJECTED_UPDATE", adapter_id, authority_sha256, (),
                                            source.sha256,
                                            "update lineage must name the complete active orbit")
            if previous:
                old_rows = tuple(self._attestations[fact_id].fact for fact_id in previous)
                if any(fact.version <= old.version for old in old_rows):
                    return SidecarIngestReceipt("REJECTED_UPDATE", adapter_id,
                                                authority_sha256, (), source.sha256,
                                                "updates require a strictly newer version")
                pending_superseded.update({fact_id: fact.fact_id for fact_id in previous})
        # Build the sidecar publication first.  The underlying memory independently checks
        # source, FactIds, causal edges and index construction before its own atomic swap.
        prospective = {**self._attestations,
                       **{item.fact.fact_id: item for item in proposed}}
        prospective_facts = tuple(item.fact for item in prospective.values())
        if any(not isinstance(claim, AttestedCompletenessClaim) for claim in claims):
            return SidecarIngestReceipt("REJECTED_COMPLETENESS", adapter_id,
                                        authority_sha256, (), source.sha256,
                                        "completeness claims must be typed, unique and canonical")
        claim_keys = tuple((claim.subject, claim.predicate) for claim in claims)
        if claim_keys != tuple(sorted(set(claim_keys))):
            return SidecarIngestReceipt("REJECTED_COMPLETENESS", adapter_id,
                                        authority_sha256, (), source.sha256,
                                        "completeness claims must be typed, unique and canonical")
        for claim in claims:
            selected = self._selected_population(
                prospective_facts, claim.subject, claim.predicate)
            if (not claim.verify(trusted, source) or selected is None or
                    claim.scope != self.scope or claim.fact_ids != selected):
                return SidecarIngestReceipt("REJECTED_COMPLETENESS", adapter_id,
                                            authority_sha256, (), source.sha256,
                                            "completeness claim does not equal current population")
        if facts:
            core = self._memory.ingest(_FrozenFactAdapter(adapter_id, facts), batch)
            if core.state not in ("APPLIED", "IDEMPOTENT"):
                return SidecarIngestReceipt(core.state, adapter_id, authority_sha256,
                                            core.fact_ids, core.source_sha256, core.reason)
            core_state, core_fact_ids = core.state, core.fact_ids
        else:
            existing_source = self._sources.get(source.source_id)
            if existing_source is not None and existing_source.sha256 != source.sha256:
                return SidecarIngestReceipt(
                    "REJECTED_SOURCE_COLLISION", adapter_id, authority_sha256, (), source.sha256,
                    "source identity is immutable across completeness batches")
            identical = bool(claims) and all(
                self._completeness.get((claim.subject, claim.predicate)) == claim
                for claim in claims)
            core_state, core_fact_ids = ("IDEMPOTENT" if identical else "APPLIED"), ()
        self._attestations = prospective
        self._sources[source.source_id] = source
        self._completeness = {**self._completeness,
                              **{(claim.subject, claim.predicate): claim for claim in claims}}
        self._superseded_by = {**self._superseded_by, **pending_superseded}
        return SidecarIngestReceipt(core_state, adapter_id, authority_sha256,
                                    core_fact_ids, source.sha256,
                                    "authorized sidecar batch atomically committed" if
                                    core_state == "APPLIED" else
                                    "identical authorized sidecar batch already committed")

    def query(self, program: TypedCausalProgram, *,
              as_of: int | None = None) -> TypedCausalResult:
        if as_of is not None and as_of < 0:
            return TypedCausalResult("unsupported", None, "", (),
                                     "invalid_sidecar_evaluation_time")
        if program.operator in ("COUNT_DISTINCT", "SUM"):
            return TypedCausalResult("unsupported", None, "", (),
                                     "sidecar_completeness_certificate_required")
        return self._query_verified(program, as_of=as_of)

    def _query_verified(self, program: TypedCausalProgram, *,
                        as_of: int | None = None) -> TypedCausalResult:
        result = self._memory.query(program)
        for proof in result.proofs:
            attested = self._attestations.get(proof.fact_id)
            source = self._sources.get(proof.source_id)
            authority = (None if attested is None else
                         next((item for item in self._authorities.values()
                               if item.authority_sha256 == attested.authority_sha256), None))
            if attested is None or source is None or authority is None or not \
                    attested.verify(authority, source):
                return replace(result, state="abstain", value=None, unit="", fact_ids=(),
                               proofs=(), reason="sidecar_attestation_failed_revalidation")
            lifecycle = attested.lifecycle
            if lifecycle.purpose != self.purpose:
                return replace(result, state="abstain", value=None, unit="", fact_ids=(),
                               proofs=(), reason="sidecar_purpose_boundary_violation")
            if as_of is None and lifecycle.valid_until is not None:
                return replace(result, state="abstain", value=None, unit="", fact_ids=(),
                               proofs=(), reason="sidecar_evaluation_time_required")
            if as_of is not None and not lifecycle.is_valid_at(as_of):
                reason = ("sidecar_fact_not_yet_valid" if as_of < lifecycle.valid_from else
                          "sidecar_fact_expired")
                return replace(result, state="abstain", value=None, unit="", fact_ids=(),
                               proofs=(), reason=reason)
        return result

    def completeness_certificate(self, subject: str,
                                 predicate: str, *,
                                 as_of: int | None = None) -> CompletenessCertificate | None:
        claim = self._completeness.get((subject, predicate))
        if claim is None:
            return None
        population = self._selected_population(
            tuple(item.fact for item in self._attestations.values()), subject, predicate)
        if population is None or population != claim.fact_ids:
            return None
        lifecycles = tuple(self._attestations[fact_id].lifecycle for fact_id in population)
        if ((as_of is None and any(item.valid_until is not None for item in lifecycles)) or
                (as_of is not None and any(not item.is_valid_at(as_of) for item in lifecycles))):
            return None
        attestations = [self._attestations[fact_id].attestation_sha256
                        for fact_id in population]
        population_digest = _digest(b"HORIZON-SIDECAR-POPULATION-v1", attestations)
        payload = {
            "claim_attestation_sha256": claim.attestation_sha256,
            "fact_attestations_sha256": population_digest,
            "fact_ids": list(population), "predicate": predicate,
            "scope": self.scope, "subject": subject, "evaluated_at": as_of,
        }
        certificate_digest = _digest(b"HORIZON-SIDECAR-CERTIFICATE-v1", payload)
        return CompletenessCertificate(
            self.scope, subject, predicate, population, population_digest,
            claim.attestation_sha256, as_of, certificate_digest)

    def query_certified(self, program: TypedCausalProgram,
                        certificate: CompletenessCertificate, *,
                        as_of: int | None = None) -> TypedCausalResult:
        if as_of is not None and as_of < 0:
            return TypedCausalResult("unsupported", None, "", (),
                                     "invalid_sidecar_evaluation_time")
        if program.operator not in ("COUNT_DISTINCT", "SUM"):
            return TypedCausalResult("unsupported", None, "", (),
                                     "certificate_only_applies_to_aggregation")
        if program.at_time is not None:
            return TypedCausalResult("unsupported", None, "", (),
                                     "historical_completeness_not_certified")
        expected = self.completeness_certificate(
            program.selector.subject, program.selector.predicate, as_of=as_of)
        if expected is None or certificate != expected:
            return TypedCausalResult("abstain", None, "", (),
                                     "invalid_or_stale_completeness_certificate")
        if program.operator == "COUNT_DISTINCT" and not certificate.fact_ids:
            return TypedCausalResult("resolved", "0", "count", (),
                                     "certified_empty_distinct_count")
        return self._query_verified(replace(program, closed_world=True), as_of=as_of)

    @property
    def fact_count(self) -> int:
        return self._memory.fact_count

    def verify_attestation(self, fact_id: int) -> bool:
        attested = self._attestations.get(fact_id)
        if attested is None:
            return False
        authority = next((item for item in self._authorities.values()
                          if item.authority_sha256 == attested.authority_sha256), None)
        source = self._sources.get(attested.fact.source_id)
        return bool(authority and source and attested.verify(authority, source))

    def attested_facts(self) -> tuple[AttestedSidecarFact, ...]:
        """Return an immutable, FactId-canonical audit snapshot."""
        return tuple(self._attestations[fact_id] for fact_id in sorted(self._attestations))
