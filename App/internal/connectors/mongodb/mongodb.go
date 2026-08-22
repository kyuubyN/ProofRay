// Package mongodb implements connectors.Connector against a real MongoDB instance, the same
// backend HorizonAI Engine/examples/mongodb_documents_example.py queries (that example also
// supports an in-process mongomock stand-in for CI; this Go connector always requires a real
// MONGODB_URI -- there is no equivalent pure-Go in-process mock wired up here).
package mongodb

import (
	"context"
	"fmt"

	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/mongo"
	"go.mongodb.org/mongo-driver/mongo/options"

	"horizonmemory/connector/internal/connectors"
)

func init() {
	connectors.Register("mongodb", New)
}

const (
	defaultDatabase   = "support_kb"
	defaultCollection = "articles"
)

type Connector struct {
	client     *mongo.Client
	collection *mongo.Collection
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

	return &Connector{
		client:     client,
		collection: client.Database(database).Collection(collectionName),
	}, nil
}

func (c *Connector) Name() string { return "mongodb" }

// FetchDocuments runs Find({}) projected to just body, sorted by _id ascending -- the same
// query mongodb_documents_example.py runs -- and returns each document's body as one document.
func (c *Connector) FetchDocuments(ctx context.Context) ([]string, error) {
	findOptions := options.Find().
		SetProjection(bson.D{{Key: "_id", Value: 1}, {Key: "body", Value: 1}}).
		SetSort(bson.D{{Key: "_id", Value: 1}})

	cursor, err := c.collection.Find(ctx, bson.D{}, findOptions)
	if err != nil {
		return nil, fmt.Errorf("mongodb: find: %w", err)
	}
	defer cursor.Close(ctx)

	var documents []string
	for cursor.Next(ctx) {
		var row struct {
			Body string `bson:"body"`
		}
		if err := cursor.Decode(&row); err != nil {
			return nil, fmt.Errorf("mongodb: decoding document: %w", err)
		}
		documents = append(documents, row.Body)
	}
	if err := cursor.Err(); err != nil {
		return nil, fmt.Errorf("mongodb: reading cursor: %w", err)
	}
	return documents, nil
}

func (c *Connector) Close() error {
	return c.client.Disconnect(context.Background())
}
