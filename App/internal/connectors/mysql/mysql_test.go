package mysql

import (
	"net"
	"strings"
	"testing"

	mysqldriver "github.com/go-sql-driver/mysql"
)

// buildDSN mirrors exactly how New assembles its DSN. It exists so the escaping can be tested
// without a live server: New itself dials before returning, so it cannot be called here.
func buildDSN(user, password, host, port, database string) string {
	cfg := mysqldriver.NewConfig()
	cfg.User = user
	cfg.Passwd = password
	cfg.Net = "tcp"
	cfg.Addr = net.JoinHostPort(host, port)
	cfg.DBName = database
	return cfg.FormatDSN()
}

// A previous version built this DSN with fmt.Sprintf, which let a value containing "?" smuggle
// extra driver parameters in through a plain form field -- "allowAllFiles=true" makes the client
// honor a malicious server's request to read arbitrary local files. FormatDSN escapes each field
// instead. This is a regression guard for that fix, not a test of the driver.
func TestDSNCannotInjectDriverParameters(t *testing.T) {
	dsn := buildDSN("root", "secret", "localhost", "3306", "horizon_example?allowAllFiles=true")

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

func TestDSNPreservesOrdinaryValues(t *testing.T) {
	dsn := buildDSN("app", "p@ss w0rd/&?", "db.internal", "3307", "horizon_example")

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
	dsn := buildDSN("root", "", "::1", "3306", "horizon_example")
	parsed, err := mysqldriver.ParseDSN(dsn)
	if err != nil {
		t.Fatalf("unexpected parse error: %v", err)
	}
	if parsed.Addr != "[::1]:3306" {
		t.Errorf("Addr = %q, want %q", parsed.Addr, "[::1]:3306")
	}
}
