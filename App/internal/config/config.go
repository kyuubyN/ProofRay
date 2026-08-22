// Package config reads process configuration from the environment, mirroring the *_DSN/*_URL
// variables HorizonAI Engine/examples/*_documents_example.py already use per backend.
package config

import (
	"fmt"
	"os"
)

// Config is the connector CLI's process-level configuration -- which HorizonAPI instance to
// call and, optionally, which question to ask against the fetched corpus.
type Config struct {
	// HorizonBaseURL is the running api/server.py instance. Defaults to
	// http://127.0.0.1:8420, the same default run_api_server.py binds to.
	HorizonBaseURL string

	// Connector is the registered backend name to use (e.g. "postgres", "sqlite").
	Connector string

	// Question is passed straight through to POST /v1/answers.
	Question string

	// IncludeSources requests the full verified claim list back, not just the compressed
	// answer.
	IncludeSources bool
}

// Load builds a Config from HORIZON_API_BASE_URL, CONNECTOR, and QUESTION. Connector and
// Question have no default -- an empty value is a caller error, not silently filled in.
func Load() (Config, error) {
	cfg := Config{
		HorizonBaseURL: getenvDefault("HORIZON_API_BASE_URL", "http://127.0.0.1:8420"),
		Connector:      os.Getenv("CONNECTOR"),
		Question:       os.Getenv("QUESTION"),
		IncludeSources: os.Getenv("INCLUDE_SOURCES") == "1",
	}
	if cfg.Connector == "" {
		return Config{}, fmt.Errorf("config: CONNECTOR is required (e.g. CONNECTOR=postgres)")
	}
	if cfg.Question == "" {
		return Config{}, fmt.Errorf("config: QUESTION is required")
	}
	return cfg, nil
}

func getenvDefault(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
