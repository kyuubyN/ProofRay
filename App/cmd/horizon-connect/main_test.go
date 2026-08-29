package main

import (
	"bytes"
	"errors"
	"strings"
	"testing"
)

func TestSafeErrorDoesNotPrintEnvironmentCredentials(t *testing.T) {
	for _, key := range sensitiveEnvironmentKeys {
		t.Setenv(key, "")
	}
	const password = "CLI-SECRET-PASSWORD-789"
	dsn := "postgres://app:" + password + "@db.internal:5432/prod"
	t.Setenv("POSTGRES_DSN", dsn)
	t.Setenv("MYSQL_PASSWORD", "xy")
	t.Setenv("AWS_ACCESS_KEY_ID", "AKIA-CLI-TEST-IDENTIFIER")

	var output bytes.Buffer
	writeError(&output, errors.New(
		"failed "+dsn+`; password=xy; access=AKIA-CLI-TEST-IDENTIFIER; connection refused`))
	got := output.String()

	if strings.Contains(got, password) || strings.Contains(got, dsn) || strings.Contains(got, "password=xy") ||
		strings.Contains(got, "AKIA-CLI-TEST-IDENTIFIER") {
		t.Errorf("CLI error leaked a configured credential: %s", got)
	}
	if !strings.Contains(got, "connection refused") {
		t.Errorf("redaction removed the diagnosis: %s", got)
	}
	if !strings.HasPrefix(got, "horizon-connect: ") {
		t.Errorf("stderr line lost the command prefix: %q", got)
	}
}
