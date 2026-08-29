package horizonclient

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"

	"horizonmemory/connector/internal/document"
)

// isolateCredentials points token resolution at an empty temp dir, so a test never picks up the
// developer's real ~/.config/proofray/api_credentials.json and never depends on whether the API
// has been run on this machine.
func isolateCredentials(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("PROOFRAY_API_TOKEN", "")
	t.Setenv("HORIZON_API_TOKEN", "")
	t.Setenv("HORIZON_API_CREDENTIALS_PATH", "")
	t.Setenv("PROOFRAY_API_CREDENTIALS_PATH", filepath.Join(dir, "api_credentials.json"))
	return dir
}

func TestResolveTokenPrefersEnvironment(t *testing.T) {
	isolateCredentials(t)
	t.Setenv("PROOFRAY_API_TOKEN", "from-proofray-env")
	t.Setenv("HORIZON_API_TOKEN", "from-legacy-env")

	if got := resolveToken(); got != "from-proofray-env" {
		t.Errorf("got %q, want the PROOFRAY_API_TOKEN value", got)
	}
}

func TestResolveTokenFallsBackToLegacyEnvVar(t *testing.T) {
	isolateCredentials(t)
	t.Setenv("HORIZON_API_TOKEN", "from-legacy-env")

	if got := resolveToken(); got != "from-legacy-env" {
		t.Errorf("got %q, want the HORIZON_API_TOKEN value", got)
	}
}

func TestResolveTokenReadsCredentialsFile(t *testing.T) {
	dir := isolateCredentials(t)
	path := filepath.Join(dir, "api_credentials.json")
	contents := `{"token": "from-file", "machine_fingerprint": "abc"}`
	if err := os.WriteFile(path, []byte(contents), 0o600); err != nil {
		t.Fatal(err)
	}

	if got := resolveToken(); got != "from-file" {
		t.Errorf("got %q, want %q", got, "from-file")
	}
}

func TestResolveTokenEmptyWhenNothingAvailable(t *testing.T) {
	isolateCredentials(t)

	// No env var, no file: api/server.py has simply never run on this machine. That is not an
	// error to guess around -- it becomes ErrNoToken at request time (see TestDoRequiresToken).
	if got := resolveToken(); got != "" {
		t.Errorf("got %q, want an empty string", got)
	}
}

func TestResolveTokenIgnoresMalformedFile(t *testing.T) {
	dir := isolateCredentials(t)
	path := filepath.Join(dir, "api_credentials.json")
	if err := os.WriteFile(path, []byte("not json at all"), 0o600); err != nil {
		t.Fatal(err)
	}

	if got := resolveToken(); got != "" {
		t.Errorf("got %q, want an empty string for an unparseable file", got)
	}
}

// Every route except GET /v1/health requires the bearer token; health stays open so a monitoring
// probe works before the operator has any credentials.
func TestHealthSendsNoAuthorizationHeader(t *testing.T) {
	isolateCredentials(t)
	t.Setenv("PROOFRAY_API_TOKEN", "should-not-be-sent")

	var gotAuth string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth = r.Header.Get("Authorization")
		json.NewEncoder(w).Encode(HealthResponse{Status: "ok"})
	}))
	defer server.Close()

	if _, err := New(server.URL).Health(context.Background()); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if gotAuth != "" {
		t.Errorf("GET /v1/health sent Authorization: %q, want none", gotAuth)
	}
}

func TestCreateAnswerSendsBearerToken(t *testing.T) {
	isolateCredentials(t)
	t.Setenv("PROOFRAY_API_TOKEN", "tok-123")

	var gotAuth string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth = r.Header.Get("Authorization")
		json.NewEncoder(w).Encode(AnswerResponse{ID: "a1", State: "resolved"})
	}))
	defer server.Close()

	client := New(server.URL)
	if _, err := client.CreateAnswer(context.Background(), AnswerRequest{
		Question:  "who wrote this?",
		Documents: []document.Document{document.New("postgres:articles", "42", "a document", 0)},
	}); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if gotAuth != "Bearer tok-123" {
		t.Errorf("got Authorization %q, want %q", gotAuth, "Bearer tok-123")
	}
}

// Without a token the client must fail before making the request, so "this client never found a
// token" stays distinguishable from "the server rejected the token we sent".
func TestDoRequiresToken(t *testing.T) {
	isolateCredentials(t)

	var reached bool
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reached = true
	}))
	defer server.Close()

	// Deliberately omit documents: authentication is checked before payload validation, so a
	// missing credential remains the primary actionable error for every protected route.
	_, err := New(server.URL).CreateAnswer(context.Background(), AnswerRequest{Question: "q"})
	if !errors.Is(err, ErrNoToken) {
		t.Errorf("got %v, want ErrNoToken", err)
	}
	if reached {
		t.Error("the request was sent despite no token being available")
	}
}

