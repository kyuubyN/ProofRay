#!/usr/bin/env bash
# Seeds the same fixture ("Meridian"/"Solstice", the exact rows
# HorizonAI Engine/examples/*_documents_example.py use) into every service started by
# docker-compose.yml, so App/README.md's example commands work against them unchanged.
# Run after `docker compose -f App/testdata/docker-compose.yml up -d` and once each service is
# ready (this script does not wait for readiness -- retry it if a container is still starting).
set -euo pipefail

MERIDIAN_1="The Meridian project reduced compute cost by exactly 42 percent compared to the previous baseline architecture across every workload."
MERIDIAN_2="Meridian's cost reduction came from a redesigned caching layer that eliminated redundant recomputation across adjacent pipeline stages."
SOLSTICE="The Solstice project, unrelated to Meridian, focuses on latency instead of cost."

echo "== postgres =="
docker exec -i "$(docker compose -f "$(dirname "$0")/docker-compose.yml" ps -q postgres)" \
  psql -U postgres -d horizon_example <<SQL
CREATE TABLE IF NOT EXISTS articles (id SERIAL PRIMARY KEY, body TEXT NOT NULL);
INSERT INTO articles (body) VALUES ('$MERIDIAN_1'), ('$MERIDIAN_2'), ('$SOLSTICE');
SQL

echo "== mysql =="
docker exec -i "$(docker compose -f "$(dirname "$0")/docker-compose.yml" ps -q mysql)" \
  mysql -uroot -phorizon horizon_example <<SQL
CREATE TABLE IF NOT EXISTS articles (id INT AUTO_INCREMENT PRIMARY KEY, body TEXT NOT NULL);
INSERT INTO articles (body) VALUES ('$MERIDIAN_1'), ('$MERIDIAN_2'), ('$SOLSTICE');
SQL

echo "== mongo =="
docker exec "$(docker compose -f "$(dirname "$0")/docker-compose.yml" ps -q mongo)" \
  mongosh support_kb --quiet --eval "
db.articles.insertMany([
  {body: '$MERIDIAN_1'},
  {body: '$MERIDIAN_2'},
  {body: '$SOLSTICE'}
]);
"

echo "== redis =="
REDIS_CONTAINER="$(docker compose -f "$(dirname "$0")/docker-compose.yml" ps -q redis)"
docker exec "$REDIS_CONTAINER" redis-cli SET articles:1 "$MERIDIAN_1"
docker exec "$REDIS_CONTAINER" redis-cli SET articles:2 "$MERIDIAN_2"
docker exec "$REDIS_CONTAINER" redis-cli SET articles:3 "$SOLSTICE"

echo "== elasticsearch =="
curl -s -X PUT "http://127.0.0.1:9200/articles" -H 'Content-Type: application/json' -d '{}' >/dev/null
curl -s -X POST "http://127.0.0.1:9200/articles/_doc/1?refresh=true" -H 'Content-Type: application/json' \
  -d "{\"body\": \"$MERIDIAN_1\"}" >/dev/null
curl -s -X POST "http://127.0.0.1:9200/articles/_doc/2?refresh=true" -H 'Content-Type: application/json' \
  -d "{\"body\": \"$MERIDIAN_2\"}" >/dev/null
curl -s -X POST "http://127.0.0.1:9200/articles/_doc/3?refresh=true" -H 'Content-Type: application/json' \
  -d "{\"body\": \"$SOLSTICE\"}" >/dev/null

echo "== dynamodb =="
echo "DynamoDB Local needs SigV4-signed requests to seed (no curl shortcut) -- run:"
echo "  cd App && AWS_ACCESS_KEY_ID=local AWS_SECRET_ACCESS_KEY=local go run testdata/seed_dynamodb.go"

echo "done"
