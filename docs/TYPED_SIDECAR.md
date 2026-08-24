# Authorized typed sidecar

The typed sidecar is Horizon's strict controlled-input path for systems that already know
their own schema: databases, event buses, application state, sensors, forms and tools. It is
standalone and deterministic. It does not call an LLM, create embeddings, access a network or
pretend to infer unrestricted language semantics.

## Trust boundary

The source proves the literal `value` through an exact character span. The host-approved
sidecar manifest is the authority for `subject` and `predicate`. Every fact attestation binds:

- source identity, SHA-256 and exact span;
- every typed fact field, clock, version, polarity and causal edge;
- adapter ID, rule ID/version and schema SHA-256;
- exact scope and predicate capabilities;
- purpose, authorization reference, validity interval and update lineage.

Changing any bound field changes the attestation. This is an integrity and authority contract,
not a claim that metadata was independently derived from prose.

## Minimal use

```python
import hashlib

from horizon_memory import (
    AuthorizedSidecarMemory, CausalAdapterBatch, CausalSelector,
    DeclarativeSidecarAdapter, SidecarAuthority, SidecarFactDeclaration,
    SidecarLifecycle, StructuredCausalDeclaration, TypedCausalProgram,
)

schema = b'{"device":"string","state":"string"}'
authority = SidecarAuthority(
    adapter_id="device-sidecar",
    rule_id="device-schema",
    rule_version=1,
    schema_sha256=hashlib.sha256(schema).hexdigest(),
    allowed_scopes=("home",),
    allowed_predicates=("state",),
    purpose="remember device state",
)

source = '{"device":"printer","state":"ready"}'
start = source.index("ready")
declaration = StructuredCausalDeclaration(
    fact_id=1, scope="home", subject="printer", predicate="state",
    value="ready", source_span=(start, start + 5),
    observed_at=1, event_time=1, event_id="printer-state",
)
lifecycle = SidecarLifecycle(
    valid_from=1, valid_until=None, purpose=authority.purpose,
    authorization_reference="policy:home-memory",
)
batch = CausalAdapterBatch(
    "device-db-row-42", source, "home",
    (SidecarFactDeclaration(declaration, lifecycle),),
)

memory = AuthorizedSidecarMemory("home", (authority,))
assert memory.ingest(DeclarativeSidecarAdapter(authority), batch).state == "APPLIED"
result = memory.query(TypedCausalProgram(
    "LOOKUP", CausalSelector("printer", "state")))
assert result.value == "ready"
```

Use `DurableAuthorizedSidecarMemory` with the same authority registry for an fsync-backed,
hash-chained correctness baseline. Recovery revalidates every record through the live strict
boundary before publishing its query index.

Existing deterministic adapters do not need to be copied. `AuthorizedAdapterBridge` can wrap an
adapter such as `JsonPointerCausalAdapter` when its exact `adapter_id` is named by the authority
manifest. The bridge may add lifecycle policy but cannot rewrite facts or source spans.

For unrestricted text, `OpenTextHorizonMemory` uses the same boundary with deliberately minimal
authority: the sealed source contains an exact `surface_document` span. Retrieval and composition may
propose evidence from it, but no semantic predicate is silently upgraded to truth. Observed turn
queries can be supplied as `AnswerContextIntent`; they steer composition and are never answer labels.
Pass `ledger_path=` to persist the open-text sidecar. Reopening reconstructs only attested
`surface_document` facts; additional bundles may be appended with new FactIds. New open-text facts
may attach `SidecarRouteMetadata` inside the same attestation and JSONL record, preserving scope,
session, version, generation, sequence, event time, role, speaker and source span without changing
source text. Metadata-free historical facts retain their exact v1 serialization; metadata-bearing
facts use the `HORIZON-SIDECAR-FACT-v2` attestation domain. FactId-bound observed context intents are
replicated across every member of their fiber and use `HORIZON-SIDECAR-FACT-v3`; recovery requires
identical copies, exact FactId coverage, a coherent session and canonical insertion order. They remain
routing observations rather than factual predicates, but no longer disappear after restart.

## Aggregation and updates

`COUNT_DISTINCT` and `SUM` cannot be enabled by setting `closed_world=True`. The sidecar must
carry an `AttestedCompletenessClaim`; Horizon then issues a population-bound
`CompletenessCertificate`. A new matching fact makes an old certificate stale.

An update uses a new FactId and strictly newer version in the same event orbit. Its
`SidecarLifecycle.supersedes` must name the complete active prior orbit. An asserted-false
new version is a terminal invalidation; Horizon does not resurrect the older value. Finite TTL
facts require an explicit `as_of` query time and fail closed outside their interval.

## Current limits

- Natural-language question-to-program compilation is not part of this contract.
- The durable v1 ledger rewrites the complete file on commit. It is a correctness baseline,
  not the multi-year compaction design.
- Local hash chaining detects mutation but not adversarial rollback to an older valid file;
  rollback resistance needs a monotonic authority outside that file.
- Purging a file is not a secure-erasure guarantee for the storage medium.
- Accuracy claims for open-language benchmarks cannot be transferred to structured sidecar
  execution, or vice versa.
- Digests are tamper-evidence and identity bindings, not signatures against malicious code already
  executing inside the trusted host process. Isolate untrusted adapters at the process boundary.
