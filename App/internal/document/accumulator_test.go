package document

import (
	"errors"
	"strconv"
	"strings"
	"testing"
)

func TestAccumulatorAcceptsANormalCorpus(t *testing.T) {
	acc := &Accumulator{Origin: "sqlite: table \"articles\""}
	for i := 0; i < 100; i++ {
		if err := acc.Add(New("sqlite:/tmp/db/articles", string(rune('a'+i%26))+strconv.Itoa(i), "some text", acc.Len())); err != nil {
			t.Fatalf("unexpected error at %d: %v", i, err)
		}
	}
	if acc.Len() != 100 {
		t.Errorf("got %d documents, want 100", acc.Len())
	}
}

func TestAccumulatorStopsAtTheDocumentCount(t *testing.T) {
	acc := &Accumulator{Origin: "test", MaxDocuments: 3}
	for i := 0; i < 3; i++ {
		if err := acc.Add(New("src", strconv.Itoa(i), "text", i)); err != nil {
			t.Fatalf("unexpected error at %d: %v", i, err)
		}
	}
	err := acc.Add(New("src", "d", "text", 3))
	if !errors.Is(err, ErrCorpusTooLarge) {
		t.Errorf("got %v, want ErrCorpusTooLarge", err)
	}
}

// A configured ceiling may lower the API's limit but must never raise it.
func TestAccumulatorClampsToTheAPILimit(t *testing.T) {
	acc := &Accumulator{Origin: "test", MaxDocuments: 999999}
	small := New("src", "k", "text", 0)
	for i := 0; i < MaxDocuments; i++ {
		if err := acc.Add(small); err != nil {
			t.Fatalf("unexpected error at %d: %v", i, err)
		}
	}
	if err := acc.Add(small); !errors.Is(err, ErrCorpusTooLarge) {
		t.Errorf("got %v, want ErrCorpusTooLarge at the API's own ceiling", err)
	}
}

// This is the limit the review identified: MaxDocuments documents at MaxDocumentBytes each is
// 128 MiB against a 1 MiB request cap, so a corpus can be far too large while holding well under
// the document count. Without a byte budget it would be fetched in full and rejected with a 413.
func TestAccumulatorStopsOnTotalBytesWellBeforeTheDocumentCount(t *testing.T) {
	acc := &Accumulator{Origin: "test"}
	big := strings.Repeat("x", 32*1024) // 32 KiB each: under the per-document cap

	var added int
	var err error
	for i := 0; i < MaxDocuments; i++ {
		if err = acc.Add(New("src", strconv.Itoa(i), big, i)); err != nil {
			break
		}
		added++
	}

	if !errors.Is(err, ErrCorpusTooLarge) {
		t.Fatalf("got %v, want ErrCorpusTooLarge", err)
	}
	if added >= MaxDocuments {
		t.Error("the byte budget never triggered; the document count was the only limit")
	}
	// 1 MiB / 32 KiB is about 32 documents -- nowhere near the 2000-document ceiling.
	if added > 40 {
		t.Errorf("accepted %d documents totaling over 1 MiB before stopping", added)
	}
}

func TestAccumulatorRejectsAnOversizedRecord(t *testing.T) {
	acc := &Accumulator{Origin: "test"}
	huge := strings.Repeat("x", MaxDocumentBytes+1)

	err := acc.Add(New("src", "k", huge, 0))
	if !errors.Is(err, ErrDocumentTooLarge) {
		t.Errorf("got %v, want ErrDocumentTooLarge", err)
	}
	// A single oversized row is not fixable by narrowing the query, so it must be distinguishable
	// from a too-large corpus.
	if errors.Is(err, ErrCorpusTooLarge) {
		t.Error("an oversized single record was reported as a too-large corpus")
	}
}

func TestAccumulatorErrorNamesTheOrigin(t *testing.T) {
	acc := &Accumulator{Origin: `postgres: table "articles"`, MaxDocuments: 1}
	acc.Add(New("src", "a", "text", 0))
	err := acc.Add(New("src", "b", "text", 1))
	if err == nil || !strings.Contains(err.Error(), `table "articles"`) {
		t.Errorf("the error does not say which source overflowed: %v", err)
	}
}

