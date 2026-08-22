// Package connectors defines the shape every database backend plugs into. Horizon itself has no
// database of its own -- every call to HorizonAnswerEngine (and its HTTP twin, POST /v1/answers)
// takes a plain []string of documents. A Connector's only job is: reach into one specific
// database, run one specific query, and hand back that []string. Everything downstream (routing,
// verification, composition) stays in the Python engine behind api/server.py.
//
// This mirrors HorizonAI Engine/examples/*_documents_example.py one-to-one: each example is a
// query against one backend, printed instead of shipped over HTTP. A Connector is the same query,
// wired to the real API instead.
package connectors

import (
	"context"
	"fmt"
	"os"
	"regexp"
)

// Connector fetches the current corpus from one database backend.
type Connector interface {
	// Name identifies the backend for logging (e.g. "postgres", "sqlite").
	Name() string

	// FetchDocuments runs the backend-specific query and returns the resulting rows as plain
	// text, in the same []string shape POST /v1/answers expects for its "documents" field.
	FetchDocuments(ctx context.Context) ([]string, error)

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

// Register adds a backend under the given name. Called from each connector subpackage's init(),
// the same self-registration pattern database/sql drivers use.
func Register(name string, factory Factory) {
	registry[name] = factory
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
