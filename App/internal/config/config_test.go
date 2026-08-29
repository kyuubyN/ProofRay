package config

import (
	"errors"
	"strings"
	"testing"

	"horizonmemory/connector/internal/horizonclient"
)

func setValidEnvironment(t *testing.T) {
	t.Helper()
	t.Setenv("CONNECTOR", "sqlite")
	t.Setenv("QUESTION", "What changed?")
	t.Setenv("HORIZON_API_BASE_URL", "")
	t.Setenv("INCLUDE_SOURCES", "")
}

func TestLoadDefaultsAndFlags(t *testing.T) {
	setValidEnvironment(t)
	t.Setenv("INCLUDE_SOURCES", "1")

	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.HorizonBaseURL != "http://127.0.0.1:8420" {
		t.Errorf("HorizonBaseURL = %q", cfg.HorizonBaseURL)
	}
	if !cfg.IncludeSources {
		t.Error("INCLUDE_SOURCES=1 was ignored")
	}
}

func TestLoadRejectsMissingConnectorAndQuestion(t *testing.T) {
	setValidEnvironment(t)
	t.Setenv("CONNECTOR", "")
	if _, err := Load(); err == nil {
		t.Error("missing CONNECTOR was accepted")
	}

	setValidEnvironment(t)
	t.Setenv("QUESTION", "")
	if _, err := Load(); err == nil {
		t.Error("missing QUESTION was accepted")
	}
}

func TestLoadRejectsQuestionTheAPIWouldReject(t *testing.T) {
	setValidEnvironment(t)
	for _, question := range []string{" \t\n", strings.Repeat("x", horizonclient.MaxQuestionBytes+1)} {
		t.Setenv("QUESTION", question)
		if _, err := Load(); !errors.Is(err, horizonclient.ErrInvalidQuestion) {
			t.Errorf("QUESTION of %d bytes: got %v, want ErrInvalidQuestion", len(question), err)
		}
	}
}

func TestLoadRejectsInvalidAPIBaseURL(t *testing.T) {
	setValidEnvironment(t)
	for _, baseURL := range []string{"not a URL", "ftp://api.internal", "http://user:pass@api.internal"} {
		t.Setenv("HORIZON_API_BASE_URL", baseURL)
		if _, err := Load(); !errors.Is(err, horizonclient.ErrInvalidBaseURL) {
			t.Errorf("base URL %q: got %v, want ErrInvalidBaseURL", baseURL, err)
		}
	}
}
