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
	source       string
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

	// From the parsed options, never the URL string, which may carry a password.
	redisOptions := client.Options()

	return &Connector{
		client:       client,
		prefix:       prefix,
		source:       fmt.Sprintf("redis:%s/%d", redisOptions.Addr, redisOptions.DB),
		maxDocuments: maxDocuments,
	}, nil
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

	// The Redis key already carries the prefix, so it is used whole as the record key -- the
	// resulting `source` ends in literally the key to GET.
	acc := &document.Accumulator{
		Origin:       fmt.Sprintf("redis: prefix %q", c.prefix),
		MaxDocuments: c.maxDocuments,
	}
	for _, key := range keys {
		value, err := c.client.Get(ctx, key).Result()
		if err == redis.Nil {
			continue
		}
		if err != nil {
			return nil, fmt.Errorf("redis: getting key %q: %w", key, err)
		}
		if err := acc.Add(document.New(c.source, key, value, acc.Len())); err != nil {
			return nil, err
		}
	}
	return acc.Documents(), nil
}

func (c *Connector) Close() error {
	return c.client.Close()
}