// An error path must not echo the token back to the caller -- that string ends up in the web UI's
// ErrorMessage, rendered into the page.
func TestAPIErrorDoesNotLeakToken(t *testing.T) {
	isolateCredentials(t)
	const token = "super-secret-token-value"
	t.Setenv("PROOFRAY_API_TOKEN", token)

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusUnauthorized)
		w.Write([]byte(`{"error": {"message": "unauthorized", "type": "auth_error"}}`))
	}))
	defer server.Close()

	_, err := New(server.URL).CreateAnswer(context.Background(), validAnswerRequest())
	if err == nil {
		t.Fatal("expected an error")
	}
	if strings.Contains(err.Error(), token) {
		t.Errorf("error message leaked the bearer token: %q", err)
	}

	var apiErr *APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("got %T, want *APIError", err)
	}
	if apiErr.StatusCode != http.StatusUnauthorized {
		t.Errorf("got status %d, want 401", apiErr.StatusCode)
	}
}

func TestNonJSONErrorBodyIsReported(t *testing.T) {
	isolateCredentials(t)
	t.Setenv("PROOFRAY_API_TOKEN", "tok")

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		w.Write([]byte("<html>proxy error</html>"))
	}))
	defer server.Close()

	_, err := New(server.URL).CreateAnswer(context.Background(), validAnswerRequest())
	if err == nil {
		t.Fatal("expected an error")
	}
	if !strings.Contains(err.Error(), "500") {
		t.Errorf("error %q does not mention the status code", err)
	}
}

// Werkzeug rejects an oversized body with a bare 413 before api/server.py's handler runs, so
// without a preflight the caller learns nothing about which limit was hit -- after the whole
// corpus has already been read out of the database and pushed over the network.
func TestCreateAnswerRejectsAnOversizedPayloadWithoutSending(t *testing.T) {
	isolateCredentials(t)
	t.Setenv("PROOFRAY_API_TOKEN", "tok")

	var reached bool
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reached = true
	}))
	defer server.Close()

	big := strings.Repeat("x", 32*1024)
	var docs []document.Document
	for i := 0; i < 64; i++ { // ~2 MiB, over the API's 1 MiB cap
		docs = append(docs, document.New("src", strconv.Itoa(i), big, i))
	}

	_, err := New(server.URL).CreateAnswer(context.Background(), AnswerRequest{
		Question: "q", Documents: docs,
	})
	if !errors.Is(err, document.ErrCorpusTooLarge) {
		t.Errorf("got %v, want ErrCorpusTooLarge", err)
	}
	if reached {
		t.Error("the oversized request was sent instead of being rejected locally")
	}
}

func TestValidateQuestionMirrorsTheAPIByteLimit(t *testing.T) {
	if err := ValidateQuestion(strings.Repeat("x", MaxQuestionBytes)); err != nil {
		t.Errorf("exact boundary was rejected: %v", err)
	}
	if err := ValidateQuestion(strings.Repeat("x", MaxQuestionBytes+1)); !errors.Is(err, ErrInvalidQuestion) {
		t.Errorf("one byte over: got %v, want ErrInvalidQuestion", err)
	}
	// Multibyte text is measured in bytes, not runes.
	if err := ValidateQuestion(strings.Repeat("é", MaxQuestionBytes/2+1)); !errors.Is(err, ErrInvalidQuestion) {
		t.Errorf("multibyte overflow: got %v, want ErrInvalidQuestion", err)
	}
}

func TestValidateQuestionRejectsWhitespaceAndInvalidUTF8(t *testing.T) {
	for _, question := range []string{" \t\n", string([]byte{'q', 0xff})} {
		if err := ValidateQuestion(question); !errors.Is(err, ErrInvalidQuestion) {
			t.Errorf("question %q: got %v, want ErrInvalidQuestion", question, err)
		}
	}
}

func TestValidateBaseURL(t *testing.T) {
	for _, valid := range []string{"http://127.0.0.1:8420", "https://api.internal/proofray/"} {
		if err := ValidateBaseURL(valid); err != nil {
			t.Errorf("valid URL %q rejected: %v", valid, err)
		}
	}
	for _, invalid := range []string{
		"", "localhost:8420", "ftp://api.internal", "http://user:pass@api.internal",
		"http://api.internal?token=secret", "http://api.internal?", "http://api.internal/#fragment",
		"http://api.internal#",
	} {
		if err := ValidateBaseURL(invalid); !errors.Is(err, ErrInvalidBaseURL) {
			t.Errorf("invalid URL %q: got %v, want ErrInvalidBaseURL", invalid, err)
		}
	}
}

func TestValidateBaseURLErrorDoesNotEchoUserinfo(t *testing.T) {
	const raw = "http://user:super-secret-password@api.internal"
	err := ValidateBaseURL(raw)
	if err == nil {
		t.Fatal("URL containing userinfo was accepted")
	}
	if strings.Contains(err.Error(), "user:") || strings.Contains(err.Error(), "super-secret-password") {
		t.Errorf("validation error leaked URL userinfo: %v", err)
	}
}

