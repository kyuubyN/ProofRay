# ProofRay App

Native, local-first Flutter client for ProofRay. The UI does not use Flet and
model output never becomes memory authority automatically.

## Targets and pinned runtime

- Android 10+ arm64 (`minSdk` 29; Serious Python itself requires at least 23)
- Linux x86_64
- Windows 10+ x86_64
- Flutter 3.47.1 at `6655482ec06e547f90abf8ae7590466f4415978d`
- embedded CPython 3.12.13 through Serious Python 4.5.1
- pinned `tzdata 2026.3`, so IANA profile clocks reopen identically on Windows,
  Android and Linux even when the operating system has no zoneinfo database

The complete toolchain lock is in `toolchain.lock.json`. iOS, accounts, cloud
sync, continuous background synchronization and on-device model execution are
outside v1.

## Authority flow

```text
user turn staged in encrypted outbox
→ Python compiles exact surface authority
→ Flutter commits canonical sidecar suffix in SQLCipher
→ Flutter ACKs durability
→ Python publishes the staged index
→ answer / evidence / abstention / conflict
```

The SQLite file is physically owned by Flutter. Python can only request
canonical sidecar loads and replacements through the authenticated loopback
bridge. A failed or missing ACK never changes the visible index.

Questions and requests remain chat history but do not become factual evidence.
Generated model text has `memory_authority=none`. “Confirm as my memory” creates
a new, explicit user-attested observation. A model rewrite can be displayed only
after the deterministic number/name/polarity/protected-detail guard accepts it, and the exact
certified text remains instantly available.

## User experience

- desktop: conversation history, editorial chat and Observatory in three columns;
- mobile: `Chat / History / Memory / Sources / Settings` navigation;
- exactly 128 monochrome Bit Horizon columns, deterministic from query digest;
- reduced-motion users receive static proof states;
- the only semantic color is the small green `PFR` brain shown below an assistant
  message **only when ProofRay memory actually ran**.

## Development

The app must not silently use another toolchain:

```bash
./tool/generate_platform_shells.sh   # one-time, refuses to overwrite
./tool/verify_toolchain.sh
./tool/fetch_duckdb.sh Linux
dart run build_runner build
dart run flutter_launcher_icons
./tool/package_python.sh Linux   # or Android / Windows
./tool/build_platform.sh Linux debug
flutter analyze
flutter test
```

`package_python.sh` stages the public `proofray`/`horizon_memory` source plus the
app backend, fixes `SERIOUS_PYTHON_VERSION=3.12`, and packages the direct and
transitive pure-driver graph from exact pins in `requirements-mobile.txt`.
It must be run before a platform release build.

`generate_platform_shells.sh` copies only Android/Linux/Windows shells generated
by the exact pinned Flutter revision, applies Android 10 (`minSdk 29`), resolves
the versioned Dart lockfile and generates Drift code. It never builds or launches
the app and refuses to overwrite an existing platform directory.

The app has deliberately not been launched in the current implementation run.
Until the feasibility and release matrices in `docs/RELEASE_GATES.md` are filled
with real artifacts, this directory is an implementation candidate, not a
published binary claim.
