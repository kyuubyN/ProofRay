// Package postgres implements connectors.Connector against a real PostgreSQL instance, the same
// backend HorizonAI Engine/examples/postgres_documents_example.py queries. There is no
// pure-Go/no-server stand-in here on purpose -- same reasoning as the Python example: this
// connector requires a real server and will not start one for you.
package postgres

import (
	"context"
	"fmt"
	"net"
	"strconv"

	"github.com/jackc/pgx/v5/pgxpool"

	"horizonmemory/connector/internal/connectors"
	"horizonmemory/connector/internal/document"
)

func init() {
	connectors.Register("postgres", New)
}

const defaultTable = "articles"

type Connector struct {
	pool         *pgxpool.Pool
	table        string
	source       string
	maxDocuments int
}

// New builds a Postgres connector from opts["dsn"] or POSTGRES_DSN, e.g.
// "postgresql://user:pass@localhost:5432/yourdb". Returns an error immediately -- no server
// started, no background retry -- when neither is set.
func New(ctx context.Context, opts connectors.Options) (connectors.Connector, error) {
	dsn := opts.Get("dsn", "POSTGRES_DSN", "")
	if dsn == "" {
		return nil, fmt.Errorf(
			"postgres: a DSN is required, e.g.\n" +
				`  POSTGRES_DSN="postgresql://user:pass@localhost:5432/yourdb"`,
		)
	}

	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return nil, fmt.Errorf("postgres: connecting: %w", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("postgres: ping: %w", err)
	}

	table := opts.Get("table", "POSTGRES_TABLE", defaultTable)
	if err := connectors.ValidateIdentifier(table); err != nil {
		pool.Close()
		return nil, fmt.Errorf("postgres: table name: %w", err)
	}

	maxDocuments, err := connectors.MaxDocuments(opts)
	if err != nil {
		pool.Close()
		return nil, fmt.Errorf("postgres: %w", err)
	}

	source, err := sourceOf(ctx, pool, table)
	if err != nil {
		pool.Close()
		return nil, fmt.Errorf("postgres: %w", err)
	}

	return &Connector{
		pool:         pool,
		table:        table,
		source:       source,
		maxDocuments: maxDocuments,
	}, nil
}

// sourceOf names the physical origin: which server, which database, which schema, which table.
//
// Built from the parsed connection config rather than the DSN string, so the user and password in
// the DSN are never part of it -- this value is rendered in the web UI and travels to the API
// inside every document's `source`.
//
// The schema is resolved rather than assumed: two DSNs differing only in `search_path` select
// physically different tables of the same name (the usual schema-per-tenant layout), and without
// it both would produce the same identity for row 42.
func sourceOf(ctx context.Context, pool *pgxpool.Pool, table string) (string, error) {
	config := pool.Config().ConnConfig

	var schema string
	if err := pool.QueryRow(ctx, "SELECT current_schema()").Scan(&schema); err != nil {
		return "", fmt.Errorf("resolving current schema: %w", err)
	}

	return fmt.Sprintf("postgres:%s/%s/%s/%s",
		net.JoinHostPort(config.Host, strconv.Itoa(int(config.Port))),
		config.Database, schema, table), nil
}

func (c *Connector) Name() string { return "postgres" }

// FetchDocuments runs "SELECT id, body FROM <table> ORDER BY id", the same query
// postgres_documents_example.py runs, and returns each row as one document. The caller owns the
// query -- swap the table/columns via POSTGRES_TABLE or by editing this method for a schema that
// doesn't match the example's (id, body) shape.
//
// The id is selected, not just ordered by: it becomes the document's identity, so a claim
// verified from this row can be traced back to it (see internal/document).
func (c *Connector) FetchDocuments(ctx context.Context) ([]document.Document, error) {
	rows, err := c.pool.Query(ctx, fmt.Sprintf("SELECT id, body FROM %s ORDER BY id", c.table))
	if err != nil {
		return nil, fmt.Errorf("postgres: query: %w", err)
	}
	defer rows.Close()

	acc := &document.Accumulator{
		Origin:       fmt.Sprintf("postgres: table %q", c.table),
		MaxDocuments: c.maxDocuments,
	}
	for rows.Next() {
		var id, body string
		if err := rows.Scan(&id, &body); err != nil {
			return nil, fmt.Errorf("postgres: scanning row: %w", err)
		}
		if err := acc.Add(document.New(c.source, id, body, acc.Len())); err != nil {
			return nil, err
		}
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("postgres: reading rows: %w", err)
	}
	return acc.Documents(), nil
}

func (c *Connector) Close() error {
	c.pool.Close()
	return nil
}
