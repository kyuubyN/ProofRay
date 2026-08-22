// Package postgres implements connectors.Connector against a real PostgreSQL instance, the same
// backend HorizonAI Engine/examples/postgres_documents_example.py queries. There is no
// pure-Go/no-server stand-in here on purpose -- same reasoning as the Python example: this
// connector requires a real server and will not start one for you.
package postgres

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5/pgxpool"

	"horizonmemory/connector/internal/connectors"
)

func init() {
	connectors.Register("postgres", New)
}

const defaultTable = "articles"

type Connector struct {
	pool  *pgxpool.Pool
	table string
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

	return &Connector{pool: pool, table: table}, nil
}

func (c *Connector) Name() string { return "postgres" }

// FetchDocuments runs "SELECT id, body FROM <table> ORDER BY id", the same query
// postgres_documents_example.py runs, and returns each row's body as one document. The caller
// owns the query -- swap the table/columns via POSTGRES_TABLE or by editing this method for a
// schema that doesn't match the example's (id, body) shape.
func (c *Connector) FetchDocuments(ctx context.Context) ([]string, error) {
	rows, err := c.pool.Query(ctx, fmt.Sprintf("SELECT body FROM %s ORDER BY id", c.table))
	if err != nil {
		return nil, fmt.Errorf("postgres: query: %w", err)
	}
	defer rows.Close()

	var documents []string
	for rows.Next() {
		var body string
		if err := rows.Scan(&body); err != nil {
			return nil, fmt.Errorf("postgres: scanning row: %w", err)
		}
		documents = append(documents, body)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("postgres: reading rows: %w", err)
	}
	return documents, nil
}

func (c *Connector) Close() error {
	c.pool.Close()
	return nil
}
