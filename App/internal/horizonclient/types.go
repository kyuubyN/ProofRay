package horizonclient

import "horizonmemory/connector/internal/document"

// AnswerRequest is the body of POST /v1/answers -- see api/README.md.
//
// Documents uses the structured schema api/_engine_bridge.py validates, not the legacy []string
// form: the legacy form makes the server synthesize an identity per document ("doc:1", "doc:2",
// keyed to array position), which discards the primary key the connector actually read and makes
// an answer's provenance impossible to reopen against the source database.
type AnswerRequest struct {
	Question       string              `json:"question"`
	Documents      []document.Document `json:"documents"`
	IncludeSources bool                `json:"include_sources,omitempty"`
	Polish         bool                `json:"polish,omitempty"`
	PolishModel    string              `json:"polish_model,omitempty"`
}

// AnswerLine is one composed line of an answer, or one entry of the full verified pool when
// Sources is populated (IncludeSources: true).
type AnswerLine struct {
	Text           string  `json:"text"`
	Source         string  `json:"source"`
	RelevanceScore float64 `json:"relevance_score"`
}

// AnswerResponse mirrors the JSON shape returned by both POST /v1/answers and
// GET /v1/answers/{id}. State is "resolved" when an answer was composed, or the lowercased
// name of a router abstain state (e.g. "abstention") when the supplied documents did not verify
// -- Horizon fails closed rather than guessing, so a caller must check State before trusting
// Answer.
type AnswerResponse struct {
	ID                  string       `json:"id"`
	Object              string       `json:"object"`
	Created             int64        `json:"created"`
	State               string       `json:"state"`
	Answer              string       `json:"answer"`
	AnswerLines         []AnswerLine `json:"answer_lines"`
	DocumentsConsidered int          `json:"documents_considered"`
	VerifiedCandidates  int          `json:"verified_candidates"`
	AnswerBytes         int          `json:"answer_bytes"`
	Sources             []AnswerLine `json:"sources"`
	PolishedAnswer      *string      `json:"polished_answer"`
	PolishState         *string      `json:"polish_state"`
}

// HealthResponse is the body of GET /v1/health.
type HealthResponse struct {
	Status        string `json:"status"`
	EngineProfile string `json:"engine_profile"`
	Schema        string `json:"schema"`
}

// APIError is the {"error": {...}} shape api/server.py returns on 4xx responses.
type APIError struct {
	StatusCode int
	Message    string `json:"message"`
	Type       string `json:"type"`
}

func (e *APIError) Error() string {
	return e.Message
}
