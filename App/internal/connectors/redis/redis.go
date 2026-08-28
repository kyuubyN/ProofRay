// Package redis implements connectors.Connector against a real Redis instance, the same backend
// HorizonAI Engine/examples/redis_documents_example.py queries (that example also supports an
// in-process fakeredis stand-in for CI; this Go connector always requires a real REDIS_URL --
// there is no equivalent pure-Go in-process mock wired up here). Redis has no query language of
// its own, so "connect a database" here means: list the keys under a prefix, read each value,
// return it as one document -- same as the Python example.
package redis

import (
	"context"
	"fmt"
	"sort"

	"github.com/redis/go-redis/v9"

	"horizonmemory/connector/internal/connectors"
	"horizonmemory/connector/internal/document"
)

func init() {
	connectors.Register("redis", New)
}

const defaultKeyPrefix = "articles:"

type Connector struct {
	client       *redis.Client
	prefix       string
	maxDocuments int
}

// New builds a Redis connector from opts/env: url (REDIS_URL, required, e.g.
// "redis://localhost:6379/0"), prefix (REDIS_KEY_PREFIX, default "articles:") -- the same
// variables/prefix redis_documents_example.py reads.
func New(ctx context.Context, opts connectors.Options) (connectors.Connector, error) {
	url := opts.Get("url", "REDIS_URL", "")
	if url == "" {
		return nil, fmt.Errorf(
			"redis: a URL is required, e.g.\n" +
				`  REDIS_URL="redis://localhost:6379/0"`,
		)
	}

	options, err := redis.ParseURL(url)
	if err != nil {
		return nil, fmt.Errorf("redis: parsing URL: %w", err)
	}
	client := redis.NewClient(options)
	if err := client.Ping(ctx).Err(); err != nil {
		client.Close()
		return nil, fmt.Errorf("redis: ping: %w", err)
	}

	prefix := opts.Get("prefix", "REDIS_KEY_PREFIX", defaultKeyPrefix)

	maxDocuments, err := connectors.MaxDocuments(opts)
	if err != nil {
		client.Close()
		return nil, fmt.Errorf("redis: %w", err)
	}

	return &Connector{client: client, prefix: prefix, maxDocuments: maxDocuments}, nil
}

func (c *Connector) Name() string { return "redis" }

// FetchDocuments scans keys under the configured prefix (SCAN, not KEYS -- safe against a large
// keyspace) and GETs each value, the same pattern redis_documents_example.py's scan_iter loop
// follows. Keys are sorted for a stable, reproducible document order.
func (c *Connector) FetchDocuments(ctx context.Context) ([]document.Document, error) {
	var keys []string
	iter := c.client.Scan(ctx, 0, c.prefix+"*", 0).Iterator()
	for iter.Next(ctx) {
		keys = append(keys, iter.Val())
	}
	if err := iter.Err(); err != nil {
		return nil, fmt.Errorf("redis: scanning keys: %w", err)
	}
	sort.Strings(keys)

	if len(keys) > c.maxDocuments {
		return nil, fmt.Errorf(
			"redis: prefix %q matches more than %d keys: %w -- narrow the prefix or raise the "+
				"ceiling with max_documents / HORIZON_MAX_DOCUMENTS",
			c.prefix, c.maxDocuments, connectors.ErrCorpusTooLarge)
	}

	// The Redis key is already the record's full identity, and it carries the prefix itself --
	// so the session stays a bare "redis" rather than repeating the prefix, and `source` comes
	// out as "redis:articles:1": literally the key to GET.
	const session = "redis"
	documents := make([]document.Document, 0, len(keys))
	for _, key := range keys {
		value, err := c.client.Get(ctx, key).Result()
		if err == redis.Nil {
			continue
		}
		if err != nil {
			return nil, fmt.Errorf("redis: getting key %q: %w", key, err)
		}
		documents = append(documents, document.New(session, key, value, len(documents)))
	}
	return documents, nil
}

func (c *Connector) Close() error {
	return c.client.Close()
}
