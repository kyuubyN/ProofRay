package document

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"testing"
)

func TestNewCarriesProvenance(t *testing.T) {
	const source = "postgres:db.internal:5432/prod/articles"
	doc := New(source, "42", "the text", 3)

	if doc.Source != source+":42" {
		t.Errorf("Source = %q, want %q", doc.Source, source+":42")
	}
	if doc.Session != source {
		t.Errorf("Session = %q, want %q", doc.Session, source)
	}
	if doc.Text != "the text" {
		t.Errorf("Text = %q, want %q", doc.Text, "the text")
	}
	if doc.Scope != Scope {
		t.Errorf("Scope = %d, want %d", doc.Scope, Scope)
	}
	if doc.Version != 1 {
		t.Errorf("Version = %d, want 1", doc.Version)
	}
	if doc.Sequence == nil || *doc.Sequence != 3 {
		t.Errorf("Sequence = %v, want 3", doc.Sequence)
	}
}

// The digest is what lets api/_engine_bridge.py verify the text survived the network hop intact;
// a wrong one makes the server reject the whole request.
func TestNewComputesTextDigest(t *testing.T) {
	const text = "the text"
	doc := New("postgres:db.internal:5432/prod/articles", "42", text, 0)

	want := sha256.Sum256([]byte(text))
	if doc.TextSHA256 != hex.EncodeToString(want[:]) {
		t.Errorf("TextSHA256 = %q, want %q", doc.TextSHA256, hex.EncodeToString(want[:]))
	}
}

// Identity must come from the record's key, not its position -- that is the whole point of the
// structured schema. A row that moves because an earlier row was deleted has to keep its fact_id,
// or provenance cannot be reopened across two fetches.
func TestFactIDIsStableAcrossPositionAndText(t *testing.T) {
	const source = "postgres:db.internal:5432/prod/articles"
	first := New(source, "42", "original text", 0)
	moved := New(source, "42", "original text", 97)
	edited := New(source, "42", "the row was edited", 0)

	if first.FactID != moved.FactID {
		t.Error("fact_id changed when the row moved position")
	}
	if first.FactID != edited.FactID {
		t.Error("fact_id changed when the row's text was edited; identity must track the key")
	}
}

func TestFactIDDistinguishesRecords(t *testing.T) {
	a := New("postgres:db:5432/p/articles", "42", "text", 0)
	b := New("postgres:db:5432/p/articles", "43", "text", 1)
	if a.FactID == b.FactID {
		t.Error("two different primary keys produced the same fact_id")
	}
}

// Two backends that happen to share a key ("42") must not collide, or documents from one would be
// silently indistinguishable from the other's.
func TestFactIDIsNamespacedBySource(t *testing.T) {
	pg := New("postgres:db:5432/p/articles", "42", "text", 0)
	my := New("mysql:db:3306/p/articles", "42", "text", 0)
	if pg.FactID == my.FactID {
		t.Error("the same key in two backends produced the same fact_id")
	}
}

// factID length-prefixes the source so no source/key boundary shift can produce the same input.
// A plain separator byte would be ambiguous for any key that may contain it -- and Redis keys
// contain ':' by convention.
func TestFactIDBoundaryCannotBeShifted(t *testing.T) {
	pairs := [][2]string{
		{"ab", "c"},
		{"a", "bc"},
		{"a:b", "c"},
		{"a", "b:c"},
	}
	seen := map[int64][2]string{}
	for _, pair := range pairs {
		id := New(pair[0], pair[1], "text", 0).FactID
		if previous, clash := seen[id]; clash {
			t.Errorf("%v and %v produced the same fact_id", previous, pair)
		}
		seen[id] = pair
	}
}

