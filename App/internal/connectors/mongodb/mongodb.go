// Package mongodb implements connectors.Connector against a real MongoDB instance, the same
// backend HorizonAI Engine/examples/mongodb_documents_example.py queries (that example also
// supports an in-process mongomock stand-in for CI; this Go connector always requires a real
// MONGODB_URI -- there is no equivalent pure-Go in-process mock wired up here).
package mongodb

import (
	"context"
	"fmt"
	"strings"

	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
	"go.mongodb.org/mongo-driver/mongo"
	"go.mongodb.org/mongo-driver/mongo/options"

	"horizonmemory/connector/internal/connectors"
	"horizonmemory/connector/internal/document"
)

func init() {
	connectors.Register("mongodb", New)
}

const (
	defaultDatabase   = "support_kb"
	defaultCollection = "articles"
)

type Connector struct {
	client       *mongo.Client
	collection   *mongo.Collection
	source       string
	maxDocuments int
}

// New builds a MongoDB connector from opts/env: uri (MONGODB_URI, required), database
// (MONGODB_DATABASE, default "support_kb"), collection (MONGODB_COLLECTION, default
// "articles") -- the database/collection names mongodb_documents_example.py's fixture uses.
func New(ctx context.Context, opts connectors.Options) (connectors.Connector, error) {
	uri := opts.Get("uri", "MONGODB_URI", "")
	if uri == "" {
		return nil, fmt.Errorf(
			"mongodb: a URI is required, e.g.\n" +
				`  MONGODB_URI="mongodb://localhost:27017"`,
		)
	}

	clientOptions := options.Client().ApplyURI(uri)
	client, err := mongo.Connect(ctx, clientOptions)
	if err != nil {
		return nil, fmt.Errorf("mongodb: connecting: %w", err)
	}
	if err := client.Ping(ctx, nil); err != nil {
		_ = client.Disconnect(ctx)
		return nil, fmt.Errorf("mongodb: ping: %w", err)
	}

	database := opts.Get("database", "MONGODB_DATABASE", defaultDatabase)
	collectionName := opts.Get("collection", "MONGODB_COLLECTION", defaultCollection)

	maxDocuments, err := connectors.MaxDocuments(opts)
	if err != nil {
		client.Disconnect(ctx)
		return nil, fmt.Errorf("mongodb: %w", err)
	}

	// Hosts come from the parsed options, not the URI string: the URI carries credentials, and
	// this value is rendered in the web UI and sent to the API in every document.
	hosts := strings.Join(clientOptions.Hosts, ",")

	return &Connector{
		client:       client,
		collection:   client.Database(database).Collection(collectionName),
		source:       fmt.Sprintf("mongodb:%s/%s.%s", hosts, database, collectionName),
		maxDocuments: maxDocuments,
	}, nil
}

func (c *Connector) Name() string { return "mongodb" }

// FetchDocuments runs Find({}) projected to _id and body, sorted by _id ascending -- the same
// query mongodb_documents_example.py runs -- and returns each record as one document. The _id is
// kept rather than discarded: it becomes the document's identity, so a verified claim can be
// traced back to the record that produced it.
func (c *Connector) FetchDocuments(ctx context.Context) ([]document.Document, error) {
	findOptions := options.Find().
		SetProjection(bson.D{{Key: "_id", Value: 1}, {Key: "body", Value: 1}}).
		SetSort(bson.D{{Key: "_id", Value: 1}})

	cursor, err := c.collection.Find(ctx, bson.D{}, findOptions)
	if err != nil {
		return nil, fmt.Errorf("mongodb: find: %w", err)
	}
	defer cursor.Close(ctx)

	acc := &document.Accumulator{
		Origin:       fmt.Sprintf("mongodb: collection %q", c.collection.Name()),
		MaxDocuments: c.maxDocuments,
	}
	for cursor.Next(ctx) {
		var row struct {
			ID   any    `bson:"_id"`
			Body string `bson:"body"`
		}
		if err := cursor.Decode(&row); err != nil {
			return nil, fmt.Errorf("mongodb: decoding document: %w", err)
		}
		if err := acc.Add(document.New(
			c.source, formatID(row.ID), row.Body, acc.Len())); err != nil {
			return nil, err
		}
	}
	if err := cursor.Err(); err != nil {
		return nil, fmt.Errorf("mongodb: reading cursor: %w", err)
	}
	return acc.Documents(), nil
}

func (c *Connector) Close() error {
	return c.client.Disconnect(context.Background())
}

// formatID renders a record's _id as a string that is both readable and unambiguous.
//
// _id is an ObjectID by default but may be any BSON type in a user's own collection, and Mongo
// treats {_id: 42} and {_id: "42"} as two distinct records. Rendering both with %v yields "42"
// for each, giving them the same source and the same fact_id -- the server then rejects the whole
// corpus for duplicate fact_ids, on a collection that is perfectly valid. So every type except
// ObjectID is tagged with its type name.
//
// ObjectID is left bare (its hex is already unambiguous, and it is the form every Mongo client
// and shell accepts) rather than tagged, since it is the overwhelmingly common case and a plain
// %v on it would print `ObjectID("6a91...")` -- quotes included -- into the document's source.
func formatID(id any) string {
	switch value := id.(type) {
	case primitive.ObjectID:
		return value.Hex()
	case string:
		return "string(" + value + ")"
	default:
		return fmt.Sprintf("%T(%v)", id, id)
	}
}
