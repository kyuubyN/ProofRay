# ProofRay App v1 release gates

No unchecked row is a claim of support.

## Platform release status

| Platform | Public status | CI policy |
|---|---|---|
| Linux x86_64 | **Public Alpha** as an AppImage | Required on pull requests: native acceptance, packaging and shared contracts. |
| Windows x86_64 | Experimental | Manual feasibility workflow only; no release claim. |
| Android arm64 | Experimental | Manual feasibility workflow only; no physical-device claim. |

The Linux release claim is deliberately narrower than “all v1 targets work.”
Hosted compilation is useful evidence, but it cannot substitute for testing the
secure store, lifecycle and bridge on a device the project controls.

## Feasibility spike

- [ ] Flutter 3.47.1 / Dart lock verified.
- [ ] CPython 3.12.13 starts on Android arm64, Linux x86_64 and Windows x86_64.
- [ ] NumPy and pure connector drivers import on all targets.
- [ ] DuckDB host plugin loads and queries on all targets.
- [ ] Twenty frozen cases have zero mismatch in state, ranking, answer, sources
      and proof bytes between desktop Python and embedded Python.
  Desktop reference digest frozen in the manifest:
  `11ba3aa0ae2f96fe27d28dbaa664c1b0300d611c212d624766edddba0922a043`.

Linux evidence already closed headlessly:

- [x] Flutter 3.47.1 / Dart 3.13.1 exact revision and lock verified.
- [x] CPython 3.12.13 package imports core, NumPy and every pure connector driver.
- [x] Twenty frozen cases reproduce the complete desktop digest exactly.
- [x] Repository DuckDB FFI opens, creates only the dedicated namespace and discovers it.
- [x] Full Flutter Linux bundle builds and runs its native first-launch,
      encrypted-memory, recall-marker and restart acceptance path on CI.

## Storage and recovery

- [ ] SQLCipher `cipher_version` is present on every target.
- [ ] Known plaintext is absent from DB, WAL, journal and backups.
- [ ] Wrong key always fails.
- [ ] Secure-vault fallback requires a passphrase on every opening and matches
      PBKDF2-HMAC-SHA256 at 600,000 iterations.
- [x] Crash injection passes before/after stage, compilation, sidecar commit,
      host ACK, index publication, answer commit and checkpoint commit.
- [x] Outbox replay, exact update retry, purge replay and every migration are
      fail-closed and idempotent in visible state.

Linux headless storage evidence:

- [x] `cipher_version` is present, correct key reopens and wrong key fails HMAC.
- [x] A known sentinel is absent from DB, WAL, journal and shared-memory files.
- [x] Unknown message authority rolls back sidecar publication and memory eligibility.
- [x] Connector checkpoint advancement and outbox ACK roll back together under
      injected failure, then replay successfully after process restart.
- [x] Historical schemas v1 through v7 reach v8 and reopen idempotently; v6
      authority reconstruction additionally proves that assistant text is not
      promoted into memory.
- [x] Staging and answer-commit failures unlock the composer, preserve the
      retryable outbox and stop ordered recovery at the first uncommitted turn.
- [x] Compiler exceptions leave host records and the published index unchanged;
      exact update/upsert retries return `IDEMPOTENT` without rewriting bytes.

## Product

- [x] PT-BR and English widget/golden suites pass on mobile and desktop sizes.
- [x] Text scaling at 200% and reduced motion pass without overflow or stale frames.
- [x] Green brain appears iff `memory_consulted=true`.
- [x] Model rewrite failure always displays exact certified text.
- [x] Personal-memory abstention never falls through to general model priors.
- [x] Linux first-launch acceptance path passes through restart with the
      expected reopened evidence and proof state in CI.

Headless product evidence:

- [x] PT-BR onboarding completes all five pages on a 360x640 viewport at 200%
      text scale without overflow.
- [x] First local profile/conversation, authoritative observation, proved
      answer, certificate and exact sources reopen byte-identically after a
      database restart. A packaged native click-through remains required.
- [x] Provider IDs resolve their actual rotating opaque vault handles; secret
      rotation deletes the prior handle and never derives a credential name.

## Connectors

- [x] Common contract passes for all nine connectors.
- [x] Existing sources are read-only; managed writes stay inside the dedicated namespace.
- [x] Pagination, 256-row batching, mapping, timeout, TLS and checkpoint crash tests pass.
- [ ] Containers pass for PostgreSQL, MySQL and Elasticsearch/OpenSearch.
- [x] Local fixtures pass for SQLite, SQLCipher and DuckDB; fake services pass
      for the remaining non-container connectors.
- [x] Elasticsearch mappings use a unique sortable keyword ID; `_id` is rejected
      as an incremental checkpoint rather than silently paginated unsafely.

## Local models and downloaded binaries

The app fetches a llama.cpp build and GGUF files after installation and then
executes one of them. That is a different trust boundary from everything above,
so it gets its own rows.

- [x] Engine and model downloads are verified against the SHA-256 the source
      itself publishes (GitHub release asset digest, Hugging Face LFS oid); a
      mismatch deletes the download and raises before anything is extracted.
- [x] A file published without a checksum is not offered at all, rather than
      downloaded unverified.
- [x] Archives that would write outside the install directory are refused
      before extraction, and an asset URL outside the release hosts is refused
      before any request is made.
- [x] `llama-server` is registered with `PR_SET_PDEATHSIG` on Linux, so it dies
      with the app however the app exits, including `kill -9`. Spawning is
      funnelled through one long-lived thread because that signal is scoped to
      the spawning thread; both directions are covered by tests.
- [x] The AppArmor profile parses, runs the downloaded engine under a separate
      child profile, and denies that profile the conversation database
      (`tool/verify_apparmor_profile.sh`).
- [ ] The AppArmor profile has been loaded on a real system and the app still
      starts, answers, and loads a local model under confinement.
- [ ] Windows and macOS have an equivalent parent-death guarantee. Only Linux
      is covered today; a Job Object (Windows) has not been implemented, so an
      orphaned server after an abnormal exit remains possible there.
- [ ] A hostile or truncated GGUF is handled without crashing the app.
- [ ] Disk-space exhaustion during a multi-gigabyte download fails cleanly and
      leaves no partial file that the model scan would offer.

## Performance and artifacts

- [ ] Bit Horizon p95 frame time is below 16.7 ms.
- [ ] 10k-message cold start is at most 5 s on the Android reference device.
- [ ] Idle RSS is at most 256 MiB; query peak at most 768 MiB.
- [ ] Each ABI build is at most 200 MiB or has an accepted size report.
- [ ] Every arm64 ELF in the final APK is AArch64 and has at least 16 KiB
      `PT_LOAD` alignment; CI rejects the artifact otherwise.
- [ ] APK, AAB, MSIX and AppImage are signed/reproducible release artifacts.

Desktop diagnostics do not close Android gates:

- [x] A profile-mode physical frame harness records Bit Horizon build+raster
      p95 on Linux CI; the 16.7 ms release assertion is reserved for the
      declared Android reference device.
- [x] A 10,000-message transcript materializes in less than five seconds in the
      headless Linux test database; Android cold start, RSS and frame timing
      remain physical-device gates.
