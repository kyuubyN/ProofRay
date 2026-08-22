// Package dynamodb implements connectors.Connector against a real (or DynamoDB Local) table,
// the same backend HorizonAI Engine/examples/dynamodb_documents_example.py queries in its
// `DYNAMODB_USE_REAL_AWS=1` mode (that example also supports an in-process moto mock for CI;
// this Go connector always talks to a real endpoint -- DynamoDB Local, started with `docker run
// -p 8000:8000 amazon/dynamodb-local`, is that "real endpoint" for local testing, reached via
// the endpoint override below).
package dynamodb

import (
	"context"
	"fmt"
	"sort"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/feature/dynamodb/attributevalue"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"

	"horizonmemory/connector/internal/connectors"
)

func init() {
	connectors.Register("dynamodb", New)
}

const defaultTable = "articles"

type Connector struct {
	client *dynamodb.Client
	table  string
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

	if _, err := client.DescribeTable(ctx, &dynamodb.DescribeTableInput{TableName: aws.String(table)}); err != nil {
		return nil, fmt.Errorf("dynamodb: describing table %q: %w", table, err)
	}

	return &Connector{client: client, table: table}, nil
}

func (c *Connector) Name() string { return "dynamodb" }

// FetchDocuments runs a Scan over the whole table, the same operation
// dynamodb_documents_example.py runs, sorts items by id (matching the Python example's
// `sorted(response["Items"], key=lambda x: x["id"])`), and returns each item's body as one
// document.
func (c *Connector) FetchDocuments(ctx context.Context) ([]string, error) {
	output, err := c.client.Scan(ctx, &dynamodb.ScanInput{TableName: aws.String(c.table)})
	if err != nil {
		return nil, fmt.Errorf("dynamodb: scan: %w", err)
	}

	items := make([]item, 0, len(output.Items))
	for _, raw := range output.Items {
		var it item
		if err := attributevalue.UnmarshalMap(raw, &it); err != nil {
			return nil, fmt.Errorf("dynamodb: unmarshaling item: %w", err)
		}
		items = append(items, it)
	}
	sort.Slice(items, func(i, j int) bool { return items[i].ID < items[j].ID })

	documents := make([]string, 0, len(items))
	for _, it := range items {
		documents = append(documents, it.Body)
	}
	return documents, nil
}

func (c *Connector) Close() error {
	return nil
}
