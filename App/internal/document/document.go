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
	"encoding/hex"
	"fmt"
	"hash/fnv"
)

// Scope is the only scope api/_engine_bridge.py authorizes (SCOPE_ID = 1); a document carrying
// any other value is rejected by the server.
const Scope = 1

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
	// "postgres:articles:42" or "mongodb:horizon.articles:507f1f77bcf86cd799439011".
	Source string `json:"source"`

	Scope   int    `json:"scope"`
	Session string `json:"session"`
	Version int    `json:"version"`

	// Sequence is the record's position in this fetch, preserving read order independently of
	// however the server ends up ordering documents.
	Sequence *int `json:"sequence,omitempty"`

	// EventTime is the record's own timestamp (Unix seconds) when the backend carries one.
	EventTime *int64 `json:"event_time,omitempty"`

	// TextSHA256 lets the server verify the text arrived intact -- api/_engine_bridge.py
	// recomputes it and rejects a mismatch, so this is an end-to-end integrity check across the
	// connector, the network hop, and the JSON encoding.
	TextSHA256 string `json:"text_sha256,omitempty"`
}

// New builds a Document from a backend's own identifiers.
//
// session identifies the corpus being read (e.g. "postgres:articles"); primaryKey is the
// record's key within it, in whatever form the backend uses -- an integer id, a Mongo ObjectID,
// a Redis key. FactID is derived from session+primaryKey rather than from the record's position,
// so the same row keeps the same identity across fetches even when rows are inserted, deleted, or
// returned in a different order.
func New(session, primaryKey, text string, sequence int) Document {
	digest := sha256.Sum256([]byte(text))
	return Document{
		FactID:     factID(session, primaryKey),
		Text:       text,
		Source:     fmt.Sprintf("%s:%s", session, primaryKey),
		Scope:      Scope,
		Session:    session,
		Version:    1,
		Sequence:   &sequence,
		TextSHA256: hex.EncodeToString(digest[:]),
	}
}

// WithEventTime returns a copy carrying the record's own timestamp.
func (d Document) WithEventTime(unixSeconds int64) Document {
	d.EventTime = &unixSeconds
	return d
}

// factID hashes a record's key into the server's identity domain.
//
// The schema requires an integer fact_id, but most backends here have non-integer keys (Mongo
// ObjectIDs, Redis keys, DynamoDB string ids), so a hash is the only way to carry a real key into
// an integer field. FNV-1a is chosen for stability and speed, not collision resistance: this is an
// identity mapping, not a security boundary. The session is mixed in so two backends sharing a key
// ("42") do not collide with each other. If two records in one fetch ever do collide, the server
// rejects the whole request ("structured documents require unique fact_id values") rather than
// silently merging them -- a loud failure, not a wrong answer.
func factID(session, primaryKey string) int64 {
	h := fnv.New64a()
	h.Write([]byte(session))
	h.Write([]byte{0}) // separator, so ("ab","c") and ("a","bc") differ
	h.Write([]byte(primaryKey))
	return int64(h.Sum64() & maxFactID)
}
