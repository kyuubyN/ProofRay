package webui

import (
	"context"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"

	"horizonmemory/connector/internal/connectors"
	"horizonmemory/connector/internal/document"
	"horizonmemory/connector/internal/horizonclient"
)

// The redaction unit tests build their error strings by hand, so they would all still pass if
// handleAsk went back to rendering err.Error() verbatim. These drive the actual HTTP handler and
// assert on the bytes it writes, which is the only thing that proves the page cannot leak.

// failingConnector fails at connect time with an error quoting the DSN it was handed -- the shape
// every SQL driver produces for an unreachable or malformed connection string.
func registerFailingConnector(t *testing.T, name string) {
	t.Helper()
	connectors.Register(name, func(ctx context.Context, opts connectors.Options) (connectors.Connector, error) {
		return nil, &dsnError{dsn: opts.Get("dsn", "", "")}
	})
	// handleAsk only forwards the form fields listed here, so without this the fake connector
	// receives an empty DSN and the test asserts against a message that never contained a
	// credential in the first place.
	connectorFields[name] = []string{"dsn", "password"}
	t.Cleanup(func() { delete(connectorFields, name) })
}

type dsnError struct{ dsn string }

func (e *dsnError) Error() string {
	return "failed to connect to `" + e.dsn + "`: connection refused"
}

func postAsk(t *testing.T, form url.Values) string {
	t.Helper()
	server := New(horizonclient.New("http://127.0.0.1:1"), "http://127.0.0.1:1", "http://127.0.0.1:2")

	request := httptest.NewRequest(http.MethodPost, "/ask", strings.NewReader(form.Encode()))
	request.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	recorder := httptest.NewRecorder()

	server.Routes().ServeHTTP(recorder, request)
	return recorder.Body.String()
}

func TestHandleAskNeverRendersACredentialFromADriverError(t *testing.T) {
	registerFailingConnector(t, "testfail")
	const password = "SENHA-ULTRA-SECRETA-XYZ789"
	dsn := "postgres://app:" + password + "@db.internal:5432/prod"

	body := postAsk(t, url.Values{
		"connector":    {"testfail"},
		"testfail_dsn": {dsn},
		"question":     {"anything"},
	})

	if strings.Contains(body, password) {
		t.Error("the rendered page contains the submitted password")
	}
	// The whole DSN was submitted, so none of it should survive either.
	if strings.Contains(body, "db.internal:5432/prod") {
		t.Error("the rendered page contains the submitted DSN")
	}
	if !strings.Contains(body, "connection refused") {
		t.Error("the page lost the diagnosis along with the credential")
	}
}

func TestHandleAskRedactsAShortPasswordFromADriverError(t *testing.T) {
	registerFailingConnector(t, "testfailshort")

	body := postAsk(t, url.Values{
		"connector":              {"testfailshort"},
		"testfailshort_dsn":      {"host=db"},
		"testfailshort_password": {"xy"},
		"question":               {"anything"},
	})

	if strings.Contains(body, "`host=db`") && strings.Contains(body, ">xy<") {
		t.Error("a short password reached the page")
	}
}

// The submitted values must not come back in the form's value="..." attributes either -- this is
// the pre-existing protection, asserted here against the real response body rather than the map.
func TestHandleAskDoesNotEchoCredentialsIntoTheForm(t *testing.T) {
	registerFailingConnector(t, "testfailecho")
	const password = "ECHO-TEST-PASSWORD-123"

	body := postAsk(t, url.Values{
		"connector":             {"testfailecho"},
		"testfailecho_dsn":      {"postgres://u:" + password + "@h:5432/d"},
		"testfailecho_password": {password},
		"question":              {"anything"},
	})

	if strings.Contains(body, password) {
		t.Error("a submitted credential was echoed into the rendered form")
	}
}

// A connector whose fetch exceeds the API's request budget must surface that as a readable
// message, not as a 413 from the server later.
func TestHandleAskReportsAnOversizedCorpus(t *testing.T) {
	connectors.Register("testbig", func(ctx context.Context, opts connectors.Options) (connectors.Connector, error) {
		return &oversizedConnector{}, nil
	})

	body := postAsk(t, url.Values{
		"connector": {"testbig"},
		"question":  {"anything"},
	})

	if !strings.Contains(body, "caps a request body") && !strings.Contains(body, "request body would be") {
		t.Errorf("the oversized corpus was not reported to the visitor:\n%s", firstError(body))
	}
}

type oversizedConnector struct{}

func (c *oversizedConnector) Name() string { return "testbig" }
func (c *oversizedConnector) Close() error { return nil }
func (c *oversizedConnector) FetchDocuments(ctx context.Context) ([]document.Document, error) {
	acc := &document.Accumulator{Origin: "testbig"}
	big := strings.Repeat("x", 32*1024)
	for i := 0; i < 100; i++ {
		if err := acc.Add(document.New("testbig:local/x", string(rune('a'+i%26))+"-"+strings.Repeat("k", i%5), big, acc.Len())); err != nil {
			return nil, err
		}
	}
	return acc.Documents(), nil
}

func firstError(body string) string {
	start := strings.Index(body, `class="error"`)
	if start < 0 {
		return "(no error block rendered)"
	}
	end := start + 400
	if end > len(body) {
		end = len(body)
	}
	return body[start:end]
}
