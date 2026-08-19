# Roadmap: standalone packaging

Feasibility notes from a 2026-08-19 discussion, written down for later -- nothing described here
is built yet. Each item states what it needs, honestly, rather than assuming it's "just
packaging."

## 1. Binary + installer

**Feasible, standard tooling, no architectural blocker.** PyInstaller or Nuitka can bundle the
whole engine (core + `api/server.py`/`api/mcp_server.py`) into a single executable that doesn't
require the end user to have Python installed. Wrapping that in a platform installer (Inno
Setup/MSI on Windows, a signed `.pkg`/`.dmg` on macOS, `.deb`/AppImage on Linux) is well-trodden
territory -- nothing about how Horizon is built gets in the way. Not started; no packaging
tooling exists in this repo today (already noted as deferred in
[`../api/README.md`](../api/README.md)).

**Licensing reminder, since this is exactly the scenario that motivated the licensing
conversation in the first place**: shipping a compiled binary to a third party is still
*distributing* the AGPL-licensed engine inside it. Compiling to a binary does not change or
avoid the license -- see [`LICENSE_COMMERCIAL_PLACEHOLDER.md`](LICENSE_COMMERCIAL_PLACEHOLDER.md)
and [`../LICENSE_POLICY.md`](../LICENSE_POLICY.md). Whatever commercial terms eventually get
drafted need to account for the binary distribution path specifically, not just the hosted-API
path.

## 2. A small local GUI ("MongoDB Compass"-style)

**Feasible for the shell** -- status, start/stop, and displaying the ready-to-copy endpoint URLs
is straightforward once the HTTP API (already built) is running locally; could be a small local
web page, or a thin desktop wrapper (Tauri/Electron/etc.) around one. Nothing new to design here
beyond normal UI work.

**Not feasible today for "paste a database URL and it just reads from it"** -- see the next
section. A GUI can only expose a control that isn't backed by anything yet.

## 3. "Point Horizon at a database URL" -- a real subsystem, not a checkbox

Horizon has zero database connectivity today. Every call takes `documents: list[str]` (or
`tuple[RouteDocument, ...]`) directly -- the caller has already turned their data into text
before Horizon ever sees it (see
[`README.md`'s "Connect a database" section](README.md#connect-a-database-bring-your-own-documents)
for the current, real pattern: query it yourself, pass the rows in).

Making "paste a connection string, Horizon reads from it automatically" real would need, at
minimum:
- A connection layer per database family (a relational DB, a document store, a vector store are
  three different problems, not one).
- A decision about what actually becomes a "document" -- an entire table? A user-authored query?
  Automatic schema introspection? Each answer has different failure modes.
- Credential handling (storage, rotation, least-privilege access) at a level this project hasn't
  needed to solve yet -- every existing secret (`GROQ_KEY`, `GEMINI_API_KEY`, etc.) is read from
  an environment variable at call time and never persisted; a saved DB connection string is a
  different, harder problem.

This is comparable in size to the work already done for the polish adapter and MCP server, not
an incremental add-on. Scope it as its own effort when it's actually prioritized.

## 4. Non-factual messages ("oi", small talk) -- confirmed safe, but the integration matters

Real, measured behavior (2026-08-19, `HorizonAnswerEngine.answer()` and the full
`_horizon_ask_impl()` response shape, no code changes needed to produce this -- it already works
this way):

```
msg='oi'            state=ABSTENTION   answer_lines=0  answer_text=''
msg='hi'             state=ABSTENTION   answer_lines=0  answer_text=''
msg='how are you?'   state=ABSTENTION   answer_lines=0  answer_text=''
msg='kkkkkk'          state=ABSTENTION   answer_lines=0  answer_text=''
msg='obrigado!'       state=ABSTENTION   answer_lines=0  answer_text=''
```

And through the full API-shaped path with `polish: true`:

```json
{
  "state": "abstention",
  "answer": "",
  "answer_lines": [],
  "verified_candidates": 0,
  "polished_answer": null,
  "polish_state": "skipped_abstained"
}
```

Horizon already does the safe thing: no evidence to route or verify against a greeting means a
clean abstain, never a hallucinated or wrong answer, and (per `answer_engine.py`'s own design)
zero network calls wasted on the polish step when there's nothing to polish.

**The actual risk is in integration, not in Horizon itself**: if whoever wires an AI chat
product to Horizon routes *every* user message through it -- including small talk -- as if it
were a factual lookup, the end user sees an empty/abstained response to "oi" instead of a normal
greeting back. The fix belongs at the orchestration layer around Horizon, not inside it:

- A cheap triage step (an intent check, a classifier, or a simple rule) decides whether a message
  needs Horizon's grounding at all before calling it.
- On `state: "abstention"` / `polish_state: "skipped_abstained"`, the calling application should
  fall back to a normal, ungrounded conversational reply -- Horizon already reports this state
  cleanly; the caller just needs to branch on it.

Deliberately not fixed inside `HorizonAnswerEngine` itself: adding conversational/small-talk
handling to the core would mean putting model judgment inside the deterministic engine, which is
exactly the "no LLM/API inside the memory core" ground rule this project holds to. Worth adding
as a documented pattern (maybe a short example) in `README.md` once this folder's tutorial gets
its next pass -- not done yet.
