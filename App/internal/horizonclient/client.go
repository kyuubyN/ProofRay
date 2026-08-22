// Package horizonclient is a thin HTTP client for HorizonAPI (api/server.py), the Flask surface
// over HorizonAnswerEngine. It knows nothing about databases -- a Connector hands it a
// []string, this package sends that to POST /v1/answers and parses the JSON back.
package horizonclient

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

// Client talks to one running HorizonAPI instance (api/server.py, default
// http://127.0.0.1:8420).
type Client struct {
	baseURL    string
	httpClient *http.Client
}

// New builds a Client. baseURL should not have a trailing slash (e.g.
// "http://127.0.0.1:8420").
func New(baseURL string) *Client {
	return &Client{
		baseURL: baseURL,
		httpClient: &http.Client{
			Timeout: 30 * time.Second,
		},
	}
}

// Health calls GET /v1/health.
func (c *Client) Health(ctx context.Context) (*HealthResponse, error) {
	var out HealthResponse
	if err := c.do(ctx, http.MethodGet, "/v1/health", nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// CreateAnswer calls POST /v1/answers with the given request body.
func (c *Client) CreateAnswer(ctx context.Context, req AnswerRequest) (*AnswerResponse, error) {
	var out AnswerResponse
	if err := c.do(ctx, http.MethodPost, "/v1/answers", req, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

// GetAnswer calls GET /v1/answers/{id}, optionally requesting the full verified claim list.
func (c *Client) GetAnswer(ctx context.Context, id string, includeSources bool) (*AnswerResponse, error) {
	path := "/v1/answers/" + id
	if includeSources {
		path += "?include_sources=true"
	}
	var out AnswerResponse
	if err := c.do(ctx, http.MethodGet, path, nil, &out); err != nil {
		return nil, err
	}
	return &out, nil
}

func (c *Client) do(ctx context.Context, method, path string, body any, out any) error {
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
