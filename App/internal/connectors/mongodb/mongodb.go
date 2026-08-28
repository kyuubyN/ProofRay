// Package mongodb implements connectors.Connector against a real MongoDB instance, the same
// backend HorizonAI Engine/examples/mongodb_documents_example.py queries (that example also
// supports an in-process mongomock stand-in for CI; this Go connector always requires a real
// MONGODB_URI -- there is no equivalent pure-Go in-process mock wired up here).
package mongodb

import (
	"context"
	"fmt"

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

	client, err := mongo.Connect(ctx, options.Client().ApplyURI(uri))
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

	return &Connector{
		client:       client,
		collection:   client.Database(database).Collection(collectionName),
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

	session := fmt.Sprintf("mongodb:%s.%s", c.collection.Database().Name(), c.collection.Name())
	var documents []document.Document
	for cursor.Next(ctx) {
		var row struct {
			ID   any    `bson:"_id"`
			Body string `bson:"body"`
		}
		if err := cursor.Decode(&row); err != nil {
			return nil, fmt.Errorf("mongodb: decoding document: %w", err)
		}
		documents = append(documents, document.New(
			session, formatID(row.ID), row.Body, len(documents)))
		if len(documents) > c.maxDocuments {
			return nil, fmt.Errorf(
				"mongodb: collection %q holds more than %d documents: %w -- narrow the collection "+
					"or raise the ceiling with max_documents / HORIZON_MAX_DOCUMENTS",
				c.collection.Name(), c.maxDocuments, connectors.ErrCorpusTooLarge)
		}
	}
	if err := cursor.Err(); err != nil {
		return nil, fmt.Errorf("mongodb: reading cursor: %w", err)
	}
	return documents, nil
}

func (c *Connector) Close() error {
	return c.client.Disconnect(context.Background())
}

// formatID renders a record's _id as the string a reader would use to look it up again.
//
// _id is an ObjectID by default but may be any BSON type in a user's own collection. A plain %v
// on an ObjectID prints `ObjectID("6a91...")` -- quotes and wrapper included -- which is not
// something you can paste into a query, and embeds quote characters in the document's `source`.
// The hex is the form every Mongo client and shell accepts, so ObjectIDs are unwrapped and
// everything else falls back to its default rendering.
func formatID(id any) string {
	if oid, ok := id.(primitive.ObjectID); ok {
		return oid.Hex()
	}
	return fmt.Sprintf("%v", id)
}
