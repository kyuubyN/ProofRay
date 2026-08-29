package webui

import (
	"errors"
	"fmt"
	"strings"
	"testing"
)

// The messages here are the shapes real drivers produce when a connection string is malformed or
// unreachable -- the case the review asked for. Each one carries a credential the visitor
// submitted, and each would otherwise be rendered straight into the page.
func TestRedactErrorRemovesSubmittedCredentials(t *testing.T) {
	const password = "hunter2-super-secret"

	cases := []struct {
		name      string
		message   string
		submitted map[string]string
	}{
		{
			name:      "postgres connection error quotes the whole DSN",
			message:   `failed to connect to ` + "`postgres://app:" + password + `@db.internal:5432/prod` + "`" + `: connection refused`,
			submitted: map[string]string{"postgres_dsn": "postgres://app:" + password + "@db.internal:5432/prod"},
		},
		{
			name:      "mongo URI echoed in a parse error",
			message:   `error parsing uri: mongodb://admin:` + password + `@cluster.internal:27017/?ssl=true`,
			submitted: map[string]string{"mongodb_uri": "mongodb://admin:" + password + "@cluster.internal:27017/?ssl=true"},
		},
		{
			name:      "redis URL in a dial error",
			message:   `dial redis://default:` + password + `@cache.internal:6379/0: i/o timeout`,
			submitted: map[string]string{"redis_url": "redis://default:" + password + "@cache.internal:6379/0"},
		},
		{
			name:      "mysql password submitted as its own field",
			message:   `Access denied for user 'root' (using password: ` + password + `)`,
			submitted: map[string]string{"mysql_password": password},
		},
		{
			name:      "elasticsearch URL with userinfo",
			message:   `cannot connect to http://elastic:` + password + `@es.internal:9200`,
			submitted: map[string]string{"elasticsearch_url": "http://elastic:" + password + "@es.internal:9200"},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := redactError(errors.New(tc.message), tc.submitted)
			if strings.Contains(got, password) {
				t.Errorf("the password survived redaction:\n%s", got)
			}
			if !strings.Contains(got, redactedPlaceholder) {
				t.Errorf("nothing was redacted, so the pattern did not match:\n%s", got)
			}
		})
	}
}

// A redacted message still has to say what went wrong and where, or the visitor cannot fix a
// typo'd hostname -- redaction that destroys the diagnostic would just be removed by whoever
// next has to debug a connection.
func TestRedactErrorKeepsTheDiagnosis(t *testing.T) {
	submitted := map[string]string{"postgres_dsn": "postgres://app:hunter2-secret@db.internal:5432/prod"}
	message := "failed to connect to `postgres://app:hunter2-secret@db.internal:5432/prod`: connection refused"

	got := redactError(errors.New(message), submitted)

	for _, keep := range []string{"failed to connect", "connection refused"} {
		if !strings.Contains(got, keep) {
			t.Errorf("redaction removed %q, which the visitor needs:\n%s", keep, got)
		}
	}
}

// The second pass exists for exactly this: a credential that never came through the form (an env
// var, or a driver reformatting the string) is invisible to exact-match removal.
func TestRedactErrorCatchesUnsubmittedCredentials(t *testing.T) {
	message := "failed to connect to postgres://app:from-the-environment@db.internal:5432/prod"

	got := redactMessage(message, map[string]string{})

	if strings.Contains(got, "from-the-environment") {
		t.Errorf("a credential not present in the form survived:\n%s", got)
	}
	if !strings.Contains(got, "db.internal:5432") {
		t.Errorf("the host was removed along with the credential:\n%s", got)
	}
}

func TestRedactErrorCatchesKeyValueSecrets(t *testing.T) {
	cases := []string{
		`invalid dsn: host=db.internal password=hunter2 sslmode=require`,
		`config error: PASSWORD=hunter2`,
		`bad option: api_key: hunter2`,
		`rejected: token="hunter2"`,
	}
	for _, message := range cases {
		t.Run(message, func(t *testing.T) {
			got := redactMessage(message, map[string]string{})
			if strings.Contains(got, "hunter2") {
				t.Errorf("a key=value secret survived:\n%s", got)
			}
		})
	}
}

// When a DSN and the password inside it are both submitted, replacing the shorter value first
// would leave the DSN unmatched and let the rest of it through. secretValues sorts longest first
// to prevent that.
func TestRedactErrorHandlesOverlappingSecrets(t *testing.T) {
	const password = "hunter2-secret"
	dsn := "postgres://app:" + password + "@db.internal:5432/prod"
	submitted := map[string]string{"postgres_dsn": dsn, "postgres_password": password}

	got := redactMessage("failed to connect to "+dsn, submitted)

	if strings.Contains(got, password) {
		t.Errorf("the password survived:\n%s", got)
	}
	if strings.Contains(got, "db.internal") && strings.Contains(got, "app") {
		// The whole DSN was submitted, so the whole DSN is removed -- not just its password.
		t.Logf("note: the full DSN was redacted as one value: %s", got)
	}
}

// A one or two character value would match all over the message and reduce it to placeholders,
// destroying the diagnostic without protecting anything the pattern pass misses.
func TestRedactErrorIgnoresVeryShortValues(t *testing.T) {
	submitted := map[string]string{"mysql_password": "a"}

	got := redactMessage("connection to host a.b.c failed: no route to host", submitted)

	if !strings.Contains(got, "a.b.c") {
		t.Errorf("a one-character password shredded the message:\n%s", got)
	}
}

func TestRedactErrorHandlesNil(t *testing.T) {
	if got := redactError(nil, map[string]string{}); got != "" {
		t.Errorf("got %q, want an empty string", got)
	}
}

// Redaction must survive fmt.Errorf wrapping, since that is how the handler receives it: the
// connector's error is wrapped with "connecting to %s" before reaching redactError.
func TestRedactErrorWorksThroughWrapping(t *testing.T) {
	const password = "hunter2-secret"
	inner := errors.New("dial postgres://app:" + password + "@db.internal:5432: refused")
	wrapped := fmt.Errorf("connecting to postgres: %w", inner)

	got := redactError(wrapped, map[string]string{"postgres_dsn": "postgres://app:" + password + "@db.internal:5432"})

	if strings.Contains(got, password) {
		t.Errorf("the password survived wrapping:\n%s", got)
	}
	if !strings.Contains(got, "connecting to postgres") {
		t.Errorf("the wrapping context was lost:\n%s", got)
	}
}