func TestCheckPayloadAcceptsASendableRequest(t *testing.T) {
	docs := []Document{New("src", "1", "some text", 0), New("src", "2", "more text", 1)}
	if err := CheckPayload(docs, 200); err != nil {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestCheckPayloadRejectsTooManyDocuments(t *testing.T) {
	docs := make([]Document, MaxDocuments+1)
	for i := range docs {
		docs[i] = New("src", strconv.Itoa(i), "t", i)
	}
	if err := CheckPayload(docs, 0); !errors.Is(err, ErrCorpusTooLarge) {
		t.Errorf("got %v, want ErrCorpusTooLarge", err)
	}
}

func TestCheckPayloadRejectsAnOversizedBody(t *testing.T) {
	big := strings.Repeat("x", 32*1024)
	docs := make([]Document, 40) // ~1.3 MiB, over the 1 MiB cap
	for i := range docs {
		docs[i] = New("src", strconv.Itoa(i), big, i)
	}
	if err := CheckPayload(docs, 0); !errors.Is(err, ErrCorpusTooLarge) {
		t.Errorf("got %v, want ErrCorpusTooLarge", err)
	}
}

func TestCheckPayloadUsesTheTextLimitNotTheEncodedDocumentSize(t *testing.T) {
	exact := New("postgres:db:5432/prod/public/articles", "42", strings.Repeat("x", MaxDocumentBytes), 0)
	if exact.EncodedSize() <= MaxDocumentBytes {
		t.Fatal("fixture does not distinguish encoded size from text size")
	}
	if err := CheckPayload([]Document{exact}, 0); err != nil {
		t.Errorf("text exactly at the API limit was rejected: %v", err)
	}

	over := New("src", "42", strings.Repeat("x", MaxDocumentBytes+1), 0)
	if err := CheckPayload([]Document{over}, 0); !errors.Is(err, ErrDocumentTooLarge) {
		t.Errorf("got %v, want ErrDocumentTooLarge", err)
	}
}

func TestCheckPayloadRechecksMetadata(t *testing.T) {
	doc := New("src", "42", "text", 0)
	doc.Source = strings.Repeat("s", MaxMetadataBytes+1)

	if err := CheckPayload([]Document{doc}, 0); !errors.Is(err, ErrInvalidMetadata) {
		t.Errorf("got %v, want ErrInvalidMetadata", err)
	}
}

func TestPayloadChecksRejectInvalidUTF8BeforeJSONCanRewriteIt(t *testing.T) {
	invalid := string([]byte{'o', 'k', 0xff})

	t.Run("text through accumulator", func(t *testing.T) {
		doc := New("src", "42", invalid, 0)
		if err := (&Accumulator{Origin: "test"}).Add(doc); !errors.Is(err, ErrInvalidUTF8) {
			t.Errorf("got %v, want ErrInvalidUTF8", err)
		}
	})

	t.Run("text through final preflight", func(t *testing.T) {
		doc := New("src", "42", invalid, 0)
		if err := CheckPayload([]Document{doc}, 0); !errors.Is(err, ErrInvalidUTF8) {
			t.Errorf("got %v, want ErrInvalidUTF8", err)
		}
	})

	t.Run("metadata through final preflight", func(t *testing.T) {
		doc := New("src", "42", "text", 0)
		doc.Source = invalid
		if err := CheckPayload([]Document{doc}, 0); !errors.Is(err, ErrInvalidUTF8) {
			t.Errorf("got %v, want ErrInvalidUTF8", err)
		}
	})
}

// The question and flags count against the same 1 MiB cap as the documents, so a corpus that
// just fits on its own can still overflow once the envelope is added.
func TestCheckPayloadCountsTheEnvelope(t *testing.T) {
	big := strings.Repeat("x", 60*1024)
	var docs []Document
	for i := 0; i < 16; i++ {
		docs = append(docs, New("src", strconv.Itoa(i), big, i))
	}
	if err := CheckPayload(docs, 0); err != nil {
		t.Fatalf("baseline should fit: %v", err)
	}
	if err := CheckPayload(docs, MaxRequestBytes); !errors.Is(err, ErrCorpusTooLarge) {
		t.Errorf("got %v, want ErrCorpusTooLarge once the envelope is counted", err)
	}
}

func TestEncodedSizeReflectsTheJSONNotJustTheText(t *testing.T) {
	doc := New("postgres:127.0.0.1:5432/db/articles", "42", "hello", 0)
	// The encoding carries fact_id, source, session, the sha256 digest and the field names, so
	// the wire size is far larger than the text -- budgeting on text length alone would
	// under-count and still produce a 413.
	if doc.EncodedSize() <= len(doc.Text)+50 {
		t.Errorf("EncodedSize %d looks like it is measuring the text, not the JSON",
			doc.EncodedSize())
	}
}
