package elasticsearch

import (
	"errors"
	"testing"
)

func TestScrollIDForPageAdoptsTheCurrentCursor(t *testing.T) {
	got, err := scrollIDForPage("old", "new", 2)
	if err != nil {
		t.Fatal(err)
	}
	if got != "new" {
		t.Errorf("got cleanup cursor %q, want current cursor", got)
	}
}

func TestScrollIDForPageRejectsANonEmptyPageWithoutItsOwnCursor(t *testing.T) {
	got, err := scrollIDForPage("old", "", 1)
	if !errors.Is(err, errScrollCursorMissing) {
		t.Errorf("got %v, want errScrollCursorMissing", err)
	}
	if got != "old" {
		t.Errorf("got cleanup cursor %q, want previous cursor %q", got, "old")
	}
}

func TestScrollIDForPageAllowsAnEmptyTerminalPage(t *testing.T) {
	got, err := scrollIDForPage("last", "", 0)
	if err != nil {
		t.Fatal(err)
	}
	if got != "last" {
		t.Errorf("got cleanup cursor %q, want %q", got, "last")
	}
}
