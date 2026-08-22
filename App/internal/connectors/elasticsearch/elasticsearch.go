// Package elasticsearch implements connectors.Connector against a real Elasticsearch (or
// OpenSearch, which speaks the same client protocol) cluster, the same backend
// HorizonAI Engine/examples/elasticsearch_documents_example.py queries. There is no pure-Go/
// no-server stand-in here on purpose -- same reasoning as the Python example: this connector
// requires a real cluster and will not start one for you.
package elasticsearch

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"

	"github.com/elastic/go-elasticsearch/v8"

	"horizonmemory/connector/internal/connectors"
)

func init() {
	connectors.Register("elasticsearch", New)
}

const defaultIndex = "articles"

type Connector struct {
	client *elasticsearch.Client
	index  string
}

// New builds an Elasticsearch connector from opts/env: url (ELASTICSEARCH_URL, required, e.g.
// "http://localhost:9200"), index (ELASTICSEARCH_INDEX, default "articles") -- the same
// variable elasticsearch_documents_example.py reads.
func New(ctx context.Context, opts connectors.Options) (connectors.Connector, error) {
	url := opts.Get("url", "ELASTICSEARCH_URL", "")
	if url == "" {
		return nil, fmt.Errorf(
			"elasticsearch: a URL is required, e.g.\n" +
				`  ELASTICSEARCH_URL="http://localhost:9200"`,
		)
	}

	client, err := elasticsearch.NewClient(elasticsearch.Config{Addresses: []string{url}})
	if err != nil {
		return nil, fmt.Errorf("elasticsearch: building client: %w", err)
	}

	pingResp, err := client.Ping(client.Ping.WithContext(ctx))
	if err != nil {
		return nil, fmt.Errorf("elasticsearch: ping: %w", err)
	}
	defer pingResp.Body.Close()
	if pingResp.IsError() {
		return nil, fmt.Errorf("elasticsearch: ping returned %s", pingResp.Status())
	}

	index := opts.Get("index", "ELASTICSEARCH_INDEX", defaultIndex)

	return &Connector{client: client, index: index}, nil
}

func (c *Connector) Name() string { return "elasticsearch" }

// FetchDocuments runs a match_all search over the configured index (size 1000, matching
// elasticsearch_documents_example.py) and returns each hit's "body" source field as one
// document, in the order the cluster returns hits (the Python example does not re-sort hits
// either).
func (c *Connector) FetchDocuments(ctx context.Context) ([]string, error) {
	var body bytes.Buffer
	if err := json.NewEncoder(&body).Encode(map[string]any{
		"query": map[string]any{"match_all": map[string]any{}},
		"size":  1000,
	}); err != nil {
		return nil, fmt.Errorf("elasticsearch: encoding query: %w", err)
	}

	resp, err := c.client.Search(
		c.client.Search.WithContext(ctx),
		c.client.Search.WithIndex(c.index),
		c.client.Search.WithBody(&body),
	)
	if err != nil {
		return nil, fmt.Errorf("elasticsearch: search: %w", err)
	}
	defer resp.Body.Close()
	if resp.IsError() {
		return nil, fmt.Errorf("elasticsearch: search returned %s: %s", resp.Status(), readAll(resp.Body))
	}

	var parsed struct {
		Hits struct {
			Hits []struct {
				Source struct {
					Body string `json:"body"`
				} `json:"_source"`
			} `json:"hits"`
		} `json:"hits"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&parsed); err != nil {
		return nil, fmt.Errorf("elasticsearch: decoding response: %w", err)
	}

	documents := make([]string, 0, len(parsed.Hits.Hits))
	for _, hit := range parsed.Hits.Hits {
		documents = append(documents, hit.Source.Body)
	}
	return documents, nil
}

func (c *Connector) Close() error {
	return nil
}

func readAll(r io.Reader) string {
	data, _ := io.ReadAll(r)
	return string(data)
}
