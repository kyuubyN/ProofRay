package sanitize

import "testing"

func TestMessageDoesNotRedactItsOwnPlaceholderTwice(t *testing.T) {
	got := Message("authentication failed: password=xy; retry denied", "xy")
	want := "authentication failed: password=[redacted]; retry denied"
	if got != want {
		t.Errorf("got %q, want %q", got, want)
	}
}
