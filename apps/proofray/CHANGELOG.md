# ProofRay app changelog

## 0.1.0-alpha.2 — unreleased

The app became usable end to end: a conversation, a memory that recalls across
chats, and a model — hosted or local — that only ever phrases what ProofRay
proved.

### Local models

- Run GGUF models on your own machine through a llama.cpp build. The engine is
  installed from official GitHub releases and models are browsed live on
  Hugging Face; both are verified against the SHA-256 the source publishes
  before anything is unpacked, kept or executed.
- llama.cpp loads GGUF only, so `.safetensors` and fp8 checkpoints found in the
  same folder are listed as skipped with that reason instead of being offered
  as choices that would fail at load time.
- The server dies with the app. `PR_SET_PDEATHSIG` moves that guarantee into
  the kernel, where it holds however the app exits; the graceful path alone did
  not, and a model was observed still holding VRAM after the app had closed.
- The chat is covered by the pixel-wave loading screen while weights reach VRAM,
  because llama.cpp answers nothing until they do. Navigation, settings and the
  conversation list stay usable.

### Choosing what answers

- One picker in the composer lists what is actually set up: saved API providers,
  local models on disk, and turning the AI off. The selection is stored, so it
  survives a restart instead of reverting to whichever provider was first in the
  table.
- The model field is a list of the provider's own models, fetched from the
  provider, with free text still available for preview and experimental
  identifiers that no catalogue returns.

### Memory

- An answer now publishes every fact the engine selected, not only the best one.
  The previous rule compared reciprocal-rank scores for equality, which two
  distinct facts never satisfy, so a question with two supporting statements
  only ever showed one of them.
- Greetings no longer become permanent memory. An observation has to relate
  something to something — an anchor, or at least two content tokens — which
  rejects "oi", "ok" and "thanks" without keeping a list of greetings and
  without discarding short real statements like "gosto de café".
- Removing an authorized memory reports what happened. It previously failed
  silently on a missing core, an unexpected state, or a thrown error.

### Interface

- Conversations are opened, renamed and deleted from the desktop sidebar, so
  `History` remains only on mobile where there is no room for that rail.
- A launch screen and the memory-activation strip drawn as one monochrome
  Bayer-dithered field, computed rather than shipped as an asset.
- Buttons no longer stretch to the full width of the pane they sit in.
- Onboarding shows English by default and no longer asks for a model id, which
  nobody should need to know to finish a tutorial.

### Fixed

- Refreshing the conversation list threw on every rebuild: `setState` was handed
  a callback returning a `Future`, which Flutter rejects. The exception fired on
  every new-conversation click and left the interface unresponsive.
- A message could sit on its typing placeholder forever. The bridge stream had
  no deadline, and a stream that closed without a terminal event replaced
  nothing.
- Selecting "No AI" did nothing: the menu item carried no value, and
  `PopupMenuButton` reads a null result as the menu being dismissed.
- The AI provider was forgotten when switching conversations.
- The claim ranker penalised any candidate whose numbers differed from numbers
  in the question, regardless of whether they described the same quantity. On an
  open question that mentions any figure, this pushed the answer-bearing
  sentence far down the ranking.

### Packaging

- An AppArmor profile for Linux, with the downloaded llama.cpp build confined by
  a separate child profile that is denied the conversation database and has no
  reason to reach the internet. `tool/verify_apparmor_profile.sh` checks both
  the syntax and that the confinement is still there.

### Known gaps

- Only Linux has a parent-death guarantee for the local server. Windows would
  need a Job Object; that is not implemented, so an abnormal exit there can
  still leave an orphaned server.
- The AppArmor profile parses and is checked in CI, but has not been loaded on a
  real system with the app running under it.
- An AppImage cannot be confined by this profile: it mounts at a different path
  every run.
- Android and Windows release gates in
  [`docs/RELEASE_GATES.md`](docs/RELEASE_GATES.md) remain open. No unchecked row
  is a claim of support.
