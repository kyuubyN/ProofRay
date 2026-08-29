// Command horizon-connect fetches the current corpus from one configured database backend and
// asks a question against it through HorizonAPI (api/server.py). It is the Go-side half of the
// "bring your own database" pattern HorizonAI Engine/examples/*_documents_example.py demonstrate
// in Python: this binary does the fetching, HorizonAnswerEngine (over HTTP) still does all the
// routing/verification/composition.
//
// Usage:
//
//	CONNECTOR=postgres POSTGRES_DSN="postgresql://user:pass@localhost:5432/yourdb" \
//	QUESTION="What percent did the Meridian project reduce cost by?" \
//	  go run ./cmd/horizon-connect
//
// Requires api/server.py already running (default http://127.0.0.1:8420, override with
// HORIZON_API_BASE_URL).
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"time"

	"horizonmemory/connector/internal/config"
	"horizonmemory/connector/internal/connectors"
	"horizonmemory/connector/internal/horizonclient"
	"horizonmemory/connector/internal/sanitize"

	// Blank-imported for side-effect registration into the connectors registry, the same
	// pattern database/sql drivers use.
	_ "horizonmemory/connector/internal/connectors/dynamodb"
	_ "horizonmemory/connector/internal/connectors/elasticsearch"
	_ "horizonmemory/connector/internal/connectors/mongodb"
	_ "horizonmemory/connector/internal/connectors/mysql"
	_ "horizonmemory/connector/internal/connectors/postgres"
	_ "horizonmemory/connector/internal/connectors/redis"
	_ "horizonmemory/connector/internal/connectors/sqlite"
)

func main() {
	if err := run(); err != nil {
		writeError(os.Stderr, err)
		os.Exit(1)
	}
}

func writeError(w io.Writer, err error) {
	fmt.Fprintln(w, "horizon-connect:", safeError(err))
}

var sensitiveEnvironmentKeys = []string{
	"POSTGRES_DSN", "MYSQL_PASSWORD", "MONGODB_URI", "REDIS_URL", "ELASTICSEARCH_URL",
	"DYNAMODB_ENDPOINT_URL", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
	"PROOFRAY_API_TOKEN", "HORIZON_API_TOKEN",
}

// safeError removes every credential this CLI can read from its environment before an error is
// printed. Drivers routinely echo malformed connection strings; unlike the web UI, the CLI has
// no submitted form map, so its exact secrets come from the environment itself.
func safeError(err error) string {
	secrets := make([]string, 0, len(sensitiveEnvironmentKeys))
	for _, key := range sensitiveEnvironmentKeys {
		if value := os.Getenv(key); value != "" {
			secrets = append(secrets, value)
		}
	}
	return sanitize.Error(err, secrets...)
}

func run() error {
	cfg, err := config.Load()
	if err != nil {
		return err
	}

	factory, ok := connectors.Get(cfg.Connector)
	if !ok {
		return fmt.Errorf("unknown CONNECTOR %q -- registered: %v", cfg.Connector, connectors.Names())
	}

	ctx, cancel := context.WithTimeout(context.Background(), 60*time.Second)
	defer cancel()

	conn, err := factory(ctx, nil)
	if err != nil {
		return fmt.Errorf("connecting to %s: %w", cfg.Connector, err)
	}
	defer conn.Close()

	documents, err := conn.FetchDocuments(ctx)
	if err != nil {
		return fmt.Errorf("fetching documents from %s: %w", cfg.Connector, err)
	}
	if len(documents) == 0 {
		return fmt.Errorf("%s returned no documents -- nothing to ask %q against", cfg.Connector, cfg.Question)
	}

	client := horizonclient.New(cfg.HorizonBaseURL)
	answer, err := client.CreateAnswer(ctx, horizonclient.AnswerRequest{
		Question:       cfg.Question,
		Documents:      documents,
		IncludeSources: cfg.IncludeSources,
	})
	if err != nil {
		return fmt.Errorf("calling HorizonAPI at %s: %w", cfg.HorizonBaseURL, err)
	}

	encoded, err := json.MarshalIndent(answer, "", "  ")
	if err != nil {
		return fmt.Errorf("encoding answer: %w", err)
	}
	fmt.Println(string(encoded))
	return nil
}
