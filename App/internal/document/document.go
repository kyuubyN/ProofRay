// Package document defines the unit a Connector produces and horizonclient ships to
// POST /v1/answers.
//
// HorizonAPI accepts two representations (see api/_engine_bridge.py's build_documents): a legacy
// `[]string`, and a structured object carrying the document's identity. The legacy form makes the
// server invent an identity -- fact_id becomes the array position, source becomes "doc:N" -- so
// every trace of where a claim actually came from is gone by the time the engine verifies it.
// A row that moves position between two fetches silently changes identity, and an answer's
// provenance cannot be reopened against the database that produced it.
//
// This package exists so connectors emit the structured form instead: the fact_id is derived from
// the record's real primary key, and `source` names the row well enough to go read it again.
// It lives in its own package because both connectors (which produce documents) and horizonclient
// (which serializes them) need the type, and neither should have to import the other.
package document

import (
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
)

// Scope is the only scope api/_engine_bridge.py authorizes (SCOPE_ID = 1); a document carrying
// any other value is rejected by the server.
const Scope = 1

// The server's own limits, mirrored here so a corpus that cannot be sent is rejected before the
// work of fetching it, rather than as an HTTP 413 after. Raising any of these without raising
// the matching value in api/ would just move the failure back to the server.
const (
	// MaxDocuments mirrors MAX_DOCUMENTS in api/_engine_bridge.py.
	MaxDocuments = 2000

	// MaxDocumentBytes mirrors MAX_DOCUMENT_BYTES in api/_engine_bridge.py.
	MaxDocumentBytes = 64 * 1024

	// MaxRequestBytes mirrors app.config["MAX_CONTENT_LENGTH"] in api/server.py, above which
	// Werkzeug returns 413 before any handler runs. This is the binding limit in practice:
	// MaxDocuments documents at MaxDocumentBytes each would be 128 MiB, so the document count
	// alone never establishes that a corpus is sendable.
	MaxRequestBytes = 1024 * 1024
)

// maxFactID mirrors api/_engine_bridge.py's MAX_FACT_ID (1 << 62), which the server enforces as
// an exclusive upper bound.
const maxFactID = uint64(1<<62) - 1

// Document is one document plus the provenance needed to reopen it. Field names and types match
// the structured schema api/_engine_bridge.py validates; the server rejects unknown fields, so
// nothing may be added here without adding it there first.
type Document struct {
	// FactID is this record's stable identity, derived from its primary key (see New). The
	// server requires these to be unique within one request.
	FactID int64 `json:"fact_id"`

	// Text is the authoritative content. Metadata is never prefixed or appended to it.
	Text string `json:"text"`

	// Source names the originating record precisely enough to read it again, e.g.
	// "postgres:db.internal:5432/prod/public/articles:42" or
	// "mongodb:db.internal:27017/horizon.articles:507f1f77bcf86cd799439011".
	Source string `json:"source"`

	Scope   int    `json:"scope"`
	Session string `json:"session"`
	Version int    `json:"version"`

	// Sequence is the record's position in this fetch, preserving read order independently of
	// however the server ends up ordering documents.
	Sequence *int `json:"sequence,omitempty"`

	// EventTime is the record's own timestamp (Unix seconds) when the backend carries one.
	//
	// NOT POPULATED YET. No connector fills this in: the schemas these connectors mirror
	// (`articles(id, body)`) have no timestamp column, and which column should supply it is a
	// schema decision, not something to guess at per backend. The field and WithEventTime exist
	// so that decision does not require reshaping the type.
	EventTime *int64 `json:"event_time,omitempty"`

	// NOTE: the server's schema also accepts `span`, `role` and `speaker`, which this type does
	// not carry. `span` marks a subrange of a larger source text; here each record IS one whole
	// document, so there is no enclosing text to offset into -- the spans that appear in an
	// answer's `source` are computed by the engine during verification, not supplied here.
	// `role`/`speaker` describe conversation turns, which a database row is not.

	// TextSHA256 lets the server verify the text arrived intact -- api/_engine_bridge.py
	// recomputes it and rejects a mismatch, so this is an end-to-end integrity check across the
	// connector, the network hop, and the JSON encoding.
	TextSHA256 string `json:"text_sha256,omitempty"`
}

// New builds a Document from a backend's own identifiers.
//
// source identifies the physical origin, precisely enough to tell two of them apart: it must
// carry the host/port and backend namespace (database/schema/table, or absolute file path) the
// record was read from, not just the backend name -- see each connector's source-building code.
// primaryKey is the record's key within it, in whatever form the backend uses: an integer id, a
// Mongo ObjectID, a Redis key.
//
// FactID is derived from source+primaryKey rather than from the record's position, so the same
// row keeps the same identity across fetches even when rows are inserted, deleted, or returned
// in a different order -- and rows from two different servers that happen to share a key stay
// distinct.
func New(source, primaryKey, text string, sequence int) Document {
	digest := sha256.Sum256([]byte(text))
	return Document{
		FactID:     factID(source, primaryKey),
		Text:       text,
		Source:     fmt.Sprintf("%s:%s", source, primaryKey),
		Scope:      Scope,
		Session:    source,
		Version:    1,
		Sequence:   &sequence,
		TextSHA256: hex.EncodeToString(digest[:]),
	}
}

// WithEventTime returns a copy carrying the record's own timestamp. Currently unused -- see the
// note on the EventTime field.
func (d Document) WithEventTime(unixSeconds int64) Document {
	d.EventTime = &unixSeconds
	return d
}

// factID maps a record's key into the server's identity domain.
//
// The schema requires an integer fact_id, but most backends here have non-integer keys (Mongo
// ObjectIDs, Redis keys, DynamoDB string ids), so a hash is the only way to carry a real key into
// an integer field. SHA-256 truncated to the server's 62-bit domain: at that width a birthday
// collision needs on the order of 2^31 records in one corpus, far beyond the 2000-document
// ceiling, and unlike a non-cryptographic hash it is not practical to construct two keys that
// collide on purpose.
//
// The source is mixed in with a length prefix rather than a plain separator, so no combination of
// source and key can be re-split to produce another pair's input -- a separator byte alone is
// ambiguous for any key that may itself contain that byte (a Redis key contains ':' by
// convention). If two records in one fetch ever do collide, the accumulator/client rejects the
// payload before sending it rather than letting the server merge or reject it later.
func factID(source, primaryKey string) int64 {
	h := sha256.New()
	var length [8]byte
	binary.BigEndian.PutUint64(length[:], uint64(len(source)))
	h.Write(length[:])
	h.Write([]byte(source))
	h.Write([]byte(primaryKey))
	sum := h.Sum(nil)
	return int64(binary.BigEndian.Uint64(sum[:8]) & maxFactID)
}

// EncodedSize reports how many bytes this document contributes to the request body, including
// the comma that separates it from the next one.
func (d Document) EncodedSize() int {
	encoded, err := json.Marshal(d)
	if err != nil {
		return 0
	}
	return len(encoded) + 1
}
