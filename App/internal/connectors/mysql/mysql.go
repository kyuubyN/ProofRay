// Package mysql implements connectors.Connector against a real MySQL instance, the same
// backend HorizonAI Engine/examples/mysql_documents_example.py queries. There is no pure-Go/
// no-server stand-in here on purpose -- same reasoning as the Python example: this connector
// requires a real server and will not start one for you.
package mysql

import (
	"context"
	"database/sql"
	"fmt"
	"net"

	mysqldriver "github.com/go-sql-driver/mysql"

	"horizonmemory/connector/internal/connectors"
	"horizonmemory/connector/internal/document"
)

func init() {
	connectors.Register("mysql", New)
}

const defaultTable = "articles"

type Connector struct {
	db           *sql.DB
	table        string
	source       string
	maxDocuments int
}

// New builds a MySQL connector from opts/env: host (MYSQL_HOST, required), port (MYSQL_PORT,
// default "3306"), user (MYSQL_USER, default "root"), password (MYSQL_PASSWORD), database
// (MYSQL_DB, default "horizon_example") -- the same variables mysql_documents_example.py reads.
func New(ctx context.Context, opts connectors.Options) (connectors.Connector, error) {
	host := opts.Get("host", "MYSQL_HOST", "")
	if host == "" {
		return nil, fmt.Errorf(
			"mysql: a host is required, e.g.\n" +
				`  MYSQL_HOST=localhost MYSQL_USER=root MYSQL_PASSWORD=secret MYSQL_DB=yourdb`,
		)
	}
	port := opts.Get("port", "MYSQL_PORT", "3306")
	user := opts.Get("user", "MYSQL_USER", "root")
	password := opts.Get("password", "MYSQL_PASSWORD", "")
	database := opts.Get("database", "MYSQL_DB", "horizon_example")

	// Pass the structured config straight to the driver. A FormatDSN -> ParseDSN round trip is
	// unnecessary and can change fields containing DSN syntax (notably user names); NewConnector
	// preserves the exact values the caller supplied.
	cfg := driverConfig(user, password, host, port, database)
	db, err := openMySQLDB(cfg)
	if err != nil {
		return nil, fmt.Errorf("mysql: configuring driver: %w", err)
	}
	if err := db.PingContext(ctx); err != nil {
		db.Close()
		return nil, fmt.Errorf("mysql: ping: %w", err)
	}

	table := opts.Get("table", "MYSQL_TABLE", defaultTable)
	if err := connectors.ValidateIdentifier(table); err != nil {
		db.Close()
		return nil, fmt.Errorf("mysql: table name: %w", err)
	}

	maxDocuments, err := connectors.MaxDocuments(opts)
	if err != nil {
		db.Close()
		return nil, fmt.Errorf("mysql: %w", err)
	}

	// Built from cfg's parsed fields, never the formatted DSN: that string carries the password,
	// and this value is rendered in the web UI and sent to the API in every document.
	return &Connector{
		db:           db,
		table:        table,
		source:       fmt.Sprintf("mysql:%s/%s/%s", cfg.Addr, cfg.DBName, table),
		maxDocuments: maxDocuments,
	}, nil
}

// openMySQLDB is a narrow seam for verifying that New hands the exact structured Config to the
// driver without requiring a live MySQL server. Tests replace it only for the duration of one
// non-parallel test; production only reads it.
var openMySQLDB = func(cfg *mysqldriver.Config) (*sql.DB, error) {
	driverConnector, err := mysqldriver.NewConnector(cfg)
	if err != nil {
		return nil, err
	}
	return sql.OpenDB(driverConnector), nil
}

// driverConfig is shared with the regression tests so they exercise the exact structured config
// New passes to mysql.NewConnector, rather than a test-only DSN builder.
func driverConfig(user, password, host, port, database string) *mysqldriver.Config {
	cfg := mysqldriver.NewConfig()
	cfg.User = user
	cfg.Passwd = password
	cfg.Net = "tcp"
	cfg.Addr = net.JoinHostPort(host, port)
	cfg.DBName = database
	return cfg
}

func (c *Connector) Name() string { return "mysql" }

// FetchDocuments runs "SELECT id, body FROM <table> ORDER BY id", the same query
// mysql_documents_example.py runs, and returns each row as one document. The id is selected, not
// just ordered by: it becomes the document's identity, so a claim verified from this row can be
// traced back to it (see internal/document).
func (c *Connector) FetchDocuments(ctx context.Context) ([]document.Document, error) {
	rows, err := c.db.QueryContext(ctx, fmt.Sprintf("SELECT id, body FROM %s ORDER BY id", c.table))
	if err != nil {
		return nil, fmt.Errorf("mysql: query: %w", err)
	}
	defer rows.Close()

	acc := &document.Accumulator{
		Origin:       fmt.Sprintf("mysql: table %q", c.table),
		MaxDocuments: c.maxDocuments,
	}
	for rows.Next() {
		var id, body string
		if err := rows.Scan(&id, &body); err != nil {
			return nil, fmt.Errorf("mysql: scanning row: %w", err)
		}
		if err := acc.Add(document.New(c.source, id, body, acc.Len())); err != nil {
			return nil, err
		}
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("mysql: reading rows: %w", err)
	}
	return acc.Documents(), nil
}

func (c *Connector) Close() error {
	return c.db.Close()
}
