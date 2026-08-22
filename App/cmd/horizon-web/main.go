// Command horizon-web serves a small local web UI over the same Connector registry
// cmd/horizon-connect drives from the command line: pick a database, type a question, get back
// whatever HorizonAPI (api/server.py) resolves. See App/internal/webui for the handler and
// App/README.md's "Not done here" section for what this deliberately lacks (auth, HTTPS,
// persistence) -- bind only to a trusted network.
//
// Usage:
//
//	# start the Python API first (from the repo root):
//	#   python3 api/server.py   # serves http://127.0.0.1:8420
//	go run ./cmd/horizon-web
//	# then open http://127.0.0.1:8080
package main

import (
	"fmt"
	"log"
	"net/http"
	"os"

	"horizonmemory/connector/internal/horizonclient"
	"horizonmemory/connector/internal/webui"

	// Blank-imported for side-effect registration into the connectors registry, the same
	// pattern cmd/horizon-connect/main.go uses.
	_ "horizonmemory/connector/internal/connectors/dynamodb"
	_ "horizonmemory/connector/internal/connectors/elasticsearch"
	_ "horizonmemory/connector/internal/connectors/mongodb"
	_ "horizonmemory/connector/internal/connectors/mysql"
	_ "horizonmemory/connector/internal/connectors/postgres"
	_ "horizonmemory/connector/internal/connectors/redis"
	_ "horizonmemory/connector/internal/connectors/sqlite"
)

func main() {
	apiBaseURL := getenvDefault("HORIZON_API_BASE_URL", "http://127.0.0.1:8420")
	addr := getenvDefault("WEB_ADDR", "127.0.0.1:8080")

	server := webui.New(horizonclient.New(apiBaseURL), apiBaseURL)

	log.Printf("horizon-web: listening on http://%s (HorizonAPI at %s)", addr, apiBaseURL)
	if err := http.ListenAndServe(addr, server.Routes()); err != nil {
		fmt.Fprintln(os.Stderr, "horizon-web:", err)
		os.Exit(1)
	}
}

func getenvDefault(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
