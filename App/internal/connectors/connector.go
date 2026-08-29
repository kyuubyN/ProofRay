// Package connectors defines the shape every database backend plugs into. Horizon itself has no
// database of its own: a Connector's only job is to reach into one specific database, run one
// specific query, and hand back the documents it found. Everything downstream (routing,
// verification, composition) stays in the Python engine behind api/server.py.
//
// Each connector mirrors HorizonAI Engine/examples/*_documents_example.py -- the same query,
// wired to the real API instead of printed. It differs from those examples in one way that
// matters: a connector carries each record's identity along with its text (see
// internal/document), so a verified claim can be traced back to the row that produced it.
package connectors

import (
	"context"
	"fmt"
	"os"
	"regexp"
	"strconv"

	"horizonmemory/connector/internal/document"
)

// Connector fetches the current corpus from one database backend.
type Connector interface {
	// Name identifies the backend for logging (e.g. "postgres", "sqlite").
	Name() string

	// FetchDocuments runs the backend-specific query and returns the resulting rows as
	// structured documents -- each carrying the identity of the record it came from, so an
	// answer's provenance can be reopened against this database (see internal/document).
	FetchDocuments(ctx context.Context) ([]document.Document, error)

	// Close releases any held connection/pool. Safe to call on a Connector that never
	// connected successfully.
	Close() error
}

// Options carries per-call connector settings (e.g. a DSN typed into the web UI). A key absent
// from Options falls back to the matching environment variable -- the same *_DSN / *_URL
// variables each HorizonAI Engine example already reads (POSTGRES_DSN, MYSQL_HOST, REDIS_URL,
// ...) -- so the CLI (cmd/horizon-connect) keeps working with Options left nil. The web server
// (cmd/horizon-web) handles one request at a time per connection attempt, so passing values this
// way -- rather than mutating process-wide env vars -- keeps concurrent requests from racing each
// other's configuration.
type Options map[string]string

// Get returns opts[key] if non-empty, else os.Getenv(envVar) if non-empty, else fallback.
func (o Options) Get(key, envVar, fallback string) string {
	if v := o[key]; v != "" {
		return v
	}
	if envVar != "" {
		if v := os.Getenv(envVar); v != "" {
			return v
		}
	}
	return fallback
}

// Factory builds a Connector from the given Options (env-fallback per Options.Get). It returns
// an error immediately -- and starts nothing in the background -- when required configuration is
// missing, the same "print setup instructions and exit cleanly" contract the Python examples
// follow.
type Factory func(ctx context.Context, opts Options) (Connector, error)

var registry = map[string]Factory{}

// Register adds a backend under the given name and returns a function that restores the previous
// registration (or removes the new one). Connector init functions ignore the return value; tests
// use it for cleanup so temporary factories cannot leak into later tests through the global map.
func Register(name string, factory Factory) func() {
	previous, existed := registry[name]
	registry[name] = factory
	return func() {
		if existed {
			registry[name] = previous
			return
		}
		delete(registry, name)
	}
}

// Get looks up a previously registered backend by name.
func Get(name string) (Factory, bool) {
	factory, ok := registry[name]
	return factory, ok
}

// Names lists every backend registered so far (import side effects populate this at startup).
func Names() []string {
	names := make([]string, 0, len(registry))
	for name := range registry {
		names = append(names, name)
	}
	return names
}

// ErrCorpusTooLarge is an alias for document.ErrCorpusTooLarge, kept so callers matching on it
// do not have to import both packages.
var ErrCorpusTooLarge = document.ErrCorpusTooLarge

// MaxDocuments resolves the per-fetch document ceiling from opts/env (max_documents,
// HORIZON_MAX_DOCUMENTS).
//
// This can only LOWER the API's own ceiling, never raise it: api/_engine_bridge.py rejects more
// than document.MaxDocuments in one request regardless of what is configured here, so accepting
// a larger value would just promise something the server refuses. A value of 0 or less is
// rejected rather than read as "unlimited" -- an unbounded fetch is exactly what this prevents.
func MaxDocuments(opts Options) (int, error) {
	raw := opts.Get("max_documents", "HORIZON_MAX_DOCUMENTS", "")
	if raw == "" {
		return document.MaxDocuments, nil
	}
	value, err := strconv.Atoi(raw)
	if err != nil {
		return 0, fmt.Errorf("invalid max_documents %q: must be a positive integer", raw)
	}
	if value <= 0 {
		return 0, fmt.Errorf("invalid max_documents %d: must be greater than zero", value)
	}
	if value > document.MaxDocuments {
		return 0, fmt.Errorf(
			"invalid max_documents %d: the API accepts at most %d documents per request, so this "+
				"ceiling can only be lowered, not raised",
			value, document.MaxDocuments)
	}
	return value, nil
}

var identifierPattern = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*$`)

// ValidateIdentifier checks that name is safe to interpolate directly into a SQL identifier
// position (table/column name): letters, digits, underscore, not starting with a digit. The
// query drivers here (pgx, database/sql) only parameterize values, not identifiers, so every
// connector that builds a query with fmt.Sprintf around a caller-supplied table name -- e.g. one
// typed into the web UI, not just read from a trusted env var -- must run it through this first.
// A name that fails this check is rejected outright rather than escaped.
func ValidateIdentifier(name string) error {
	if !identifierPattern.MatchString(name) {
		return fmt.Errorf("invalid identifier %q: must match %s", name, identifierPattern.String())
	}
	return nil
}
