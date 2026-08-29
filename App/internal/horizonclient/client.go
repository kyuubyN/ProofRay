// Package horizonclient is a thin HTTP client for HorizonAPI (api/server.py), the Flask surface
// over HorizonAnswerEngine. It knows nothing about databases -- a Connector hands it a
// []string, this package sends that to POST /v1/answers and parses the JSON back.
package horizonclient

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
	"unicode/utf8"

	"horizonmemory/connector/internal/document"
)

// ErrNoToken is returned instead of making the request when no bearer token could be resolved
// (see auth.go) -- distinct from an *APIError so a 401 that HorizonAPI itself rejected (bad or
// stale token) isn't confused with this client never having found one to send.
var ErrNoToken = errors.New(
	"horizonclient: no bearer token found (set PROOFRAY_API_TOKEN/HORIZON_API_TOKEN, or start " +
		"api/server.py at least once so it generates its credentials file)")

// MaxQuestionBytes mirrors MAX_QUESTION_BYTES in api/_engine_bridge.py.
const MaxQuestionBytes = 16 * 1024

// ErrInvalidQuestion reports a question the API would reject before running the engine.
var ErrInvalidQuestion = errors.New("question does not match the API contract")

// ErrInvalidBaseURL reports a HorizonAPI endpoint that cannot produce valid client requests.
var ErrInvalidBaseURL = errors.New("HorizonAPI base URL is invalid")

// ValidateQuestion is safe to call before opening a database connection, so an invalid question
// cannot trigger a full corpus fetch only to receive HTTP 400 afterward. The API strips outer
// whitespace before validating and measures UTF-8 bytes, so this mirrors that order.
func ValidateQuestion(question string) error {
	if !utf8.ValidString(question) {
		return fmt.Errorf("question is not valid UTF-8: %w", ErrInvalidQuestion)
	}
	trimmed := strings.TrimSpace(question)
	if trimmed == "" {
		return fmt.Errorf("question is required: %w", ErrInvalidQuestion)
	}
	if len(trimmed) > MaxQuestionBytes {
		return fmt.Errorf(
			"question is %d bytes after trimming, over the API's %d-byte limit: %w",
			len(trimmed), MaxQuestionBytes, ErrInvalidQuestion)
	}
	return nil
}

// ValidateBaseURL lets entrypoints reject configuration before fetching a corpus. Userinfo is
// disallowed because API authentication uses a bearer token and embedding a second credential in
// a URL makes it liable to appear in diagnostics.
func ValidateBaseURL(raw string) error {
	// ParseRequestURI treats a fragment marker in an absolute URI as part of the path on some
	// inputs, so it cannot enforce the no-fragment contract. Parse preserves Fragment and the
	// explicit scheme/host checks below still reject relative references.
	parsed, err := url.Parse(raw)
	if err != nil {
		return fmt.Errorf("base URL cannot be parsed: %w", ErrInvalidBaseURL)
	}
	if (parsed.Scheme != "http" && parsed.Scheme != "https") || parsed.Host == "" {
		return fmt.Errorf("base URL must be an absolute http(s) URL: %w", ErrInvalidBaseURL)
	}
	if parsed.User != nil || parsed.RawQuery != "" || parsed.ForceQuery || strings.Contains(raw, "#") {
		return fmt.Errorf("base URL must not contain userinfo, query, or fragment: %w", ErrInvalidBaseURL)
	}
	return nil
}

// Client talks to one running HorizonAPI instance (api/server.py, default
// http://127.0.0.1:8420).
type Client struct {
	baseURL    string
	httpClient *http.Client
}

// New builds a Client. Entrypoints validate baseURL with ValidateBaseURL; trailing slashes are
// normalized so both "http://127.0.0.1:8420" and its slash-suffixed form behave identically.
func New(baseURL string) *Client {
	return &Client{
		baseURL: strings.TrimRight(baseURL, "/"),
		httpClient: &http.Client{
			Timeout: 30 * time.Second,
		},
	}
}

// Health calls GET /v1/health.
func (c *Client) Health(ctx context.Context) (*HealthResponse, error) {
	var out HealthResponse
	if err := c.do(ctx, http.MethodGet, "/v1/health", nil, &out, ""); err != nil {
		return nil, err
	}
	return &out, nil
}

