# App — Horizon database connector (Go)

A Go-side companion to `HorizonAI Engine/examples/*_documents_example.py`. HorizonMemory itself
has no database backend — "connecting a database" is entirely the caller's own query, and this app
is that caller, written in Go instead of Python, so a single compiled binary can pull from a real
database and hit the running Horizon API without a Python runtime on the connector side.

The Python engine (`../api/server.py`) still does all the actual work — routing, verification,
composition. This binary's only job is: fetch rows from one backend, hand them to
`POST /v1/answers` as `documents`, print (or render) the answer.

**Documents keep their provenance.** `POST /v1/answers` accepts two shapes (see
`build_documents` in `../api/_engine_bridge.py`): a legacy `[]string`, and a structured object
carrying each document's identity. This app sends the structured form. The legacy form makes the
server synthesize an identity from array position (`fact_id` = index, `source` = `"doc:N"`), which
throws away the primary key the connector just read — a row that shifts position between two
fetches silently changes identity, and a verified claim cannot be traced back to the record it
came from. Instead, each connector selects its table's key alongside the text and emits it as
`fact_id`/`source` (see `internal/document`), so `source` reads
`postgres:db.internal:5432/prod/public/articles:42` rather than `doc:1` — host, port, database,
schema and table, so two servers or schemas holding the same table name never share a document
identity. The `fact_id` is
SHA-256 of that source plus the primary key, truncated to the server's 62-bit domain. The source
is always built from the driver's *parsed* connection config, never the DSN string, so a password
in a DSN never reaches the page or the API. Each document also carries a `text_sha256` the server
recomputes, making the text's integrity verifiable end to end across the connector, the network
hop, and the JSON encoding.

## Layout

```
App/
  cmd/horizon-connect/main.go     CLI entry point — env vars in, one answer printed as JSON
  cmd/horizon-web/main.go         web UI entry point — form in the browser instead of env vars
  internal/config/                env-var configuration (CLI only)
  internal/horizonclient/         HTTP client for api/server.py (types.go mirrors its JSON contract)
  internal/webui/                 the /ask form handler + embedded html/template
  internal/document/              the structured document type sent to POST /v1/answers
  internal/connectors/
    connector.go                  the Connector interface, Options, ValidateIdentifier, registry,
                                  and MaxDocuments (the per-fetch corpus ceiling)
    postgres/                     github.com/jackc/pgx/v5
    sqlite/                       modernc.org/sqlite (pure Go, no cgo)
    mysql/                        github.com/go-sql-driver/mysql
    mongodb/                      go.mongodb.org/mongo-driver
    redis/                        github.com/redis/go-redis/v9
    dynamodb/                     github.com/aws/aws-sdk-go-v2 (+ DynamoDB Local support)
    elasticsearch/                github.com/elastic/go-elasticsearch/v8
  testdata/
    docker-compose.yml            one throwaway container per backend, for local testing
    seed.sh / seed_dynamodb.go    loads the same "Meridian"/"Solstice" fixture into all of them
```

All seven connectors are real implementations, not stubs — every one has been run end-to-end
against a real instance of its backend (see "How this was verified" below). Both entry points
share every connector: `cmd/horizon-connect` builds a connector's `Options` from env vars
(`config.Load`), `cmd/horizon-web` builds the same `Options` from submitted form fields
(`internal/webui`) — neither the `connectors.Connector` interface nor a given connector's query
logic changes between the two.

Each connector subpackage mirrors one `HorizonAI Engine/examples/*_documents_example.py` file and
reads the same environment variables/schema that example does (`POSTGRES_DSN` + table `articles`,
`MONGODB_URI` + database `support_kb`/collection `articles`, `REDIS_URL` + key prefix
`articles:`, ...), so anyone who has already set one up for the Python example can point this at
the same database. Two deliberate departures from the Python examples, both because this is Go
serving concurrent requests (the web UI) rather than a one-shot script:

