// Package dynamodb implements connectors.Connector against a real (or DynamoDB Local) table,
// the same backend HorizonAI Engine/examples/dynamodb_documents_example.py queries in its
// `DYNAMODB_USE_REAL_AWS=1` mode (that example also supports an in-process moto mock for CI;
// this Go connector always talks to a real endpoint -- DynamoDB Local, started with `docker run
// -p 8000:8000 amazon/dynamodb-local`, is that "real endpoint" for local testing, reached via
// the endpoint override below).
package dynamodb

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net/url"
	"sort"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/feature/dynamodb/attributevalue"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb/types"

	"horizonmemory/connector/internal/connectors"
	"horizonmemory/connector/internal/document"
)

func init() {
	connectors.Register("dynamodb", New)
}

const defaultTable = "articles"

type Connector struct {
	client       *dynamodb.Client
	table        string
	source       string
	maxDocuments int
}

type item struct {
	ID   string `dynamodbav:"id"`
	Body string `dynamodbav:"body"`
}

// New builds a DynamoDB connector from opts/env: table (DYNAMODB_TABLE, default "articles"),
// region (AWS_DEFAULT_REGION, required, matching dynamodb_documents_example.py's real-AWS mode),
// and an optional endpoint (DYNAMODB_ENDPOINT_URL) to point at DynamoDB Local instead of real
// AWS -- not a variable the Python example reads (it only ever mocks in-process via moto or
// hits real AWS), but the natural Go-side equivalent for pointing this connector at a local
// table without a real AWS account. Credentials come from the standard AWS SDK chain
// (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY env vars, a shared config file, etc.) -- DynamoDB
// Local accepts any non-empty credentials.
func New(ctx context.Context, opts connectors.Options) (connectors.Connector, error) {
	region := opts.Get("region", "AWS_DEFAULT_REGION", "")
	if region == "" {
		return nil, fmt.Errorf(
			"dynamodb: a region is required, e.g.\n" +
				`  AWS_DEFAULT_REGION=us-east-1`,
		)
	}

	cfg, err := config.LoadDefaultConfig(ctx, config.WithRegion(region))
	if err != nil {
		return nil, fmt.Errorf("dynamodb: loading AWS config: %w", err)
	}

	endpoint := opts.Get("endpoint", "DYNAMODB_ENDPOINT_URL", "")
	client := dynamodb.NewFromConfig(cfg, func(o *dynamodb.Options) {
		if endpoint != "" {
			o.BaseEndpoint = aws.String(endpoint)
		}
	})

	table := opts.Get("table", "DYNAMODB_TABLE", defaultTable)

	maxDocuments, err := connectors.MaxDocuments(opts)
	if err != nil {
		return nil, fmt.Errorf("dynamodb: %w", err)
	}

	described, err := client.DescribeTable(ctx, &dynamodb.DescribeTableInput{TableName: aws.String(table)})
	if err != nil {
		return nil, fmt.Errorf("dynamodb: describing table %q: %w", table, err)
	}

	// Prefer the table's ARN, which names the AWS account as well as the region and table --
	// region+table alone is identical across two accounts, so rows with the same key in each
	// would share a fact_id. DynamoDB Local returns an ARN with a dummy account, so the endpoint
	// is appended there to keep two local instances apart. Endpoint userinfo/query/fragment are
	// stripped below so no credential appears in either form.
	origin := region
	if described.Table != nil && described.Table.TableArn != nil {
		origin = *described.Table.TableArn
	}
	if endpoint != "" {
		origin = fmt.Sprintf("%s@%s", origin, endpointIdentity(endpoint))
	}

	return &Connector{
		client:       client,
		table:        table,
		source:       fmt.Sprintf("dynamodb:%s", origin),
		maxDocuments: maxDocuments,
	}, nil
}

// endpointIdentity keeps DynamoDB Local instances distinct without putting credentials into
// document metadata. Userinfo, query parameters and fragments are configuration, not the
// physical network endpoint, and are common places for secrets. If the endpoint is malformed,
// retain a stable distinction through a digest instead of falling back to the sensitive input.
func endpointIdentity(raw string) string {
	parsed, err := url.Parse(raw)
	if err != nil || parsed.Scheme == "" || parsed.Host == "" {
		return hashedEndpointIdentity("invalid-endpoint-sha256:", raw)
	}
	parsed.User = nil
	parsed.RawQuery = ""
	parsed.ForceQuery = false
	parsed.Fragment = ""
	sanitized := parsed.String()
	// Leave ample room under the API's 4 KiB source/session ceiling for the table ARN and key.
	// A pathological URL path remains distinguishable without making every fetched row invalid.
	if len(sanitized) > 1024 {
		return hashedEndpointIdentity("endpoint-sha256:", sanitized)
	}
	return sanitized
}

func hashedEndpointIdentity(prefix, value string) string {
	digest := sha256.Sum256([]byte(value))
	return prefix + hex.EncodeToString(digest[:])
}

func (c *Connector) Name() string { return "dynamodb" }

// FetchDocuments Scans the whole table, sorts items by id (matching
// dynamodb_documents_example.py's `sorted(response["Items"], key=lambda x: x["id"])`), and
// returns each item's body as one document.
//
// A Scan returns at most 1 MB per call, so a single call covers only the first page of any table
// larger than that. This follows LastEvaluatedKey until the table is exhausted -- stopping at the
// first page would hand Horizon a partial corpus while reporting nothing, and an answer composed
// from a silently truncated corpus is exactly the kind of unearned confidence this project
// refuses elsewhere. A table larger than the ceiling fails with ErrCorpusTooLarge rather than
// returning what happened to fit.
func (c *Connector) FetchDocuments(ctx context.Context) ([]document.Document, error) {
	var items []item
	var startKey map[string]types.AttributeValue
	budget := &document.Accumulator{
		Origin:       fmt.Sprintf("dynamodb: table %q", c.table),
		MaxDocuments: c.maxDocuments,
	}

	for {
		output, err := c.client.Scan(ctx, &dynamodb.ScanInput{
			TableName:         aws.String(c.table),
			ExclusiveStartKey: startKey,
		})
		if err != nil {
			return nil, fmt.Errorf("dynamodb: scan: %w", err)
		}

		for _, raw := range output.Items {
			var it item
			if err := attributevalue.UnmarshalMap(raw, &it); err != nil {
				return nil, fmt.Errorf("dynamodb: unmarshaling item: %w", err)
			}
			// Charged against the budget as it is read, so a table far past the request limit
			// stops being paged rather than being pulled down in full and rejected at the end.
			// The result is discarded: the documents are rebuilt below in sorted order, and only
			// the running total matters here.
			if err := budget.Add(document.New(c.source, it.ID, it.Body, budget.Len())); err != nil {
				return nil, err
			}
			items = append(items, it)
		}

		// An empty LastEvaluatedKey means this was the final page.
		if len(output.LastEvaluatedKey) == 0 {
			break
		}
		startKey = output.LastEvaluatedKey
	}

	// Sorted by id to match dynamodb_documents_example.py's
	// `sorted(response["Items"], key=lambda x: x["id"])`. Scan returns items in no useful order,
	// and the order is only known once every page has been read -- hence the rebuild.
	sort.Slice(items, func(i, j int) bool { return items[i].ID < items[j].ID })

	documents := make([]document.Document, 0, len(items))
	for _, it := range items {
		documents = append(documents, document.New(c.source, it.ID, it.Body, len(documents)))
	}
	return documents, nil
}

func (c *Connector) Close() error {
	return nil
}