// Two servers holding the same table name and the same row id must not share an identity -- this
// is why the source carries host/port and database rather than just the backend name.
func TestFactIDDistinguishesPhysicalSources(t *testing.T) {
	sources := []string{
		"postgres:10.0.0.1:5432/prod/articles",
		"postgres:10.0.0.2:5432/prod/articles",    // different host
		"postgres:10.0.0.1:5433/prod/articles",    // different port
		"postgres:10.0.0.1:5432/staging/articles", // different database
		"postgres:10.0.0.1:5432/prod/notes",       // different table
		"sqlite:/var/data/a.db/articles",
		"sqlite:/var/data/b.db/articles", // different file
	}
	seen := map[int64]string{}
	for _, source := range sources {
		id := New(source, "42", "text", 0).FactID
		if previous, clash := seen[id]; clash {
			t.Errorf("%q and %q share a fact_id for the same key", previous, source)
		}
		seen[id] = source
	}
}

// api/_engine_bridge.py rejects fact_id >= MAX_FACT_ID (1 << 62) outright, so the mask has to hold
// for every key, not just typical ones.
func TestFactIDStaysInServerDomain(t *testing.T) {
	const serverMax = int64(1) << 62
	keys := []string{
		"", "0", "42",
		"507f1f77bcf86cd799439011",
		"a-very-long-key-that-hashes-to-something-large-------------------------",
		"\x00\xff\xfe",
	}
	for _, key := range keys {
		doc := New("session", key, "text", 0)
		if doc.FactID < 0 {
			t.Errorf("key %q produced a negative fact_id %d", key, doc.FactID)
		}
		if doc.FactID >= serverMax {
			t.Errorf("key %q produced fact_id %d, at or above the server's limit", key, doc.FactID)
		}
	}
}

func TestWithEventTime(t *testing.T) {
	doc := New("s", "k", "text", 0)
	if doc.EventTime != nil {
		t.Error("EventTime should be unset unless the backend supplies one")
	}

	stamped := doc.WithEventTime(1735689600)
	if stamped.EventTime == nil || *stamped.EventTime != 1735689600 {
		t.Errorf("EventTime = %v, want 1735689600", stamped.EventTime)
	}
	// WithEventTime takes a value receiver, so the original must be untouched.
	if doc.EventTime != nil {
		t.Error("WithEventTime mutated the original document")
	}
}

// The wire format has to match the schema api/_engine_bridge.py validates: it rejects unknown
// fields outright and requires these six, so a rename here breaks every request at the server.
func TestJSONMatchesServerSchema(t *testing.T) {
	doc := New("postgres:db.internal:5432/prod/articles", "42", "the text", 0)

	encoded, err := json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}

	var decoded map[string]any
	if err := json.Unmarshal(encoded, &decoded); err != nil {
		t.Fatal(err)
	}

	required := []string{"fact_id", "text", "source", "scope", "session", "version"}
	for _, field := range required {
		if _, present := decoded[field]; !present {
			t.Errorf("required field %q is missing from the encoded document", field)
		}
	}

	// _STRUCTURED_DOCUMENT_FIELDS in api/_engine_bridge.py -- anything else is rejected.
	allowed := map[string]bool{
		"fact_id": true, "text": true, "source": true, "scope": true, "session": true,
		"version": true, "sequence": true, "event_time": true, "role": true,
		"speaker": true, "span": true, "text_sha256": true,
	}
	for field := range decoded {
		if !allowed[field] {
			t.Errorf("field %q is not in the server's accepted field set", field)
		}
	}
}

// Optional fields must vanish rather than serialize as null: the server's nullable check accepts
// null, but an omitted field keeps the payload honest about what the backend actually knew.
func TestUnsetOptionalFieldsAreOmitted(t *testing.T) {
	encoded, err := json.Marshal(New("s", "k", "text", 0))
	if err != nil {
		t.Fatal(err)
	}
	var decoded map[string]any
	if err := json.Unmarshal(encoded, &decoded); err != nil {
		t.Fatal(err)
	}

	for _, field := range []string{"event_time", "role", "speaker", "span"} {
		if _, present := decoded[field]; present {
			t.Errorf("unset optional field %q was serialized", field)
		}
	}
}
