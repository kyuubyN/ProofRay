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

- desktop: conversation list, chat and Observatory in three columns. Conversations are opened,
  renamed and deleted from the list itself, so `History` exists only on mobile, where there is no
  room for that rail;
- mobile: `Chat / History / Memory / Sources / Settings` navigation;
- a launch screen and a memory-activation strip drawn as the same monochrome Bayer-dithered pixel
  field, computed rather than shipped as an asset;
- reduced-motion users receive static proof states;
- the only semantic color is the small green `PFR` brain shown below an assistant
  message **only when ProofRay memory actually ran**.

## Choosing what answers

The composer holds one picker listing everything that is actually set up: API providers saved to
the vault, GGUF files on disk, and turning the AI off. Nothing unconfigured is offered, and the
selection is stored, so it survives a restart rather than reverting to whichever provider happens
to be first in the table.

## Local models

`local_models.py` scans a folder for GGUF by reading the file magic, not the extension, and starts
one `llama-server` at a time on a loopback port. A model is only reported ready once that server
answers: weights reach VRAM before llama.cpp serves anything, so spawning is not readiness.

Shutting it down cannot rely on a graceful path. The app terminates the embedded runtime outright,
so the bridge's own unload never runs and a server started from it survived the app, still holding
VRAM. On Linux the child is registered with `PR_SET_PDEATHSIG`, which moves the guarantee into the
kernel and holds however the parent goes away, including `kill -9`. That signal is scoped to the
spawning *thread*, not the process -- measured, not assumed -- so every spawn is funnelled through
one thread that lives as long as the runtime; spawning from a pooled worker killed a healthy model
as soon as that worker exited, which is worse than the leak. Both directions are covered by tests.

Chat then reaches it through the existing `openai_compatible` provider — llama.cpp exposes the same
`/v1` surface — so no part of the chat, memory or proof pipeline changes for a local model. Tool
mode stays off there: llama.cpp advertises tool calling per model and the app cannot know which
build is running.

`llama_installer.py` and `model_catalog.py` fetch the engine and the model files. Both verify the
SHA-256 that the source itself publishes (GitHub release assets, Hugging Face LFS oids) before
anything is unpacked or kept, refuse archives that would write outside the install directory, and
skip any file published without a checksum rather than trusting it. llama.cpp loads GGUF only, so
`.safetensors` and fp8 checkpoints are listed as skipped with that reason instead of being offered
as choices that would fail at load time.

## Packaging

Linux ships an AppArmor profile in
[`packaging/linux/apparmor`](packaging/linux/apparmor/io.proofray.proofray_app):

```bash
sudo install -m 0644 packaging/linux/apparmor/io.proofray.proofray_app /etc/apparmor.d/
sudo apparmor_parser -r /etc/apparmor.d/io.proofray.proofray_app
```

The point of the profile is not tamper resistance -- the source is public, and
anyone can rebuild the binary however they like. It bounds what a compromised or
buggy component can reach. That matters most for the llama.cpp build, which
arrives after installation and then gets executed: it runs under its own child
profile that can read one model file and answer on loopback, and is explicitly
denied the conversation database, `~/.ssh` and `~/.config`.

`./tool/verify_apparmor_profile.sh` checks the profile parses and that the
confinement is still expressed; it needs no privileges and is safe in CI. An
AppImage cannot be confined this way, because it mounts at a different path on
every run.

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
