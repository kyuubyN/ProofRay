//go:build ignore

// Creates the "articles" table in DynamoDB Local and seeds it with the same "Meridian"/
// "Solstice" fixture testdata/seed.sh uses for the other backends -- there is no AWS CLI/curl
// shortcut for DynamoDB (requests need SigV4 signing), so this is a real Go program instead of a
// shell snippet. Excluded from the App module's own build (`//go:build ignore` above) since it's
// test fixture setup, not part of the connector.
//
// Run against docker-compose.yml's dynamodb service (after `docker compose up -d`):
//
//	cd App
//	AWS_ACCESS_KEY_ID=local AWS_SECRET_ACCESS_KEY=local go run testdata/seed_dynamodb.go
package main

import (
	"context"
	"fmt"
	"log"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb"
	"github.com/aws/aws-sdk-go-v2/service/dynamodb/types"
)

func main() {
	ctx := context.Background()
	cfg, err := config.LoadDefaultConfig(ctx, config.WithRegion("us-east-1"))
	if err != nil {
		log.Fatal(err)
	}
	client := dynamodb.NewFromConfig(cfg, func(o *dynamodb.Options) {
		o.BaseEndpoint = aws.String("http://127.0.0.1:8000")
	})

	_, err = client.CreateTable(ctx, &dynamodb.CreateTableInput{
		TableName: aws.String("articles"),
		KeySchema: []types.KeySchemaElement{
			{AttributeName: aws.String("id"), KeyType: types.KeyTypeHash},
		},
		AttributeDefinitions: []types.AttributeDefinition{
			{AttributeName: aws.String("id"), AttributeType: types.ScalarAttributeTypeS},
		},
		BillingMode: types.BillingModePayPerRequest,
	})
	if err != nil {
		log.Fatal("create table: ", err)
	}
	fmt.Println("table created")

	rows := []string{
		"The Meridian project reduced compute cost by exactly 42 percent compared to the previous baseline architecture across every workload.",
		"Meridian's cost reduction came from a redesigned caching layer that eliminated redundant recomputation across adjacent pipeline stages.",
		"The Solstice project, unrelated to Meridian, focuses on latency instead of cost.",
	}
	for i, body := range rows {
		_, err := client.PutItem(ctx, &dynamodb.PutItemInput{
			TableName: aws.String("articles"),
			Item: map[string]types.AttributeValue{
				"id":   &types.AttributeValueMemberS{Value: fmt.Sprintf("%d", i+1)},
				"body": &types.AttributeValueMemberS{Value: body},
			},
		})
		if err != nil {
			log.Fatal("put item: ", err)
		}
	}
	fmt.Println("seeded 3 items")
}