func validAnswerRequest() AnswerRequest {
	return AnswerRequest{
		Question:  "q",
		Documents: []document.Document{document.New("src", "1", "text", 0)},
	}
}

func TestNewNormalizesTrailingSlashes(t *testing.T) {
	client := New("http://127.0.0.1:8420///")
	if client.baseURL != "http://127.0.0.1:8420" {
		t.Errorf("baseURL = %q, want trailing slashes removed", client.baseURL)
	}
}

func TestCheckRequestSizeRejectsIncompletePolishRequest(t *testing.T) {
	req := AnswerRequest{
		Question:  "q",
		Documents: []document.Document{document.New("src", "1", "text", 0)},
		Polish:    true,
	}
	if err := checkRequestSize(req); err == nil || !strings.Contains(err.Error(), "polish_model") {
		t.Errorf("got %v, want a missing polish_model error", err)
	}
}

func TestCreateAnswerRejectsTooManyDocumentsWithoutSending(t *testing.T) {
	isolateCredentials(t)
	t.Setenv("PROOFRAY_API_TOKEN", "tok")

	var reached bool
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reached = true
	}))
	defer server.Close()

	docs := make([]document.Document, document.MaxDocuments+1)
	for i := range docs {
		docs[i] = document.New("src", strconv.Itoa(i), "t", i)
	}

	_, err := New(server.URL).CreateAnswer(context.Background(), AnswerRequest{
		Question: "q", Documents: docs,
	})
	if !errors.Is(err, document.ErrCorpusTooLarge) {
		t.Errorf("got %v, want ErrCorpusTooLarge", err)
	}
	if reached {
		t.Error("the over-count request was sent instead of being rejected locally")
	}
}

// The preflight must not reject a corpus that would actually fit.
func TestCreateAnswerSendsANormalPayload(t *testing.T) {
	isolateCredentials(t)
	t.Setenv("PROOFRAY_API_TOKEN", "tok")

	var reached bool
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reached = true
		json.NewEncoder(w).Encode(AnswerResponse{ID: "a1", State: "resolved"})
	}))
	defer server.Close()

	var docs []document.Document
	for i := 0; i < 200; i++ {
		docs = append(docs, document.New("src", strconv.Itoa(i), "a normal sized document", i))
	}

	if _, err := New(server.URL).CreateAnswer(context.Background(), AnswerRequest{
		Question: "q", Documents: docs,
	}); err != nil {
		t.Fatalf("a sendable payload was rejected: %v", err)
	}
	if !reached {
		t.Error("the request never reached the server")
	}
}

// This pins the preflight to the exact bytes encoding/json sends. The request at the limit must
// pass and the same request one byte larger must fail; an approximation (including the old
// documents:null envelope) fails the first assertion by four bytes.
func TestCheckRequestSizeMatchesTheRealJSONAtTheExactBoundary(t *testing.T) {
	docs := make([]document.Document, 16)
	for i := 0; i < len(docs)-1; i++ {
		docs[i] = document.New("src", strconv.Itoa(i), strings.Repeat("x", document.MaxDocumentBytes), i)
	}
	docs[len(docs)-1] = document.New("src", "last", "", len(docs)-1)
	req := AnswerRequest{Question: "q", Documents: docs}

	baseline, err := json.Marshal(req)
	if err != nil {
		t.Fatal(err)
	}
	remaining := document.MaxRequestBytes - len(baseline)
	if remaining <= 0 || remaining > document.MaxDocumentBytes {
		t.Fatalf("boundary fixture needs %d bytes in the final document", remaining)
	}
	req.Documents[len(req.Documents)-1] = document.New(
		"src", "last", strings.Repeat("x", remaining), len(req.Documents)-1)

	exact, err := json.Marshal(req)
	if err != nil {
		t.Fatal(err)
	}
	if len(exact) != document.MaxRequestBytes {
		t.Fatalf("fixture encoded to %d bytes, want exactly %d", len(exact), document.MaxRequestBytes)
	}
	if err := checkRequestSize(req); err != nil {
		t.Errorf("exactly-at-limit request was rejected: %v", err)
	}

	req.Documents[len(req.Documents)-1] = document.New(
		"src", "last", strings.Repeat("x", remaining+1), len(req.Documents)-1)
	over, err := json.Marshal(req)
	if err != nil {
		t.Fatal(err)
	}
	if len(over) != document.MaxRequestBytes+1 {
		t.Fatalf("oversized fixture encoded to %d bytes, want %d", len(over), document.MaxRequestBytes+1)
	}
	if err := checkRequestSize(req); !errors.Is(err, document.ErrCorpusTooLarge) {
		t.Errorf("got %v, want ErrCorpusTooLarge", err)
	}
}
