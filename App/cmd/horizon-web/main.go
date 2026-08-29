// Command horizon-web serves a small local web UI over the same Connector registry
// cmd/horizon-connect drives from the command line: pick a database, type a question, get back
// whatever HorizonAPI (api/server.py) resolves. Its page carries a "Benchmark Demo" nav button
// linking to website/web_app.py's Flask demo (HORIZON_BENCHMARK_URL) -- the two front ends stay
// separate processes, just cross-linked, so switching between them doesn't require merging a Go
// and a Python codebase together. See App/internal/webui for the handler and App/README.md's
// "Not done here" section for what this deliberately lacks (auth, HTTPS, persistence) -- bind
// only to a trusted network.
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
	"net"
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

// allowRemoteEnv opts a deployment out of the loopback-only bind check below. It is deliberately
// a separate variable from WEB_ADDR: binding a public interface then becomes something an
// operator states outright, not something that happens by editing an address.
const allowRemoteEnv = "HORIZON_WEB_ALLOW_REMOTE"

func main() {
	apiBaseURL := getenvDefault("HORIZON_API_BASE_URL", "http://127.0.0.1:8420")
	addr := getenvDefault("WEB_ADDR", "127.0.0.1:8080")
	benchmarkURL := getenvDefault("HORIZON_BENCHMARK_URL", "http://127.0.0.1:5050")

	if err := horizonclient.ValidateBaseURL(apiBaseURL); err != nil {
		fmt.Fprintln(os.Stderr, "horizon-web:", err)
		os.Exit(1)
	}
	if err := checkBindAddr(addr, os.Getenv(allowRemoteEnv) != ""); err != nil {
		fmt.Fprintln(os.Stderr, "horizon-web:", err)
		os.Exit(1)
	}

	server := webui.New(horizonclient.New(apiBaseURL), apiBaseURL, benchmarkURL)

	log.Printf("horizon-web: listening on http://%s (HorizonAPI at %s)", addr, apiBaseURL)
	if err := http.ListenAndServe(addr, server.Routes()); err != nil {
		fmt.Fprintln(os.Stderr, "horizon-web:", err)
		os.Exit(1)
	}
}

// checkBindAddr refuses to start on a non-loopback interface unless the operator opted in.
//
// This server has no authentication, no CSRF token, and no rate limiting, and every connector is
// an intentional SSRF surface: whoever reaches /ask can make this process open a connection to
// any host it can route to. On loopback that is bounded by who can already run code on the
// machine. On any other interface it is bounded by the network -- so reaching a public bind by
// accident (a stray WEB_ADDR=0.0.0.0:8080 in a compose file) would silently publish all of it.
// README's "Not done here" already documents the risk; documentation alone does not stop the
// accident, so the unsafe bind has to be stated rather than merely reached.
func checkBindAddr(addr string, allowRemote bool) error {
	if allowRemote {
		return nil
	}

	host, _, err := net.SplitHostPort(addr)
	if err != nil {
		return fmt.Errorf("WEB_ADDR %q is not a valid host:port address: %w", addr, err)
	}

	// An empty host (":8080") means every interface -- the same exposure as 0.0.0.0.
	if host != "" {
		if ip := net.ParseIP(host); ip != nil {
			if ip.IsLoopback() {
				return nil
			}
		} else {
			// A hostname rather than a literal: loopback only if every address it resolves to is.
			addrs, resolveErr := net.LookupIP(host)
			if resolveErr != nil {
				return fmt.Errorf("WEB_ADDR %q: cannot resolve host %q: %w", addr, host, resolveErr)
			}
			allLoopback := len(addrs) > 0
			for _, ip := range addrs {
				if !ip.IsLoopback() {
					allLoopback = false
					break
				}
			}
			if allLoopback {
				return nil
			}
		}
	}

	return fmt.Errorf(
		"refusing to bind %q: it is not a loopback address, and this server has no auth, no CSRF\n"+
			"protection, and no rate limiting -- anyone who can reach it can make this process\n"+
			"connect to any database host it can route to (see App/README.md, \"Not done here\").\n"+
			"Bind 127.0.0.1 instead, or set %s=1 to accept that exposure deliberately.",
		addr, allowRemoteEnv,
	)
}

func getenvDefault(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
