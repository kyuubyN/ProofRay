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
	neturl "net/url"
	"time"

	"github.com/elastic/go-elasticsearch/v8"
	"github.com/elastic/go-elasticsearch/v8/esapi"

	"horizonmemory/connector/internal/connectors"
	"horizonmemory/connector/internal/document"
)

func init() {
	connectors.Register("elasticsearch", New)
}

const defaultIndex = "articles"

type Connector struct {
	client       *elasticsearch.Client
	index        string
	source       string
	maxDocuments int
}

// scrollPageSize is how many hits each scroll page carries. The index is walked page by page
// regardless, so this only trades round trips against per-response size.
const scrollPageSize = 1000

// scrollKeepAlive is how long the cluster holds the scroll cursor open between pages. It is a
// per-page idle timeout, not a budget for the whole walk, so it does not cap total fetch time.
const scrollKeepAlive = 2 * time.Minute

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

	maxDocuments, err := connectors.MaxDocuments(opts)
	if err != nil {
		return nil, fmt.Errorf("elasticsearch: %w", err)
	}

	// Host and port only: a URL may carry credentials in its userinfo, and this value is
	// rendered in the web UI and sent to the API in every document.
	return &Connector{
		client:       client,
		index:        index,
		source:       fmt.Sprintf("elasticsearch:%s/%s", hostOf(url), index),
		maxDocuments: maxDocuments,
	}, nil
}

// hostOf reduces a cluster URL to host:port, dropping any user:password in its userinfo.
func hostOf(raw string) string {
	parsed, err := neturl.Parse(raw)
	if err != nil || parsed.Host == "" {
		return "unknown-host"
	}
	return parsed.Host
}

func (c *Connector) Name() string { return "elasticsearch" }

// scrollPage is one page of a scrolling match_all search: the cursor to ask for the next page,
// plus this page's hits.
type scrollPage struct {
	ScrollID string `json:"_scroll_id"`
	Hits     struct {
		Hits []struct {
			// _id is the document's identity in the cluster -- kept so a verified claim can be
			// reopened against the exact document it came from.
			ID     string `json:"_id"`
			Source struct {
				Body string `json:"body"`
			} `json:"_source"`
		} `json:"hits"`
	} `json:"hits"`
}

// FetchDocuments walks the whole configured index with a match_all search and returns each hit's
// "body" source field as one document.
//
// A plain search returns at most `size` hits (and the cluster caps that at index.max_result_window,
// 10000 by default), so a single request cannot read an index of arbitrary size. This scrolls
// through every page instead: returning only the first page would hand Horizon a partial corpus
// with no indication anything was left behind, and an answer composed from a silently truncated
// corpus is exactly the unearned confidence this project refuses elsewhere. An index larger than
// the ceiling fails with ErrCorpusTooLarge rather than returning what happened to fit.
//
// Hits come back in whatever order the cluster returns them (sorted by _doc, the cheapest scroll
// order) -- elasticsearch_documents_example.py does not re-sort hits either.
func (c *Connector) FetchDocuments(ctx context.Context) ([]document.Document, error) {
	var body bytes.Buffer
	if err := json.NewEncoder(&body).Encode(map[string]any{
		"query": map[string]any{"match_all": map[string]any{}},
		"sort":  []any{"_doc"},
	}); err != nil {
		return nil, fmt.Errorf("elasticsearch: encoding query: %w", err)
	}

	resp, err := c.client.Search(
		c.client.Search.WithContext(ctx),
		c.client.Search.WithIndex(c.index),
		c.client.Search.WithBody(&body),
		c.client.Search.WithSize(scrollPageSize),
		c.client.Search.WithScroll(scrollKeepAlive),
	)
	if err != nil {
		return nil, fmt.Errorf("elasticsearch: search: %w", err)
	}

	page, err := decodePage(resp)
	if err != nil {
		return nil, err
	}

	// Release the cursor on every exit path once we have one; the cluster would otherwise hold
	// the scroll context until keep-alive expires.
	scrollID := page.ScrollID
	defer func() {
		if scrollID != "" {
			c.clearScroll(scrollID)
		}
	}()

	acc := &document.Accumulator{
		Origin:       fmt.Sprintf("elasticsearch: index %q", c.index),
		MaxDocuments: c.maxDocuments,
	}
	for {
		// Adopt this page's cursor before processing its hits: the cluster may return a new
		// scroll ID per page, and an error raised mid-page would otherwise leave the deferred
		// cleanup holding the previous page's ID while the current one leaks until keep-alive
		// expires.
		if page.ScrollID != "" {
			scrollID = page.ScrollID
		}

		for _, hit := range page.Hits.Hits {
			if err := acc.Add(document.New(
				c.source, hit.ID, hit.Source.Body, acc.Len())); err != nil {
				return nil, err
			}
		}

		// A page with no hits means the scroll is exhausted.
		if len(page.Hits.Hits) == 0 {
			break
		}
		if scrollID == "" {
			return nil, fmt.Errorf("elasticsearch: scroll cursor missing before the index was exhausted")
		}

		next, err := c.client.Scroll(
			c.client.Scroll.WithContext(ctx),
			c.client.Scroll.WithScrollID(scrollID),
			c.client.Scroll.WithScroll(scrollKeepAlive),
		)
		if err != nil {
			return nil, fmt.Errorf("elasticsearch: scroll: %w", err)
		}
		if page, err = decodePage(next); err != nil {
			return nil, err
		}
	}

	return acc.Documents(), nil
}

// clearScroll releases a scroll cursor. Failing to clear it is not fatal to a fetch that already
// succeeded -- the cursor expires on its own -- so this reports nothing upward.
func (c *Connector) clearScroll(scrollID string) {
	resp, err := c.client.ClearScroll(c.client.ClearScroll.WithScrollID(scrollID))
	if err != nil {
		return
	}
	resp.Body.Close()
}

// decodePage turns one search/scroll response into a scrollPage, always closing the body.
func decodePage(resp *esapi.Response) (scrollPage, error) {
	defer resp.Body.Close()
	if resp.IsError() {
		return scrollPage{}, fmt.Errorf(
			"elasticsearch: search returned %s: %s", resp.Status(), readAll(resp.Body))
	}
	var page scrollPage
	if err := json.NewDecoder(resp.Body).Decode(&page); err != nil {
		return scrollPage{}, fmt.Errorf("elasticsearch: decoding response: %w", err)
	}
	return page, nil
}

func (c *Connector) Close() error {
	return nil
}

func readAll(r io.Reader) string {
	data, _ := io.ReadAll(r)
	return string(data)
}