- The Python mongodb/redis/dynamodb examples fall back to an in-process mock (`mongomock`,
  `fakeredis`, `moto`) when no real endpoint is configured, so they run with zero setup. These Go
  connectors always require a real endpoint — there's no equivalent pure-Go in-process mock wired
  up here.
- `dynamodb` adds an endpoint override (`DYNAMODB_ENDPOINT_URL` / the web form's "Endpoint
  override" field) the Python example doesn't have, specifically so it can point at DynamoDB
  Local instead of real AWS. Everything else about it — table shape, scan-then-sort-by-id logic —
  matches the Python example's real-AWS mode.

## Adding a new backend

1. Add a subpackage under `internal/connectors/<name>/` implementing `connectors.Connector`
   (`Name`, `FetchDocuments`, `Close`) and a `New(ctx context.Context, opts connectors.Options)
   (connectors.Connector, error)` factory that calls `connectors.Register("<name>", New)` from
   `init()`. Read settings via `opts.Get("key", "ENV_VAR", "fallback")` — this checks the
   caller-supplied value first (e.g. a web form field) and falls back to the environment variable
   only when that's empty, so the same connector works from both entry points unchanged. Any
   caller-supplied value used as a SQL table/column name (not a query parameter) must be checked
   with `connectors.ValidateIdentifier` before being interpolated into a query string — see
   `postgres`/`sqlite`/`mysql` for the pattern; the query APIs here only parameterize values, not
   identifiers, and the web form is the untrusted-input path that makes this matter.
2. Blank-import it in both `cmd/horizon-connect/main.go` and `cmd/horizon-web/main.go`.
3. `go get` its driver and run `go mod tidy`.
4. Add its Options keys to `connectorFields` in `internal/webui/webui.go`, and a
   `<div data-connector-fields="name">...</div>` block to
   `internal/webui/templates/index.html.tmpl` (see the existing blocks) with one input per key,
   named `name_key` (e.g. `mysql_host`) — the web form's fields are namespaced per connector so
   two backends' same-named settings (e.g. both `mysql` and `mongodb` have a "database" field)
   never collide in the one shared `<form>`.

## Running it

Go (1.27, via Homebrew) and every connector here are verified working: `go build ./...`, `go vet
./...`, and `gofmt -l .` all pass clean, and all seven connectors have been run end-to-end —
via both `cmd/horizon-connect` and, screenshotted through an actual browser, `cmd/horizon-web` —
against real instances of every backend and a real `api/server.py`.

```bash
cd App
go mod tidy     # resolves pinned driver versions in go.mod, writes go.sum

# start the Python API first (from the repo root):
#   pip install -e . && pip install -r api/requirements.txt
#   python3 api/server.py   # serves http://127.0.0.1:8420
```

### CLI (`cmd/horizon-connect`)

```bash
CONNECTOR=sqlite \
SQLITE_PATH=/path/to/your.db \
QUESTION="What percent did the Meridian project reduce cost by?" \
  go run ./cmd/horizon-connect
```

Env vars `config.Load` reads:

- `CONNECTOR` (required) — registered backend name (`postgres`, `mysql`, `mongodb`, `redis`,
  `dynamodb`, `elasticsearch`, `sqlite`).
- `QUESTION` (required) — forwarded as-is to `POST /v1/answers`.
- `HORIZON_API_BASE_URL` (default `http://127.0.0.1:8420`) — where `api/server.py` is listening.
- `INCLUDE_SOURCES` (`1` to enable) — requests the full verified claim list, not just the
  compressed answer.
- `PROOFRAY_API_TOKEN` / `HORIZON_API_TOKEN` (optional) — see "Authenticating with HorizonAPI"
  below; usually unnecessary, `horizonclient` finds the token on its own.
- Per-backend (all optional except where noted; see each connector's `New` for full details):
  - `postgres`: `POSTGRES_DSN` (required), `POSTGRES_TABLE` (default `articles`)
  - `sqlite`: `SQLITE_PATH` (required), `SQLITE_TABLE` (default `support_articles`)
  - `mysql`: `MYSQL_HOST` (required), `MYSQL_PORT` (3306), `MYSQL_USER` (root),
    `MYSQL_PASSWORD`, `MYSQL_DB` (horizon_example), `MYSQL_TABLE` (articles)
  - `mongodb`: `MONGODB_URI` (required), `MONGODB_DATABASE` (support_kb),
    `MONGODB_COLLECTION` (articles)
  - `redis`: `REDIS_URL` (required), `REDIS_KEY_PREFIX` (articles:)
  - `dynamodb`: `AWS_DEFAULT_REGION` (required), `DYNAMODB_TABLE` (articles),
    `DYNAMODB_ENDPOINT_URL` (optional, for DynamoDB Local), plus standard
    `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`
  - `elasticsearch`: `ELASTICSEARCH_URL` (required), `ELASTICSEARCH_INDEX` (articles)

### Web UI (`cmd/horizon-web`)

```bash
go run ./cmd/horizon-web
# open http://127.0.0.1:8080
```

Pick a database from the dropdown, fill in its connection fields (only the fields relevant to the
selected backend show, via a small inline script), type a question, submit. Same connectors, same
`horizonclient`, same HorizonAPI underneath — this is `cmd/horizon-connect`'s form-driven twin, not
a different pipeline. `HORIZON_API_BASE_URL` (default `http://127.0.0.1:8420`) and `WEB_ADDR`
(default `127.0.0.1:8080`, bind only to a trusted interface — see "Not done here" below) are its
main env vars; everything else comes from the form on each request. `PROOFRAY_API_TOKEN` /
`HORIZON_API_TOKEN` and the credentials-path overrides from "Authenticating with HorizonAPI" below
also apply here, though they're normally unnecessary. For `dynamodb`, credentials
still come from the process environment (`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`) — not a form
field, same reasoning as `api/server.py` never taking a polish API key in the request body (see
`../api/README.md`).

The page's top nav has two buttons, "Benchmark Demo" and "Database Connector" (the current page,
shown active) — the same pair appears on `../website/`'s demo page with the active side flipped.
They're cross-links, not a merge: `../website/web_app.py` (Flask, MemGym-DR benchmark demo) and
`cmd/horizon-web` (this Go server) stay two independent processes on two different ports; each
just knows the other's URL so a visitor can jump between them. `HORIZON_BENCHMARK_URL` (default
`http://127.0.0.1:5050`, matching `website/web_app.py`'s own default port) points this page's
button at wherever that Flask demo is actually running; `website/web_app.py` has the mirror env
var, `HORIZON_CONNECTOR_URL` (default `http://127.0.0.1:8080`), for its own "Database Connector"
button. This page's whole look (dark palette, serif heading, monospace labels/buttons, sharp
1-2px corners) is deliberately copied from `website/templates/index.html`'s CSS variables and
type scale, not just the nav bar, so the two feel like one product wearing two different forms
rather than two unrelated tools bolted together.

