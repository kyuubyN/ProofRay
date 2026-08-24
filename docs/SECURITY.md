# Security policy

## Reporting a vulnerability

This private alpha has no supported public release. Do not place secrets,
credentials, private data or exploit details in a public issue. Report
suspected vulnerabilities privately to the repository owner via GitHub's
private vulnerability reporting (Security tab) once enabled for this
repository, or by direct contact with the maintainer listed in `AUTHORS.md`.
Include a minimal reproduction, the affected commit or release, and your
assessment of impact. There is no bug-bounty program; acknowledgement in
`AUTHORS.md`/release notes is offered at the maintainer's discretion.

## Security architecture, in brief

- The offline core (`horizon_memory`) has no network dependency in its
  execution path. Every read is verified against a cryptographically sealed
  generation (WAL-backed, HMAC/SHA-256-checked); a tampered or unverifiable
  source abstains rather than returning unauthenticated content.
- Remote-model adapters (`horizon_memory.adapters`) are the only network
  boundary and must be explicitly enabled by the integrating application.
  Secrets (API keys, tokens) must never be placed in prompts, URLs, ledgers,
  logs or exception text; adapters are expected to read credentials from the
  environment, not from stored facts.
- Everything an application retrieves through `EvidencePack`/`EvidenceItem` is
  **untrusted content for downstream prompts and tools**. Prompt-injection and
  anti-injection policy live in the reader/integration layer, not in the
  storage core — the core's guarantee is provenance and integrity of what was
  stored, not the safety of what a caller does with it afterward.
- `horizon_memory.content_safety` is a narrow, deterministic, zero-LLM
  keyword/pattern screen for physical-harm instructions, malware, sensitive
  PII/credentials and CSAM indicators — a different, narrower concern than the
  prompt-injection point above. It can run at two points: ingestion
  (`RouteDocument.__post_init__`, blocking construction outright via
  `UnsafeContentError`) and query time (`SemanticRouter.route`, aborting to
  `RouteState.ABSTAIN_UNSAFE_CONTENT` for an unsafe query or unsafe verified
  evidence). **Off by default at both points** (`safety_policy=None`) — an
  opt-in tool, not a hot-path default; pass `SafetyPolicy` (e.g.
  `DEFAULT_POLICY` for every category) to enable it for a specific document
  or route call. When enabled, every non-CSAM category is individually
  toggleable; CSAM has no override. It is a first-line, best-effort
  heuristic, not a guarantee, even when turned on — see the module's own
  docstring for its honest scope, including why CSAM specifically needs
  dedicated hash-matching infrastructure this module does not attempt to
  replace.
- Report a vulnerability, not a design disagreement, through the channel
  above: a verified integrity bypass, an authentication/authorization gap, a
  memory-safety issue, or a way to make the store return content that fails
  its own verification silently. Feature requests and architecture debate
  belong in an issue or discussion, not a private security report.

## Misuse and abuse

ProofRay is a persistent, provenance-tracking memory substrate. That
shape has real misuse potential distinct from ordinary software
vulnerabilities — most notably covert surveillance, non-consensual profiling,
and long-term tracking of individuals without their knowledge. This is a
responsible-use concern, not a code defect, and is addressed in
`RESPONSIBLE_USE.md` rather than here. If you believe a specific deployment
is actively causing harm, treat it as an abuse report to that deployment's
operator, not as a vulnerability report against this project.
