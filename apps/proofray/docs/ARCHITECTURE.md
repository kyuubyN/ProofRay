# ProofRay App architecture

## Physical ownership

Flutter owns UI state, SQLCipher, migrations, the outbox, provider/connector
configuration and OS secret handles. Embedded Python owns ProofRay routing,
verification, proof readout, provider protocols and non-host connector logic.

The bridge is `proofray.app.bridge.v1`: authenticated JSON Lines over an
ephemeral TCP port bound to `127.0.0.1`. Frames are limited to 1 MiB. The first
frame authenticates a 256-bit launch token. The token crosses only the child
process environment and is removed from that environment immediately; it is
never written to the bootstrap file. The bootstrap file is deleted as soon as
Python reads it. Errors expose closed codes, not prompts, documents,
endpoints or secrets.

## Durable publication

`AuthorizedSidecarRecordStore` is the generic core boundary. Its historical
file implementation remains byte-compatible. The app implementation pages
canonical records from encrypted SQLite and replaces only the changed suffix.

Python always:

1. compiles the adapter once;
2. serializes and reopens the exact new canonical record;
3. validates it against an isolated copy-on-write candidate index;
4. asks Flutter to commit;
5. waits for the host ACK;
6. only then publishes the staged index.

Normal append never replays historical records. Cold recovery validates the
chain sequentially and constructs the causal query executor exactly once after
the last record. Purge and update use the stricter full replay rule: purge
rechains and revalidates the remaining field, while update removes the prior
active version and installs the increasing version in one durable replacement,
with no intermediate visible gap.

## Conversation authority

There is one personal field across chats. Each document retains its own
`session_id`, speaker, sequence and observed day, so cross-conversation recall
works without flattening topology.

- declarative user messages: candidate observations;
- questions/requests: history only;
- model messages: history, authority none;
- certified answers: derived receipts, not new facts;
- explicit confirmation: new user-attested observation.

Every query excludes its current `conversation:<thread>:<message>` source. This
makes an outbox retry idempotent without letting the just-committed question or
statement answer itself.
Conversation sequence allocation comes from the encrypted database's durable
`MAX(sequence)+1`, not the number of currently rendered messages. Failed
staging does not consume a slot; a successfully staged outbox pair does, even
when answer publication must be retried later.

## Provider boundary

Providers are configured without credentials. A key is read from the platform
vault immediately before `list_models`, `test_connection` or `stream_chat`, sent
as an in-memory lease and removed with the short-lived provider instance after
the call. Recent full conversation context is limited to 16 KiB. The corpus is
never sent to a model.

If a platform vault is unavailable, provider and connector credentials live
only in the current process and must be entered again after every launch. The
encrypted SQLite retains at most an opaque handle; it never becomes a fallback
credential vault. This is independent from the PBKDF2 fallback used to unlock
the local SQLCipher database itself.

Model discovery is separate from persistence. A discovered or user-declared
`supports_tools=false` capability is carried through encrypted configuration to
the composer; Tool mode is then disabled rather than attempted optimistically.
Certified rewrites must conserve numbers, names, polarity and every informative
lexeme. Rejection keeps the exact deterministic text visible.

After `proof.closed` or `evidence`, speculative model deltas are never painted.
The deterministic text and memory marker remain visible while the full optional
rewrite/summary is checked; only the accepted terminal payload can replace it.
The green marker also requires a preceding `memory.started` event in the same
authenticated request. A forged or out-of-order final `memory_consulted=true`
frame is rejected, remains retryable and renders as abstention without a brain.

## Connector boundary

SQLite and DuckDB connections are physically opened by Dart. MongoDB,
PostgreSQL, MySQL, Redis, DynamoDB, Elasticsearch/OpenSearch and SpacetimeDB use
short-lived Python connector instances with call-scoped credential leases.
Existing namespaces are read-only. A managed namespace can be created only
after a preview and a separate explicit authorization; that authorization is
injected into exactly that call and is never retained in connector config.

Mappings retain ID, text, source, session, sequence, event time, role, speaker,
version and scope. Sync batches contain at most 256 records. MongoDB ObjectIds,
DynamoDB composite keys and Redis cursors use JSON-safe reversible checkpoints;
the app commits a checkpoint only after the corresponding authoritative sidecar
batch has been acknowledged.

Elasticsearch/OpenSearch preview may expose the metadata `_id`, but incremental
sync requires a user-visible, unique sortable keyword field. The connector uses
that field with `search_after`; it never claims that Elasticsearch `_id` is a
durable ordered cursor. Reconfiguring any connector invalidates its preview
lease, and the backend accepts sync only for the exact mapping that was previewed.

The 1 MiB bridge limit is also enforced during rechain. A normal append sends
one minimal suffix frame. A larger purge/update derives a deterministic
transaction ID, stages bounded encrypted chunks, validates the complete suffix,
and swaps the visible ledger in one SQLite transaction. A crash before or after
the final ACK can replay the same transaction without publishing a partial
index. Chat observations are limited to 64 KiB and imported/mapped documents to
128 KiB so one canonical record always fits a bounded host frame, including
worst-case JSON escaping.

Connector sync authorization is also an encrypted outbox operation. The app
stages the exact previewed mapping and prior checkpoint before calling Python;
checkpoint advancement and outbox deletion commit together only after every
sidecar batch has been acknowledged. A crash repeats preview+sync from the old
checkpoint, while content-derived batch identities make that replay idempotent.
Each batch publishes exact retries, increasing-version updates and new rows in
one durable upsert transaction. A changed sealed source identity is rejected as
a FactId collision even when the incoming version is higher.

Local TXT/Markdown files are hashed and UTF-8-validated as streams before any
publication. JSON is additionally parsed as a stream under the same 64 MiB
per-file limit. A selected import is recorded by file digest and exact byte-span
source IDs; a later failure rolls back only newly applied chunks, never a
pre-existing idempotent import. Successful local imports remain separately
deletable.

Completed answer frames inline at most 384 KiB of JSON-escaped source text.
Larger exact sources are marked deferred and reopened one at a time by
`(FactId, source_id)` before receipt persistence or from the Sources tab. The
displayed answer/evidence/rewrite itself is always at most 24,576 UTF-8 bytes;
truncation is explicit and never upgrades evidence into proof.

## Restart identity

Chat history and proof receipts are distinct tables. Reload joins the exact
certificate bytes and source spans back onto the displayed assistant message,
so the Observatory survives restart. Hiding chat history does not silently
purge the retained memory source; deleting history and memory first purges and
rechains the sidecar, then hides the conversation.

Each sidecar row also carries the optional originating message ID. Suffix
replacement computes removed versus reinserted message memberships inside the
same SQLCipher transaction, so a crash after purge cannot leave the Memory tab
claiming that a source is still active.
