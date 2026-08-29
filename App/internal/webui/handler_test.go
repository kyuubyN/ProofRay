package webui

import (
	"context"
	"errors"
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

// registerFailingConnector temporarily replaces a real connector name. Using a real name matters:
// its inputs exist in the production template, so the tests exercise both the error panel and the
// value="..." attributes that a re-render can leak through.
func registerFailingConnector(t *testing.T, name string, message func(connectors.Options) string) {
	t.Helper()
	cleanup := connectors.Register(name, func(ctx context.Context, opts connectors.Options) (connectors.Connector, error) {
		return nil, &driverError{message: message(opts)}
	})
	t.Cleanup(cleanup)
}

func TestRegisterFailingConnectorCleansTheGlobalRegistry(t *testing.T) {
	const name = "handler-cleanup-test"
	if _, exists := connectors.Get(name); exists {
		t.Fatalf("%q was already registered before the test", name)
	}

	t.Run("temporary registration", func(t *testing.T) {
		registerFailingConnector(t, name, func(connectors.Options) string { return "failure" })
		if _, exists := connectors.Get(name); !exists {
			t.Fatal("temporary factory was not registered")
		}
	})

	if _, exists := connectors.Get(name); exists {
		t.Errorf("%q leaked from the completed subtest", name)
	}
}

type driverError struct{ message string }

func (e *driverError) Error() string { return e.message }

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
	registerFailingConnector(t, "postgres", func(opts connectors.Options) string {
		return "failed to connect to `" + opts["dsn"] + "`: connection refused"
	})
	const password = "SENHA-ULTRA-SECRETA-XYZ789"
	dsn := "postgres://app:" + password + "@db.internal:5432/prod"

	body := postAsk(t, url.Values{
		"connector":    {"postgres"},
		"postgres_dsn": {dsn},
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
	registerFailingConnector(t, "mysql", func(opts connectors.Options) string {
		return `authentication failed: supplied credential "` + opts["password"] + `" was rejected`
	})

	body := postAsk(t, url.Values{
		"connector":      {"mysql"},
		"mysql_password": {"xy"},
		"question":       {"anything"},
	})

	panel := firstError(body)
	if strings.Contains(panel, "xy") {
		t.Errorf("a short password reached the error panel:\n%s", panel)
	}
	if !strings.Contains(panel, "was rejected") {
		t.Errorf("the fake driver error did not reach the error panel:\n%s", panel)
	}
}

// The submitted values must not come back in the form's value="..." attributes either -- this is
// the pre-existing protection, asserted here against the real response body rather than the map.
func TestHandleAskDoesNotEchoCredentialsIntoTheForm(t *testing.T) {
	registerFailingConnector(t, "postgres", func(opts connectors.Options) string {
		return "connection refused"
	})
	const password = "ECHO-TEST-PASSWORD-123"
	dsn := "postgres://u:" + password + "@h:5432/d"

	body := postAsk(t, url.Values{
		"connector":    {"postgres"},
		"postgres_dsn": {dsn},
		"question":     {"anything"},
	})

	if !strings.Contains(body, `name="postgres_dsn"`) {
		t.Fatal("the production DSN input is absent, so this test cannot exercise form re-rendering")
	}
	if strings.Contains(body, password) || strings.Contains(body, dsn) {
		t.Error("a submitted DSN was echoed into the rendered form")
	}
}

func TestHandleAskDoesNotEchoDynamoDBEndpointCredentials(t *testing.T) {
	registerFailingConnector(t, "dynamodb", func(opts connectors.Options) string {
		return "failed to connect to " + opts["endpoint"]
	})
	const endpoint = "http://local-user:local-pass@localhost:8000"

	body := postAsk(t, url.Values{
		"connector":         {"dynamodb"},
		"dynamodb_region":   {"us-east-1"},
		"dynamodb_endpoint": {endpoint},
		"question":          {"anything"},
	})

	if !strings.Contains(body, `name="dynamodb_endpoint"`) {
		t.Fatal("the production endpoint input is absent, so this test cannot exercise form re-rendering")
	}
	if strings.Contains(body, "local-user") || strings.Contains(body, "local-pass") || strings.Contains(body, endpoint) {
		t.Error("DynamoDB endpoint credentials reached the rendered page")
	}
}

// A connector whose fetch exceeds the API's request budget must surface that as a readable
// message, not as a 413 from the server later.
func TestHandleAskReportsAnOversizedCorpus(t *testing.T) {
	t.Cleanup(connectors.Register("testbig", func(ctx context.Context, opts connectors.Options) (connectors.Connector, error) {
		return &oversizedConnector{}, nil
	}))

	body := postAsk(t, url.Values{
		"connector": {"testbig"},
		"question":  {"anything"},
	})

	if !strings.Contains(body, "caps a request body") && !strings.Contains(body, "request body would be") {
		t.Errorf("the oversized corpus was not reported to the visitor:\n%s", firstError(body))
	}
}

func TestHandleAskRejectsAnOversizedQuestionBeforeConnecting(t *testing.T) {
	const name = "question-preflight-test"
	var factoryCalled bool
	t.Cleanup(connectors.Register(name, func(context.Context, connectors.Options) (connectors.Connector, error) {
		factoryCalled = true
		return nil, errors.New("factory must not run")
	}))

	body := postAsk(t, url.Values{
		"connector": {name},
		"question":  {strings.Repeat("x", horizonclient.MaxQuestionBytes+1)},
	})

	if factoryCalled {
		t.Error("database factory ran before the question limit was checked")
	}
	if !strings.Contains(body, "16384-byte limit") {
		t.Errorf("question limit was not reported to the visitor:\n%s", firstError(body))
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
	start := strings.Index(body, `class="error-panel"`)
	if start < 0 {
		return "(no error block rendered)"
	}
	end := start + 400
	if end > len(body) {
		end = len(body)
	}
	return body[start:end]
}

func TestFirstErrorFindsTheProductionErrorPanel(t *testing.T) {
	body := `<main><div class="error-panel">driver diagnosis</div></main>`
	if got := firstError(body); !strings.Contains(got, "driver diagnosis") {
		t.Errorf("firstError did not find the production error panel: %s", got)
	}
}
