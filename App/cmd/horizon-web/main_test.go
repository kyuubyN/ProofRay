package main

import "testing"

// checkBindAddr is the only thing preventing this unauthenticated server -- whose connectors will
// dial any host they are handed -- from being published to a network by a stray WEB_ADDR. These
// cases are the shapes that would actually appear in a compose file or a shell export.
func TestCheckBindAddrRejectsNonLoopback(t *testing.T) {
	exposed := []string{
		"0.0.0.0:8080",   // every IPv4 interface
		":8080",          // every interface, host omitted
		"[::]:8080",      // every IPv6 interface
		"192.168.1.5:80", // a specific LAN interface
		"10.0.0.7:8080",  // a specific private interface
		"203.0.113.9:80", // a public address
	}
	for _, addr := range exposed {
		if err := checkBindAddr(addr, false); err == nil {
			t.Errorf("checkBindAddr(%q, false) = nil, want a refusal", addr)
		}
	}
}

func TestCheckBindAddrAllowsLoopback(t *testing.T) {
	loopback := []string{
		"127.0.0.1:8080",
		"127.0.0.53:8080", // the whole 127/8 range is loopback, not just .1
		"[::1]:8080",
		"localhost:8080", // resolves to loopback via the hosts file
	}
	for _, addr := range loopback {
		if err := checkBindAddr(addr, false); err != nil {
			t.Errorf("checkBindAddr(%q, false) = %v, want nil", addr, err)
		}
	}
}

// The opt-out has to actually work: an operator who has read the warning and accepts the exposure
// must be able to bind a real interface, otherwise the check just gets deleted by the next person.
func TestCheckBindAddrHonorsExplicitOptIn(t *testing.T) {
	for _, addr := range []string{"0.0.0.0:8080", ":8080", "192.168.1.5:80"} {
		if err := checkBindAddr(addr, true); err != nil {
			t.Errorf("checkBindAddr(%q, true) = %v, want nil", addr, err)
		}
	}
}

func TestCheckBindAddrRejectsMalformed(t *testing.T) {
	// A malformed address must fail rather than fall through to a bind: "8080" has no host:port
	// split, so treating it as safe would be guessing at what the operator meant.
	for _, addr := range []string{"8080", "", "127.0.0.1"} {
		if err := checkBindAddr(addr, false); err == nil {
			t.Errorf("checkBindAddr(%q, false) = nil, want an error", addr)
		}
	}
}

func TestGetenvDefault(t *testing.T) {
	t.Setenv("HORIZON_TEST_VALUE", "set")
	if got := getenvDefault("HORIZON_TEST_VALUE", "fallback"); got != "set" {
		t.Errorf("got %q, want %q", got, "set")
	}

	t.Setenv("HORIZON_TEST_VALUE", "")
	if got := getenvDefault("HORIZON_TEST_VALUE", "fallback"); got != "fallback" {
		t.Errorf("got %q, want %q", got, "fallback")
	}
}