### Authenticating with HorizonAPI

`api/server.py` requires a bearer token on every route except `GET /v1/health` (added after this
Go client was first written — see `api/machine_auth.py`). `horizonclient` handles this
automatically and needs no configuration in the common case: on every request it looks for
`PROOFRAY_API_TOKEN` or `HORIZON_API_TOKEN` in the environment first, and otherwise reads the token
straight out of the same credentials file `api/server.py` itself generates on first run
(`~/.config/proofray/api_credentials.json`, or `~/.config/horizon-memory/api_credentials.json` for
an installation predating the rebrand; `%APPDATA%\proofray\api_credentials.json` on Windows).
`horizonclient` only ever reads that file — it never creates or rotates the token, so if
`api/server.py` has never been run, there is nothing to read yet and requests will 401 until it has
been started at least once.

Set `PROOFRAY_API_CREDENTIALS_PATH` / `HORIZON_API_CREDENTIALS_PATH` to point at a non-default
credentials file (mirrors the same override `api/machine_auth.py` supports), or `PROOFRAY_API_TOKEN`
to skip the file lookup entirely (e.g. in CI, or when the API and the Go client don't share a
filesystem).

### Testing against real backends locally

`testdata/docker-compose.yml` brings up one throwaway instance of every backend:

```bash
docker compose -f App/testdata/docker-compose.yml up -d
App/testdata/seed.sh                                    # postgres, mysql, mongo, redis, elasticsearch
cd App && AWS_ACCESS_KEY_ID=local AWS_SECRET_ACCESS_KEY=local go run testdata/seed_dynamodb.go
```