// CreateAnswer calls POST /v1/answers with the given request body.
//
// The payload is checked against the server's limits before anything is sent. Werkzeug rejects an
// oversized body with a bare 413 before api/server.py's handler runs, so without this the caller
// gets "413" with no indication of which limit was hit or by how much -- after the whole corpus
// has already been read out of the database and pushed over the network.
func (c *Client) CreateAnswer(ctx context.Context, req AnswerRequest) (*AnswerResponse, error) {
	// Authentication is a prerequisite for every answers route. Check it before payload
	// validation so a client with no credentials receives the actionable ErrNoToken regardless
	// of the request contents, while still doing all payload checks before any HTTP request.
	token, err := requiredToken()
	if err != nil {
		return nil, err
	}
	if err := checkRequestSize(req); err != nil {
		return nil, err
	}
	var out AnswerResponse
	if err := c.do(ctx, http.MethodPost, "/v1/answers", req, &out, token); err != nil {
		return nil, err
	}
	return &out, nil
}

// checkRequestSize measures everything in the body that is not a document, then hands the
// documents to document.CheckPayload with that envelope accounted for.
func checkRequestSize(req AnswerRequest) error {
	if err := ValidateQuestion(req.Question); err != nil {
		return fmt.Errorf("horizonclient: %w", err)
	}
	if req.Polish && req.PolishModel == "" {
		return fmt.Errorf("horizonclient: polish_model is required when polish is true")
	}
	envelope := req
	// Encode an empty array rather than nil/null, then remove those two bracket bytes. That gives
	// CheckPayload exactly the bytes outside the documents array; it adds the real brackets,
	// elements and n-1 commas back. Passing documents:null here would overcount by four bytes.
	envelope.Documents = []document.Document{}
	encoded, err := json.Marshal(envelope)
	if err != nil {
		return fmt.Errorf("horizonclient: encoding request: %w", err)
	}
	if err := document.CheckPayload(req.Documents, len(encoded)-len("[]")); err != nil {
		return fmt.Errorf("horizonclient: %w", err)
	}
	return nil
}

// GetAnswer calls GET /v1/answers/{id}, optionally requesting the full verified claim list.
func (c *Client) GetAnswer(ctx context.Context, id string, includeSources bool) (*AnswerResponse, error) {
	token, err := requiredToken()
	if err != nil {
		return nil, err
	}
	path := "/v1/answers/" + id
	if includeSources {
		path += "?include_sources=true"
	}
	var out AnswerResponse
	if err := c.do(ctx, http.MethodGet, path, nil, &out, token); err != nil {
		return nil, err
	}
	return &out, nil
}

func requiredToken() (string, error) {
	token := resolveToken()
	if token == "" {
		return "", ErrNoToken
	}
	return token, nil
}

func (c *Client) do(ctx context.Context, method, path string, body any, out any, token string) error {
	var reqBody io.Reader
	if body != nil {
		encoded, err := json.Marshal(body)
		if err != nil {
			return fmt.Errorf("horizonclient: encoding request: %w", err)
		}
		reqBody = bytes.NewReader(encoded)
	}

	req, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, reqBody)
	if err != nil {
		return fmt.Errorf("horizonclient: building request: %w", err)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	// GET /v1/health passes an empty token because it is the one route api/server.py leaves open;
	// every other public method resolves a token before calling do.
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("horizonclient: calling %s: %w", path, err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("horizonclient: reading response from %s: %w", path, err)
	}

	if resp.StatusCode >= 400 {
		var wrapped struct {
			Error APIError `json:"error"`
		}
		if err := json.Unmarshal(respBody, &wrapped); err != nil {
			return fmt.Errorf("horizonclient: %s returned %d: %s", path, resp.StatusCode, respBody)
		}
		wrapped.Error.StatusCode = resp.StatusCode
		return &wrapped.Error
	}

	if err := json.Unmarshal(respBody, out); err != nil {
		return fmt.Errorf("horizonclient: decoding response from %s: %w", path, err)
	}
	return nil
}
