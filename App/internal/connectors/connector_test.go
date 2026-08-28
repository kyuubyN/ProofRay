package connectors

import (
	"errors"
	"testing"
)

func TestValidateIdentifier(t *testing.T) {
	valid := []string{"articles", "_private", "table1", "MixedCase", "a_b_c9"}
	for _, name := range valid {
		if err := ValidateIdentifier(name); err != nil {
			t.Errorf("ValidateIdentifier(%q) = %v, want nil", name, err)
		}
	}

	// Every one of these reaches a fmt.Sprintf'd SQL identifier position in the postgres/mysql/
	// sqlite connectors, where the driver cannot parameterize it -- so rejection here is the only
	// thing standing between a form field and injected SQL.
	invalid := []string{
		"",
		"1table",                  // leading digit
		"users; DROP TABLE users", // statement break
		"users--",                 // comment
		`users" OR "1"="1`,        // quote break-out
		"public.articles",         // qualified name, not a bare identifier
		"articles ",               // trailing space
		"café",                    // non-ASCII
		"articles\nunion select",  // newline
	}
	for _, name := range invalid {
		if err := ValidateIdentifier(name); err == nil {
			t.Errorf("ValidateIdentifier(%q) = nil, want error", name)
		}
	}
}

func TestOptionsGet(t *testing.T) {
	t.Run("option wins over env and fallback", func(t *testing.T) {
		t.Setenv("TEST_CONNECTOR_VALUE", "from-env")
		opts := Options{"key": "from-opts"}
		if got := opts.Get("key", "TEST_CONNECTOR_VALUE", "fallback"); got != "from-opts" {
			t.Errorf("got %q, want %q", got, "from-opts")
		}
	})

	t.Run("env wins over fallback", func(t *testing.T) {
		t.Setenv("TEST_CONNECTOR_VALUE", "from-env")
		if got := (Options{}).Get("key", "TEST_CONNECTOR_VALUE", "fallback"); got != "from-env" {
			t.Errorf("got %q, want %q", got, "from-env")
		}
	})

	t.Run("empty option value falls through to env", func(t *testing.T) {
		t.Setenv("TEST_CONNECTOR_VALUE", "from-env")
		opts := Options{"key": ""}
		if got := opts.Get("key", "TEST_CONNECTOR_VALUE", "fallback"); got != "from-env" {
			t.Errorf("got %q, want %q", got, "from-env")
		}
	})

	t.Run("fallback when nothing is set", func(t *testing.T) {
		t.Setenv("TEST_CONNECTOR_VALUE", "")
		if got := (Options{}).Get("key", "TEST_CONNECTOR_VALUE", "fallback"); got != "fallback" {
			t.Errorf("got %q, want %q", got, "fallback")
		}
	})

	t.Run("nil Options is usable", func(t *testing.T) {
		var opts Options
		if got := opts.Get("key", "", "fallback"); got != "fallback" {
			t.Errorf("got %q, want %q", got, "fallback")
		}
	})
}

func TestMaxDocuments(t *testing.T) {
	t.Run("defaults when unset", func(t *testing.T) {
		t.Setenv("HORIZON_MAX_DOCUMENTS", "")
		got, err := MaxDocuments(Options{})
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if got != DefaultMaxDocuments {
			t.Errorf("got %d, want %d", got, DefaultMaxDocuments)
		}
	})

	t.Run("reads an explicit ceiling", func(t *testing.T) {
		got, err := MaxDocuments(Options{"max_documents": "25"})
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if got != 25 {
			t.Errorf("got %d, want 25", got)
		}
	})

	// Zero and negative values are rejected rather than read as "no ceiling": an unbounded fetch
	// is the exact failure this ceiling exists to prevent, so it must not be reachable by
	// accident from a form field or a stray env var.
	for _, raw := range []string{"0", "-1", "abc", "10.5", " "} {
		t.Run("rejects "+raw, func(t *testing.T) {
			if _, err := MaxDocuments(Options{"max_documents": raw}); err == nil {
				t.Errorf("MaxDocuments(%q) = nil error, want error", raw)
			}
		})
	}
}

func TestErrCorpusTooLargeIsMatchable(t *testing.T) {
	// Callers distinguish "corpus too large" from a transport failure with errors.Is, so the
	// sentinel has to survive the %w wrapping each connector applies.
	wrapped := errors.Join(ErrCorpusTooLarge, errors.New("context"))
	if !errors.Is(wrapped, ErrCorpusTooLarge) {
		t.Error("ErrCorpusTooLarge did not survive wrapping")
	}
}

func TestRegistry(t *testing.T) {
	Register("test-backend", nil)

	if _, ok := Get("test-backend"); !ok {
		t.Error("Get did not find a just-registered backend")
	}
	if _, ok := Get("no-such-backend"); ok {
		t.Error("Get found a backend that was never registered")
	}

	var found bool
	for _, name := range Names() {
		if name == "test-backend" {
			found = true
		}
	}
	if !found {
		t.Error("Names() omitted a registered backend")
	}
}
