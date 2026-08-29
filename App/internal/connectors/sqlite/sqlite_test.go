package sqlite

import (
	"context"
	"database/sql"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"horizonmemory/connector/internal/connectors"
)

func TestNewRejectsMissingDatabaseWithoutCreatingIt(t *testing.T) {
	path := filepath.Join(t.TempDir(), "typo.db")

	conn, err := New(context.Background(), connectors.Options{"path": path})
	if conn != nil {
		conn.Close()
	}
	if err == nil {
		t.Fatal("New accepted a missing SQLite database")
	}
	if _, statErr := os.Stat(path); !os.IsNotExist(statErr) {
		t.Errorf("New created the misspelled database path: %v", statErr)
	}
}

func TestExistingDatabasePathRejectsMissingFilesWithoutCreatingThem(t *testing.T) {
	path := filepath.Join(t.TempDir(), "missing.db")

	if _, err := existingDatabasePath(path); err == nil {
		t.Fatal("missing SQLite file was accepted")
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Errorf("validating a missing path created a file: %v", err)
	}
}

func TestExistingDatabasePathRejectsDirectories(t *testing.T) {
	if _, err := existingDatabasePath(t.TempDir()); err == nil {
		t.Error("directory was accepted as a SQLite database file")
	}
}

func TestExistingDatabasePathCanonicalizesSymlinks(t *testing.T) {
	dir := t.TempDir()
	realPath := filepath.Join(dir, "real.db")
	if err := os.WriteFile(realPath, nil, 0o600); err != nil {
		t.Fatal(err)
	}
	alias := filepath.Join(dir, "alias.db")
	if err := os.Symlink(realPath, alias); err != nil {
		t.Skipf("symlinks unavailable: %v", err)
	}

	gotReal, err := existingDatabasePath(realPath)
	if err != nil {
		t.Fatal(err)
	}
	gotAlias, err := existingDatabasePath(alias)
	if err != nil {
		t.Fatal(err)
	}
	if gotAlias != gotReal {
		t.Errorf("symlink identity %q differs from physical file %q", gotAlias, gotReal)
	}
}

func TestSQLiteDSNTreatsQuestionMarkAsFilenameData(t *testing.T) {
	path := filepath.Join(t.TempDir(), "records?_pragma=query_only(1).db")
	dsn := sqliteDSN(path)

	parsed, err := url.Parse(dsn)
	if err != nil {
		t.Fatal(err)
	}
	if parsed.Path != path {
		t.Fatalf("decoded SQLite path = %q, want %q", parsed.Path, path)
	}
	if parsed.Query().Get("_pragma") != "" || parsed.Query().Get("mode") != "rw" {
		t.Fatalf("filename became SQLite query parameters: %q", dsn)
	}
	if !strings.Contains(dsn, "%3F_pragma") {
		t.Fatalf("question mark in filename was not escaped: %q", dsn)
	}
}

func TestSQLiteDSNModeRWRefusesToCreateAfterValidation(t *testing.T) {
	path := filepath.Join(t.TempDir(), "removed-before-open.db")
	db, err := sql.Open("sqlite", sqliteDSN(path))
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()

	if err := db.Ping(); err == nil {
		t.Fatal("mode=rw opened a missing database")
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Errorf("SQLite recreated the missing database: %v", err)
	}
}
