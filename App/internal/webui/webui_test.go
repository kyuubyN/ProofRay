package webui

import (
	"strings"
	"testing"
)

// redactSensitiveFields is what keeps a submitted password or DSN from being echoed back into the
// page's HTML. A masked <input type="password"> does not help: the value in a value="..."
// attribute is plain text in the page source. These cases are the exact form keys the template
// re-renders after a submit.
func TestRedactSensitiveFieldsDropsCredentials(t *testing.T) {
	raw := map[string]string{
		"mysql_password":    "hunter2",
		"postgres_dsn":      "postgres://user:secret@db.internal:5432/app",
		"mongodb_uri":       "mongodb://user:secret@db.internal:27017",
		"elasticsearch_url": "http://user:secret@es.internal:9200",
		"redis_url":         "redis://:secret@cache.internal:6379",
		"mysql_host":        "db.internal",
		"mysql_user":        "app",
		"postgres_table":    "articles",
		"question":          "who wrote this?",
	}

	redacted := redactSensitiveFields(raw)

	droppedKeys := []string{
		"mysql_password", "postgres_dsn", "mongodb_uri", "elasticsearch_url", "redis_url",
	}
	for _, key := range droppedKeys {
		if _, present := redacted[key]; present {
			t.Errorf("%q survived redaction; it would be rendered back into the page", key)
		}
	}

	// The secret must not survive under ANY key -- a value copied into a differently named field
	// would leak just as thoroughly as the original.
	for key, value := range redacted {
		if strings.Contains(value, "secret") || strings.Contains(value, "hunter2") {
			t.Errorf("field %q still carries a credential: %q", key, value)
		}
	}

	// Non-sensitive fields must be preserved, or the form resets itself on every error and the
	// redaction gets reverted by whoever has to retype the host each time.
	kept := map[string]string{
		"mysql_host":     "db.internal",
		"mysql_user":     "app",
		"postgres_table": "articles",
		"question":       "who wrote this?",
	}
	for key, want := range kept {
		if got := redacted[key]; got != want {
			t.Errorf("field %q = %q, want %q", key, got, want)
		}
	}
}

func TestRedactSensitiveFieldsHandlesUnprefixedKeys(t *testing.T) {
	// Keys without a "<connector>_" prefix have no key part to match against, so they pass
	// through. Documented here so a future change to the prefix scheme trips this test rather
	// than silently starting to echo a bare "password" field.
	redacted := redactSensitiveFields(map[string]string{"connector": "sqlite", "question": "hi"})
	if redacted["connector"] != "sqlite" || redacted["question"] != "hi" {
		t.Errorf("unprefixed fields were altered: %v", redacted)
	}
}

func TestRedactSensitiveFieldsEmptyInput(t *testing.T) {
	if got := redactSensitiveFields(map[string]string{}); len(got) != 0 {
		t.Errorf("got %v, want an empty map", got)
	}
}

func TestPreferredDefault(t *testing.T) {
	names := []string{"mysql", "postgres", "sqlite"}
	if got := preferredDefault(names, "sqlite"); got != "sqlite" {
		t.Errorf("got %q, want %q", got, "sqlite")
	}
	// Falls back to the first available rather than selecting a backend that is not registered.
	if got := preferredDefault(names, "oracle"); got != "mysql" {
		t.Errorf("got %q, want %q", got, "mysql")
	}
	if got := preferredDefault(nil, "sqlite"); got != "" {
		t.Errorf("got %q, want an empty string", got)
	}
}
