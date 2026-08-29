package document

import (
	"errors"
	"fmt"
)

// ErrCorpusTooLarge reports a corpus the API cannot accept. Failing here is deliberate: Horizon
// answers only from the documents it is given, so a silently truncated corpus would produce
// answers that look verified while resting on a partial view of the data.
var ErrCorpusTooLarge = errors.New("corpus exceeds what the API accepts")

// ErrDocumentTooLarge reports a single record larger than the server's per-document ceiling.
// Unlike a too-large corpus, this cannot be fixed by narrowing the query -- the one row has to
// be excluded or split -- so it is a distinct error.
var ErrDocumentTooLarge = errors.New("document exceeds the per-document byte limit")

// Accumulator collects documents during a fetch while enforcing every limit the API will apply,
// so a corpus that cannot be sent fails while it is being read rather than as an HTTP 413 after
// the whole thing has been pulled over the network.
//
// The byte budget is the limit that actually binds: MaxDocuments documents at MaxDocumentBytes
// each would be 128 MiB against a 1 MiB request cap, so counting documents alone never
// establishes that a corpus is sendable.
type Accumulator struct {
	// Origin names the source in error messages ("table \"articles\"", "index \"logs\"").
	Origin string

	// MaxDocuments caps the document count. Zero means MaxDocuments (the package constant).
	// It may lower that ceiling but never raise it -- see connectors.MaxDocuments.
	MaxDocuments int

	docs  []Document
	bytes int
}

// Add appends one document, or reports why the corpus cannot be sent.
func (a *Accumulator) Add(doc Document) error {
	limit := a.MaxDocuments
	if limit <= 0 || limit > MaxDocuments {
		limit = MaxDocuments
	}

	size := doc.EncodedSize()
	if size > MaxDocumentBytes {
		return fmt.Errorf(
			"%s: record %q is %d bytes, over the %d-byte per-document limit: %w",
			a.Origin, doc.Source, size, MaxDocumentBytes, ErrDocumentTooLarge)
	}

	if len(a.docs)+1 > limit {
		return fmt.Errorf(
			"%s: holds more than %d documents, the most the API accepts in one request: %w -- "+
				"narrow the query so it returns fewer",
			a.Origin, limit, ErrCorpusTooLarge)
	}

	// The envelope (question, flags, the JSON array brackets) also counts against the request
	// cap, so the documents alone are held below it rather than exactly at it.
	if a.bytes+size > MaxRequestBytes-requestEnvelopeReserve {
		return fmt.Errorf(
			"%s: the documents read so far total %d bytes, and the API caps a request body at "+
				"%d bytes: %w -- narrow the query so it returns fewer or smaller records",
			a.Origin, a.bytes+size, MaxRequestBytes, ErrCorpusTooLarge)
	}

	a.docs = append(a.docs, doc)
	a.bytes += size
	return nil
}

// requestEnvelopeReserve holds back room for everything in the request body that is not a
// document: the question, the boolean flags, the field names around the array. 8 KiB is far more
// than those need, and costs only a corpus sitting within 8 KiB of an unsendable size.
const requestEnvelopeReserve = 8 * 1024

// Len reports how many documents have been accepted so far.
func (a *Accumulator) Len() int { return len(a.docs) }

// Documents returns what was collected.
func (a *Accumulator) Documents() []Document { return a.docs }

// CheckPayload re-verifies a finished document set against the server's limits.
//
// The Accumulator already enforces these while reading, so this is a backstop for a caller that
// assembled documents some other way, and the single place a request is proven sendable before
// it goes out (see horizonclient). It measures the documents as they will actually be encoded,
// rather than trusting a running total.
func CheckPayload(docs []Document, envelopeBytes int) error {
	if len(docs) > MaxDocuments {
		return fmt.Errorf(
			"%d documents exceeds the API's %d-document limit: %w",
			len(docs), MaxDocuments, ErrCorpusTooLarge)
	}

	total := envelopeBytes
	for _, doc := range docs {
		size := doc.EncodedSize()
		if size > MaxDocumentBytes {
			return fmt.Errorf(
				"record %q is %d bytes, over the %d-byte per-document limit: %w",
				doc.Source, size, MaxDocumentBytes, ErrDocumentTooLarge)
		}
		total += size
	}

	if total > MaxRequestBytes {
		return fmt.Errorf(
			"the request body would be %d bytes, over the API's %d-byte limit: %w -- "+
				"narrow the query so it returns fewer or smaller records",
			total, MaxRequestBytes, ErrCorpusTooLarge)
	}
	return nil
}
