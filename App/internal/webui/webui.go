// Package webui is a small server-rendered form over the same Connector registry
// cmd/horizon-connect uses from the command line: pick a database, type a question, get back
// whatever HorizonAPI (api/server.py) resolves. No JavaScript framework, no build step -- one
// embedded html/template, one POST handler. Documents are fetched fresh on every request; nothing
// is cached or persisted here.
package webui

import (
	"context"
	"embed"
	"fmt"
	"html/template"
	"log"
	"net/http"
	"sort"
	"strings"
	"time"

	"horizonmemory/connector/internal/connectors"
	"horizonmemory/connector/internal/horizonclient"
)

//go:embed templates/index.html.tmpl
var templatesFS embed.FS

var tmpl = template.Must(template.ParseFS(templatesFS, "templates/index.html.tmpl"))

// maxFormBytes caps the POST /ask request body, the same defensive reasoning
// api/server.py's MAX_CONTENT_LENGTH follows: no auth here yet, so bound what an anonymous
// caller can make this process process per request.
const maxFormBytes = 1 << 20 // 1 MiB

// requestTimeout bounds how long one connector fetch + HorizonAPI call may take, so a hung
// database connection can't pin an HTTP handler goroutine forever.
const requestTimeout = 60 * time.Second

// connectorFields lists, per registered backend, the Options keys its factory reads (see each
// connector's New for the exact keys). The template's per-connector <div> block names its
// inputs "<connector>_<key>" (e.g. "mysql_host") to keep every backend's fields uniquely named
// in the one shared <form> -- so a value typed into a hidden field never collides with the
// field actually submitted for the selected connector.
var connectorFields = map[string][]string{
	"postgres":      {"dsn", "table"},
	"sqlite":        {"path", "table"},
	"mysql":         {"host", "port", "user", "password", "database", "table"},
	"mongodb":       {"uri", "database", "collection"},
	"redis":         {"url", "prefix"},
	"dynamodb":      {"table", "region", "endpoint"},
	"elasticsearch": {"url", "index"},
}

type viewData struct {
	APIBaseURL     string
	BenchmarkURL   string
	Connectors     []string
	Selected       string
	Question       string
	IncludeSources bool
	// Fields holds every submitted "<connector>_<key>" form value, so the form can redisplay
	// whatever was typed after a round trip regardless of which connector ends up selected.
	Fields       map[string]string
	Result       *viewResult
	ErrorMessage string
}

// Field returns the value of "<connector>_<key>" for use in the template, e.g.
// {{.Field "mysql" "host"}}.
func (v viewData) Field(connector, key string) string {
	return v.Fields[connector+"_"+key]
}

type viewResult struct {
	State               string
	Answer              string
	DocumentsConsidered int
	VerifiedCandidates  int
	AnswerBytes         int
	Sources             []horizonclient.AnswerLine
}

// Server renders the form and handles submissions against one HorizonAPI instance.
type Server struct {
	client       *horizonclient.Client
	apiBaseURL   string
	benchmarkURL string
}

// New builds a Server. apiBaseURL is shown in the page footer and is otherwise only used by
// client, which must already point at it. benchmarkURL is where the "Benchmark Demo" nav button
// links -- the website/ Flask demo -- so the two standalone HorizonMemory front ends can jump
// between each other without being merged into one process.
func New(client *horizonclient.Client, apiBaseURL, benchmarkURL string) *Server {
	return &Server{client: client, apiBaseURL: apiBaseURL, benchmarkURL: benchmarkURL}
}

// Routes returns the handler to pass to http.ListenAndServe.
func (s *Server) Routes() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /{$}", s.handleIndex)
	mux.HandleFunc("POST /ask", s.handleAsk)
	return mux
}

func (s *Server) handleIndex(w http.ResponseWriter, r *http.Request) {
	names := registeredConnectorNames()
	s.render(w, viewData{
		APIBaseURL:   s.apiBaseURL,
		BenchmarkURL: s.benchmarkURL,
		Connectors:   names,
		Selected:     preferredDefault(names, "sqlite"),
		Fields:       map[string]string{},
	})
}

