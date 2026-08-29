package redis

import (
	"errors"
	"testing"

	"horizonmemory/connector/internal/document"
)

func TestAppendUniqueKeyIgnoresScanDuplicates(t *testing.T) {
	seen := make(map[string]struct{})
	keys, err := appendUniqueKey(nil, seen, "articles:1", 2)
	if err != nil {
		t.Fatal(err)
	}
	keys, err = appendUniqueKey(keys, seen, "articles:1", 2)
	if err != nil {
		t.Fatal(err)
	}
	if len(keys) != 1 || keys[0] != "articles:1" {
		t.Errorf("duplicate SCAN result was retained: %#v", keys)
	}
}

func TestAppendUniqueKeyStopsAtTheDocumentLimitDuringScan(t *testing.T) {
	seen := make(map[string]struct{})
	keys, err := appendUniqueKey(nil, seen, "articles:1", 2)
	if err != nil {
		t.Fatal(err)
	}
	keys, err = appendUniqueKey(keys, seen, "articles:2", 2)
	if err != nil {
		t.Fatal(err)
	}
	keys, err = appendUniqueKey(keys, seen, "articles:3", 2)
	if !errors.Is(err, document.ErrCorpusTooLarge) {
		t.Errorf("got %v, want ErrCorpusTooLarge", err)
	}
	if len(keys) != 2 {
		t.Errorf("accepted %d keys past a limit of 2", len(keys))
	}
}
