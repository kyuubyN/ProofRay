package mysql

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"net"
	"strings"
	"testing"
	"time"

	mysqldriver "github.com/go-sql-driver/mysql"

	"horizonmemory/connector/internal/connectors"
)

// A previous version built this DSN with fmt.Sprintf, which let a value containing "?" smuggle
// extra driver parameters in through a plain form field -- "allowAllFiles=true" makes the client
// honor a malicious server's request to read arbitrary local files. FormatDSN escapes DBName,
// which closes that original database-field vector. This is a regression guard for that fix.
func TestDSNCannotInjectDriverParameters(t *testing.T) {
	cfg := driverConfig(
		"root", "secret", "localhost", "3306", "horizon_example?allowAllFiles=true")
	dsn := cfg.FormatDSN()

	if strings.Contains(dsn, "?allowAllFiles=true") {
		t.Fatalf("database name injected a driver parameter into the DSN: %s", dsn)
	}

	// Parsing back is the real check: whatever the escaping looks like, the flag must stay off.
	parsed, err := mysqldriver.ParseDSN(dsn)
	if err != nil {
		t.Fatalf("the DSN we build is not parseable by the driver: %v", err)
	}
	if parsed.AllowAllFiles {
		t.Error("AllowAllFiles was enabled through the database field")
	}
	if parsed.DBName != "horizon_example?allowAllFiles=true" {
		t.Errorf("DBName = %q, want the literal submitted value", parsed.DBName)
	}
}

// NewConnector consumes Config directly. Serializing and reparsing is unnecessary and lossy for
// values that themselves resemble DSN syntax: for example, ParseDSN truncates the user fixture
// below. Drive the real New path and capture the Config at the driver boundary so a regression to
// sql.Open("mysql", cfg.FormatDSN()) cannot pass this test.
func TestNewPassesExactFieldsToTheDriverConnector(t *testing.T) {
	const (
		user     = "root@tcp(evil:3306)/x?allowAllFiles=true#"
		password = "pw@tcp(evil:3306)/x?allowAllFiles=true#"
		host     = "h)/d?allowAllFiles=true&x=("
		port     = "3306"
		database = "literal?allowAllFiles=true"
	)

	// Prove the fixture distinguishes the implementations: this user value is changed by a
	// FormatDSN -> ParseDSN round trip, while the direct connector path below must retain it.
	roundTrip, err := mysqldriver.ParseDSN(
		driverConfig(user, "ordinary-password", "db.internal", port, "safe").FormatDSN())
	if err != nil {
		t.Fatalf("round-trip fixture is not parseable: %v", err)
	}
	if roundTrip.User == user {
		t.Fatal("fixture does not distinguish direct Config delivery from a DSN round trip")
	}

	originalOpen := openMySQLDB
	var captured mysqldriver.Config
	openMySQLDB = func(cfg *mysqldriver.Config) (*sql.DB, error) {
		captured = *cfg
		return sql.OpenDB(stubConnector{}), nil
	}
	t.Cleanup(func() { openMySQLDB = originalOpen })

	ctx, cancel := context.WithTimeout(context.Background(), 250*time.Millisecond)
	defer cancel()
	conn, err := New(ctx, connectors.Options{
		"host": host, "port": port, "user": user, "password": password,
		"database": database, "max_documents": "10",
	})
	if err != nil {
		t.Fatalf("New did not use the injected structured-config opener: %v", err)
	}
	defer conn.Close()

	wantAddr := net.JoinHostPort(host, port)
	if captured.User != user || captured.Passwd != password || captured.Addr != wantAddr ||
		captured.DBName != database || captured.Net != "tcp" {
		t.Errorf("driver received changed fields:\n  got:  user=%q password=%q addr=%q db=%q net=%q\n"+
			"  want: user=%q password=%q addr=%q db=%q net=%q",
			captured.User, captured.Passwd, captured.Addr, captured.DBName, captured.Net,
			user, password, wantAddr, database, "tcp")
	}
}

type stubConnector struct{}

func (stubConnector) Connect(context.Context) (driver.Conn, error) { return stubConn{}, nil }
func (stubConnector) Driver() driver.Driver                        { return stubDriver{} }

type stubDriver struct{}

func (stubDriver) Open(string) (driver.Conn, error) { return stubConn{}, nil }

type stubConn struct{}

func (stubConn) Prepare(string) (driver.Stmt, error) { return nil, driver.ErrSkip }
func (stubConn) Close() error                        { return nil }
func (stubConn) Begin() (driver.Tx, error)           { return nil, driver.ErrSkip }

func TestDSNPreservesOrdinaryValues(t *testing.T) {
	dsn := driverConfig("app", "p@ss w0rd/&?", "db.internal", "3307", "horizon_example").FormatDSN()

	parsed, err := mysqldriver.ParseDSN(dsn)
	if err != nil {
		t.Fatalf("unexpected parse error: %v", err)
	}
	if parsed.User != "app" {
		t.Errorf("User = %q, want %q", parsed.User, "app")
	}
	// A password full of DSN metacharacters has to survive the round trip intact, or the
	// escaping fix would break legitimate credentials.
	if parsed.Passwd != "p@ss w0rd/&?" {
		t.Errorf("Passwd = %q, want the literal submitted value", parsed.Passwd)
	}
	if parsed.Addr != "db.internal:3307" {
		t.Errorf("Addr = %q, want %q", parsed.Addr, "db.internal:3307")
	}
	if parsed.DBName != "horizon_example" {
		t.Errorf("DBName = %q, want %q", parsed.DBName, "horizon_example")
	}
}

func TestDSNHandlesIPv6Host(t *testing.T) {
	// net.JoinHostPort brackets an IPv6 literal; without it the DSN would be ambiguous.
	dsn := driverConfig("root", "", "::1", "3306", "horizon_example").FormatDSN()
	parsed, err := mysqldriver.ParseDSN(dsn)
	if err != nil {
		t.Fatalf("unexpected parse error: %v", err)
	}
	if parsed.Addr != "[::1]:3306" {
		t.Errorf("Addr = %q, want %q", parsed.Addr, "[::1]:3306")
	}
}