Then point either entry point at `127.0.0.1` on each service's mapped port (postgres is mapped to
`5433` to avoid colliding with a real Postgres on the default `5432`). `docker compose -f
App/testdata/docker-compose.yml down` tears everything back down; nothing here uses a volume, so
there's no state to clean up beyond that.

## How this was verified

All seven connectors were run against real backends, not mocked: Postgres, MySQL, MongoDB, Redis,
and Elasticsearch each in its own Docker container (via `testdata/docker-compose.yml`), and
DynamoDB against `amazon/dynamodb-local`. Each was seeded with the same three-document "Meridian
project reduced compute cost by exactly 42 percent..." fixture the Python examples use, then
queried through `cmd/horizon-connect` and confirmed `"state": "resolved"` with the right answer
text. `cmd/horizon-web` was additionally driven through an actual Chromium instance (a connected
Maestri browser portal) for all seven backends, each one screenshotted showing the `RESOLVED`
badge and correct answer rendered in the page — not just a curl response. The test containers were
torn down afterward (ephemeral, `--rm`); `testdata/` is what's left for reproducing the same setup.

## Automated tests and CI

```bash
cd App
go test ./...            # add -race to match CI
gofmt -l . && go vet ./...
```

`.github/workflows/app-go.yml` runs `gofmt`, `go mod tidy -diff`, `go build`, `go vet`,
`go test -race`, and `govulncheck` on any pull request touching `App/`. This module is not covered
by `ci.yml` (Python) or `proofray-app.yml` (the Flutter/Python native app), so without that
workflow nothing checked it.

The tests deliberately cover the parts that can be exercised without a live database, which is
also where the security-relevant logic lives:

- `internal/document` — that a `fact_id` follows the record's key rather than its position, keeps
  two physical sources distinct (different host, port, database, table or file), stays inside the
  server's identity domain, and that the wire format matches the schema `api/_engine_bridge.py`
  validates (it rejects unknown fields, so a rename breaks every request). Also the corpus
  limits: that the byte budget stops a fetch long before the document count would.
- `internal/connectors` — `ValidateIdentifier` against the injection payloads it exists to reject,
  and `MaxDocuments` rejecting a zero/negative ceiling rather than reading it as "unlimited".
- `internal/connectors/mysql` — a regression guard for the DSN-parameter injection fixed in Round
  3: a `database` field containing `?allowAllFiles=true` must not enable that flag.
- `internal/webui` — that a submitted password/DSN/URI never survives into the re-rendered page,
  and that a driver's error message is redacted before it is rendered or logged (a connection or
  DSN-parse error routinely quotes the connection string back, password included).
