// Package sqlite implements connectors.Connector against a SQLite file, the same backend
// HorizonAI Engine/examples/sqlite_documents_example.py queries. Uses modernc.org/sqlite (pure
// Go, no cgo) so this connector needs no C toolchain to build.
package sqlite

import (
	"context"
	"database/sql"
	"fmt"
	"path/filepath"

	_ "modernc.org/sqlite"

	"horizonmemory/connector/internal/connectors"
	"horizonmemory/connector/internal/document"
)

func init() {
	connectors.Register("sqlite", New)
}

const defaultTable = "support_articles"

type Connector struct {
	db           *sql.DB
	table        string
	source       string
	maxDocuments int
}

// New builds a SQLite connector from opts["path"] or SQLITE_PATH, the path to an existing
// database file. Unlike the Python example (which builds a throwaway fixture DB for the demo),
// this connector expects a real file -- building fixtures is the caller's job, same as the
// example's own comment says for a production deployment.
func New(ctx context.Context, opts connectors.Options) (connectors.Connector, error) {
	path := opts.Get("path", "SQLITE_PATH", "")
	if path == "" {
		return nil, fmt.Errorf(
			"sqlite: a database path is required, e.g.\n" +
				`  SQLITE_PATH="/path/to/your.db"`,
		)
	}

	db, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, fmt.Errorf("sqlite: opening %s: %w", path, err)
	}
	if err := db.PingContext(ctx); err != nil {
		db.Close()
		return nil, fmt.Errorf("sqlite: ping: %w", err)
	}

	table := opts.Get("table", "SQLITE_TABLE", defaultTable)
	if err := connectors.ValidateIdentifier(table); err != nil {
		db.Close()
		return nil, fmt.Errorf("sqlite: table name: %w", err)
	}

	maxDocuments, err := connectors.MaxDocuments(opts)
	if err != nil {
		db.Close()
		return nil, fmt.Errorf("sqlite: %w", err)
	}

	// The absolute path names the physical origin: two databases with the same table name in
	// different files must not share document identities.
	absolute, err := filepath.Abs(path)
	if err != nil {
		absolute = path
	}

	return &Connector{
		db:           db,
		table:        table,
		source:       fmt.Sprintf("sqlite:%s/%s", absolute, table),
		maxDocuments: maxDocuments,
	}, nil
}

func (c *Connector) Name() string { return "sqlite" }

// FetchDocuments runs "SELECT id, body FROM <table> ORDER BY id", matching the (id, body) shape
// sqlite_documents_example.py's fixture uses. The id is selected, not just ordered by: it becomes
// the document's identity, so a claim verified from this row can be traced back to it (see
// internal/document).
func (c *Connector) FetchDocuments(ctx context.Context) ([]document.Document, error) {
	rows, err := c.db.QueryContext(ctx, fmt.Sprintf("SELECT id, body FROM %s ORDER BY id", c.table))
	if err != nil {
		return nil, fmt.Errorf("sqlite: query: %w", err)
	}
	defer rows.Close()

	acc := &document.Accumulator{
		Origin:       fmt.Sprintf("sqlite: table %q", c.table),
		MaxDocuments: c.maxDocuments,
	}
	for rows.Next() {
		var id, body string
		if err := rows.Scan(&id, &body); err != nil {
			return nil, fmt.Errorf("sqlite: scanning row: %w", err)
		}
		if err := acc.Add(document.New(c.source, id, body, acc.Len())); err != nil {
			return nil, err
		}
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("sqlite: reading rows: %w", err)
	}
	return acc.Documents(), nil
}

func (c *Connector) Close() error {
	return c.db.Close()
}