func (s *Server) handleAsk(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, maxFormBytes)
	if err := r.ParseForm(); err != nil {
		http.Error(w, "request body too large or malformed", http.StatusBadRequest)
		return
	}

	// raw keeps every submitted value, including secrets, only long enough to build this one
	// request's Options -- never stored in viewData, never reaches the template.
	raw := make(map[string]string, len(r.Form))
	for key := range r.Form {
		raw[key] = r.FormValue(key)
	}

	data := viewData{
		APIBaseURL:     s.apiBaseURL,
		BenchmarkURL:   s.benchmarkURL,
		Connectors:     registeredConnectorNames(),
		Selected:       r.FormValue("connector"),
		Question:       r.FormValue("question"),
		IncludeSources: r.FormValue("include_sources") == "1",
		Fields:         redactSensitiveFields(raw),
	}

	ctx, cancel := context.WithTimeout(r.Context(), requestTimeout)
	defer cancel()

	result, err := s.ask(ctx, data.Selected, data.Question, data.IncludeSources, raw)
	if err != nil {
		// Never err.Error() directly: a driver's connection/parse error routinely quotes the DSN
		// or URI back, password included, and this string is rendered into the page.
		data.ErrorMessage = redactError(err, raw)
		s.render(w, data)
		return
	}
	data.Result = result
	s.render(w, data)
}

// sensitiveFormKeys are the per-connector option keys that carry credentials outright
// (password) or may bundle them into a connection string (dsn/uri/url/endpoint commonly follow
// scheme://user:pass@host). These are used to build the connector for this one request but
// never copied into viewData.Fields, so a submitted password/DSN never comes back in the
// page's HTML -- unlike a masked <input type="password">, the raw value in a "value=..."
// attribute is plain text in the page source regardless of the input's type.
var sensitiveFormKeys = map[string]bool{
	"password": true,
	"dsn":      true,
	"uri":      true,
	"url":      true,
	"endpoint": true,
}

func redactSensitiveFields(raw map[string]string) map[string]string {
	redacted := make(map[string]string, len(raw))
	for formKey, value := range raw {
		_, key, ok := strings.Cut(formKey, "_")
		if ok && sensitiveFormKeys[key] {
			continue
		}
		redacted[formKey] = value
	}
	return redacted
}

func (s *Server) ask(ctx context.Context, selected, question string, includeSources bool, rawFields map[string]string) (*viewResult, error) {
	if selected == "" {
		return nil, fmt.Errorf("choose a database")
	}
	if question == "" {
		return nil, fmt.Errorf("a question is required")
	}

	factory, ok := connectors.Get(selected)
	if !ok {
		return nil, fmt.Errorf("unknown connector %q -- registered: %v", selected, connectors.Names())
	}

	opts := connectors.Options{}
	for _, key := range connectorFields[selected] {
		opts[key] = rawFields[selected+"_"+key]
	}

	conn, err := factory(ctx, opts)
	if err != nil {
		return nil, fmt.Errorf("connecting to %s: %w", selected, err)
	}
	defer conn.Close()

	documents, err := conn.FetchDocuments(ctx)
	if err != nil {
		return nil, fmt.Errorf("fetching documents from %s: %w", selected, err)
	}
	if len(documents) == 0 {
		return nil, fmt.Errorf("%s returned no documents -- nothing to ask against", selected)
	}

	answer, err := s.client.CreateAnswer(ctx, horizonclient.AnswerRequest{
		Question:       question,
		Documents:      documents,
		IncludeSources: includeSources,
	})
	if err != nil {
		return nil, fmt.Errorf("calling HorizonAPI at %s: %w", s.apiBaseURL, err)
	}

	return &viewResult{
		State:               answer.State,
		Answer:              answer.Answer,
		DocumentsConsidered: answer.DocumentsConsidered,
		VerifiedCandidates:  answer.VerifiedCandidates,
		AnswerBytes:         answer.AnswerBytes,
		Sources:             answer.Sources,
	}, nil
}

func (s *Server) render(w http.ResponseWriter, data viewData) {
	header := w.Header()
	header.Set("Content-Type", "text/html; charset=utf-8")
	// The page's CSS/JS are inline (no build step for a single-file template), hence
	// 'unsafe-inline' on style-src/script-src -- default-src 'self' plus the frame/base/form
	// restrictions still stop the page from loading or submitting to any other origin, which is
	// what actually matters against XSS exfiltration and clickjacking here.
	header.Set("Content-Security-Policy",
		"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "+
			"frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
	header.Set("X-Frame-Options", "DENY")
	header.Set("X-Content-Type-Options", "nosniff")
	header.Set("Referrer-Policy", "same-origin")
	if err := tmpl.Execute(w, data); err != nil {
		// A template failure is a bug in this server, not something the visitor can act on, and
		// its message may quote the data being rendered. Log it; show a fixed string.
		log.Printf("webui: rendering template: %v", err)
		http.Error(w, "internal error rendering the page", http.StatusInternalServerError)
	}
}

func registeredConnectorNames() []string {
	names := connectors.Names()
	sort.Strings(names)
	return names
}

func preferredDefault(names []string, preferred string) string {
	for _, name := range names {
		if name == preferred {
			return preferred
		}
	}
	if len(names) > 0 {
		return names[0]
	}
	return ""
}