- `internal/horizonclient` — that `GET /v1/health` stays unauthenticated, every other route sends
  the bearer token, a missing token fails before the request rather than as an ambiguous 401, and
  no error path echoes the token back to the caller (it would land in the page's error message).
- `cmd/horizon-web` — that the loopback bind check refuses `0.0.0.0`, `:8080`, `[::]` and LAN/public
  literals, while honoring the explicit opt-out.

Connector query logic against real backends is still verified by hand (see above), not in CI:
those tests need live servers. `.github/workflows/proofray-app.yml` already provisions Postgres,
MySQL and Elasticsearch services for the Python connector contracts, so that is the natural place
to grow Go integration tests later.

## Security audit (Round 3, Maestri terminals)

The connector code went through the project's established multi-terminal audit workflow
(Antigravity + OpenCode via Maestri; Antigravity refused the scope on its own safety guardrail,
same as prior rounds, so OpenCode covered both halves). 17 raw findings came back; verified
against the actual code (not just the model's stated severities) before acting on any of them.
Two were confirmed real vulnerabilities and fixed:

- **MySQL DSN parameter injection (was High).** `mysql.go` built the DSN with `fmt.Sprintf`, so a
  `database` value like `horizon_example?allowAllFiles=true` was parsed by the driver as an extra
  connection parameter, not a literal db name — confirmed exploitable (verified `AllowAllFiles`
  flipped to `true` after a round-trip through the old code). Fixed by building a `mysql.Config`
  struct and calling `.FormatDSN()` instead, which `url.PathEscape`s each field independently
  (confirmed the same round-trip now leaves `AllowAllFiles: false` and the `?` literally
  percent-encoded).
- **Credentials echoed back into the HTML form (was High).** After a failed submission, every
  field — including `mysql_password` and any DSN/URI/URL — was written back into the page's
  `value="..."` attributes. A `type="password"` input only masks the rendered widget; the actual
  value still sits in the page's HTML source, readable via view-source/devtools regardless.
  Fixed: `webui.go` now keeps raw submitted values only long enough to build that request's
  connector `Options`, and never copies `password`/`dsn`/`uri`/`url` fields into what the template
  redisplays (verified: submitting a password now returns zero occurrences of it in the response
  body, while the connector still connects correctly with it).

Also added, cheap and unconditionally worth it: `Content-Security-Policy` (`default-src 'self'`,
blocking the page from loading or submitting to any other origin), `X-Frame-Options: DENY`,
`X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin` on every response. And two
dependencies — `github.com/jackc/pgx/v5` and `golang.org/x/text` — were upgraded after
`govulncheck ./...` (the actual Go vulnerability database, not the model's self-reported CVE
numbers, several of which didn't check out) found reachable advisories in both; `govulncheck`
reports zero reachable vulnerabilities now.

The remaining findings (CSRF, rate limiting, TLS, per-connector host allowlisting, blocklisting
system-ish index/collection names) are real observations but not new code changes — they're the
same "no auth, trusted network only" tradeoff `../api/README.md` already made for `api/server.py`,
just showing up again here because these connectors are, by design, "connect to whatever
DSN/URI/host the caller gives you." See the entries below for exactly what that means and where
the line is.

## Security audit (Round 4, Maestri terminals) — auth integration

After `origin/main` added `api/machine_auth.py` (bearer-token auth on every route but
`GET /v1/health`), this client was updated to send that token automatically — see
"Authenticating with HorizonAPI" above. That small diff (`auth.go`, new; `client.go`, the header)
plus the one-line fix to `api/machine_auth.py::verify_bearer_token` (see `../api/README.md` or its
own docstring — `secrets.compare_digest` raised `TypeError` instead of failing cleanly on a
non-ASCII bearer token) went through the same multi-terminal workflow again.

Antigravity refused the scope again, same guardrail as every prior round; redirected to Codex this
time (the user switched to Codex Plus specifically because Antigravity had stopped being useful).
Codex reviewed the `machine_auth.py` fix and found nothing wrong — its own test with non-ASCII and
emoji tokens confirmed a clean 401, no exception, matching what was already verified by hand before
asking for the audit. OpenCode reviewed `auth.go`/`client.go`: 7 findings, 0 Critical/High, 4
Medium, 3 Low. Three were cheap and worth taking:

- Falling back to the pre-rebrand `~/.config/horizon-memory/api_credentials.json` path now logs a
  warning instead of silently using a possibly-stale token.
- A credentials file readable by users other than its owner (looser than the `0600`
  `api/machine_auth.py` itself writes) now logs a warning.
- No token found at all now returns a distinct `ErrNoToken` instead of sending the request without
  an `Authorization` header and letting the server's own 401 stand in for it — that 401 looked
  identical to "wrong token," which made a simple misconfiguration (a typoed env var name) harder
  to tell apart from an actual bad credential.

Two were checked and are not real issues, not applied:

- A CRLF/control character in the token "could inject an HTTP header" — tested directly: Go's
  `net/http` rejects any request with such a header value outright (`invalid header field value`)
  before it reaches the wire. There's no smuggling path here to fix.
- Re-reading the credentials file on every request (rather than caching it once) was flagged as a
  TOCTOU race. The described attacker already needs local write access to this process's own
  `~/.config/proofray/` — at that point they can read the token file directly; re-reading it
  doesn't hand them a new capability. Reading fresh on every request is also what lets a
  long-running `horizon-web` process pick up the token once `api/server.py` generates it, without
  needing a restart, so it stays as-is on purpose.

Token rotation/refresh and custom TLS/mTLS support were flagged again as gaps but are scope
decisions, not bugs (the TLS one is the same one named below since Round 3).

## Corpus limits

Four limits apply, all mirrored from the API in `internal/document` so a corpus that cannot be
sent fails while it is being read rather than as an HTTP 413 after the whole thing has been pulled
out of the database:

| Limit | Value | Applies to | Mirrors |
| --- | --- | --- | --- |
| Documents per request | 2000 | count | `MAX_DOCUMENTS` (`api/_engine_bridge.py`) |
| Bytes per document | 64 KiB | the **text** only | `MAX_DOCUMENT_BYTES` (`api/_engine_bridge.py`) |
| Bytes per metadata field | 4 KiB | `source`, `session` | `MAX_METADATA_BYTES` (`api/_engine_bridge.py`) |
| Bytes per request body | 1 MiB | the whole encoded body | `MAX_CONTENT_LENGTH` (`api/server.py`) |

The per-document limit is measured against the text alone, matching the server's
`_utf8_size(text)` check — measuring the encoded document instead would reject records the server
accepts, since the JSON also carries `fact_id`, `source`, `session`, the digest and every field
name. Metadata is validated too (length, and the control characters the server rejects), because
`source` is built from backend-supplied data: a 5 KiB Redis key, or one containing a newline, is
legal in Redis and would otherwise be answered with a 400. Text and metadata must also be valid
UTF-8: Go's JSON encoder replaces invalid bytes with U+FFFD, which would otherwise change both the
wire size and the text the API hashes after `text_sha256` was calculated.

The **byte budget is the one that binds in practice**: 2000 documents at 64 KiB each would be
128 MiB, so a document count alone never establishes that a corpus is sendable. A fetch stops as
soon as the accumulated body would exceed 1 MiB, and `horizonclient` re-checks the assembled
payload before sending. Both report `ErrCorpusTooLarge` rather than truncating — an answer
composed from a quietly truncated corpus looks exactly as verified as one composed from all of it.

`max_documents` / `HORIZON_MAX_DOCUMENTS` can **lower** the document ceiling but never raise it:
the API rejects more than 2000 per request regardless, so a larger value is refused at startup
rather than promising something the server will not honor.

## Not done here (named on purpose)

- **Inbound auth, CSRF protection, rate limiting, and TLS for the web UI.** The Python API has its
  own machine bearer-token authentication and per-peer rate limiting, and `horizonclient` resolves
  and sends that bearer token. `cmd/horizon-web` itself still has no authentication, CSRF token,
  request rate limiter, or TLS listener. Because that browser-facing database dialer is open,
  `cmd/horizon-web` **refuses to start on a non-loopback address**: `WEB_ADDR` must resolve to
  loopback, or `HORIZON_WEB_ALLOW_REMOTE=1` must be set to accept the exposure deliberately. The
  risk was documented here long before it was enforced, which did nothing to prevent a stray
  `WEB_ADDR=0.0.0.0:8080` in a compose file from publishing an unauthenticated web UI that will
  dial any database host it is handed. Setting the opt-out does not add auth — it only records
  that the operator meant it.
- **No host allowlist — every connector is an intentional SSRF surface.** Each connector connects
  to whatever host/DSN/URI the caller provides (`POSTGRES_DSN`, `MYSQL_HOST`, `MONGODB_URI`,
  `REDIS_URL`, `ELASTICSEARCH_URL`, `SQLITE_PATH`) — that's the entire point of a generic "bring
  your own database" connector, not an oversight. The consequence: anyone who can reach
  `cmd/horizon-web`'s `/ask` endpoint can make the server originate a connection to any host it can
  route to (an internal service, a cloud metadata endpoint, a host the caller controls to harvest
  whatever the connector sends on connect). This is the same trust boundary as "no auth" above,
  restated for this specific risk — an `ALLOWED_DB_HOSTS`-style allowlist would close it, but isn't
  built here; until one is, treat reachability to `cmd/horizon-web` as equivalent to reachability
  to every database it's configured to reach.
- **DynamoDB credentials come from the ambient AWS SDK chain, not a form field.** `dynamodb.go`
  calls `config.LoadDefaultConfig`, which picks up whatever the host machine has configured —
  env vars, `~/.aws/credentials`, or (the sharper edge) an EC2/ECS/Lambda IAM role the operator
  didn't have to type in anywhere. If `cmd/horizon-web` runs on a host with a real IAM role
  attached and gets exposed beyond a trusted network, any visitor to the form can make it scan
  whatever DynamoDB tables that role can reach — with no credential ever having touched the
  request. Deliberately not "fixed" by requiring explicit per-request credentials: that would mean
  the connector behaves differently under the CLI vs. the web server, breaking the one-`Connector`
  abstraction both entry points share. Mitigation is operational: don't attach a broad IAM role to
  a host running `cmd/horizon-web` outside a trusted network.
- **Streaming large corpora.** Every `FetchDocuments` still accumulates the whole result set in
  memory before sending it, same as the Python examples; nothing here streams. What it no longer
  does is return a *partial* corpus silently — `dynamodb` follows `LastEvaluatedKey` to the end of
  the table and `elasticsearch` scrolls the whole index, rather than reading one page and
  presenting it as everything. A corpus genuinely larger than the API accepts needs chunking on
  both sides, which is not built here; it is refused rather than truncated (see below).
- **In-process mocks for mongodb/redis/dynamodb.** The Python examples can run with zero setup
  via `mongomock`/`fakeredis`/`moto`; these Go connectors always need a real endpoint (DynamoDB
  Local counts as "real" here, since it speaks the actual wire protocol).
- **`event_time` on a document is never populated.** The structured schema accepts a per-record
  timestamp and `internal/document` carries the field, but no connector fills it: the schemas
  these connectors mirror (`articles(id, body)`) have no timestamp column, and picking one per
  backend is a schema decision rather than something to infer. `span`, `role` and `speaker` are
  not sent either — `span` marks a subrange of a larger source text, but here each record is one
  whole document (the spans in an answer's `source` are computed by the engine during
  verification), and `role`/`speaker` describe conversation turns, which a database row is not.
- **No sample corpus for the demo.** `website/web_app.py` now locates `mock_dataset.jsonl`
  wherever it sits and explains what is missing when it is absent, but the file itself is ~1.1 GB
  and gitignored — so a fresh clone still cannot run the demo without obtaining it separately. A
  small versioned sample would fix that; none is committed here.
