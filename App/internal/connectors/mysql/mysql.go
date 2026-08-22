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
)

func init() {
	connectors.Register("mysql", New)
}

const defaultTable = "articles"

type Connector struct {
	db    *sql.DB
	table string
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

	// mysql.Config.FormatDSN escapes each field independently -- unlike building the DSN with
	// fmt.Sprintf, a value containing "?" or "&" (e.g. database="horizon_example?allowAllFiles=true")
	// can't inject extra driver parameters this way.
	cfg := mysqldriver.NewConfig()
	cfg.User = user
	cfg.Passwd = password
	cfg.Net = "tcp"
	cfg.Addr = net.JoinHostPort(host, port)
	cfg.DBName = database

	db, err := sql.Open("mysql", cfg.FormatDSN())
	if err != nil {
		return nil, fmt.Errorf("mysql: opening: %w", err)
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

	return &Connector{db: db, table: table}, nil
}

func (c *Connector) Name() string { return "mysql" }

// FetchDocuments runs "SELECT body FROM <table> ORDER BY id", the same query
// mysql_documents_example.py runs, and returns each row's body as one document.
func (c *Connector) FetchDocuments(ctx context.Context) ([]string, error) {
	rows, err := c.db.QueryContext(ctx, fmt.Sprintf("SELECT body FROM %s ORDER BY id", c.table))
	if err != nil {
		return nil, fmt.Errorf("mysql: query: %w", err)
	}
	defer rows.Close()

	var documents []string
	for rows.Next() {
		var body string
		if err := rows.Scan(&body); err != nil {
			return nil, fmt.Errorf("mysql: scanning row: %w", err)
		}
		documents = append(documents, body)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("mysql: reading rows: %w", err)
	}
	return documents, nil
}

func (c *Connector) Close() error {
	return c.db.Close()
}
