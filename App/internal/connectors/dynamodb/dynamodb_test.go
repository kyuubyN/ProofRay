package dynamodb

import (
	"strings"
	"testing"
)

func TestEndpointIdentityRemovesCredentials(t *testing.T) {
	const raw = "http://local-user:local-pass@localhost:8000/path?token=secret#fragment"

	got := endpointIdentity(raw)

	for _, secret := range []string{"local-user", "local-pass", "token", "secret", "fragment"} {
		if strings.Contains(got, secret) {
			t.Errorf("endpoint identity leaked %q: %q", secret, got)
		}
	}
	if got != "http://localhost:8000/path" {
		t.Errorf("got %q, want credential-free endpoint", got)
	}
}

func TestEndpointIdentityNeverReturnsMalformedInput(t *testing.T) {
	const raw = "http://user:password@%zz"

	got := endpointIdentity(raw)

	if strings.Contains(got, "user") || strings.Contains(got, "password") || got == raw {
		t.Errorf("malformed endpoint leaked credentials: %q", got)
	}
	if !strings.HasPrefix(got, "invalid-endpoint-sha256:") {
		t.Errorf("got %q, want a stable credential-free digest", got)
	}
}

func TestEndpointIdentityBoundsPathologicalURLs(t *testing.T) {
	raw := "http://localhost:8000/" + strings.Repeat("path", 2000)

	got := endpointIdentity(raw)

	if len(got) > 1024 {
		t.Errorf("endpoint identity is %d bytes, want at most 1024", len(got))
	}
	if !strings.HasPrefix(got, "endpoint-sha256:") {
		t.Errorf("got %q, want a bounded digest", got)
	}
}
