package postgres

import (
	"context"
	"errors"
	"testing"

	"github.com/jackc/pgx/v5"
)

type relationQuery struct {
	t           *testing.T
	wantTable   string
	schema      string
	relation    string
	queryCalled bool
	err         error
}

func (q *relationQuery) QueryRow(_ context.Context, sql string, args ...any) pgx.Row {
	q.t.Helper()
	q.queryCalled = true
	if sql != relationLookupSQL {
		q.t.Errorf("unexpected relation query:\n%s", sql)
	}
	if len(args) != 1 || args[0] != q.wantTable {
		q.t.Errorf("query arguments = %#v, want [%q]", args, q.wantTable)
	}
	return relationRow{schema: q.schema, relation: q.relation, err: q.err}
}

type relationRow struct {
	schema   string
	relation string
	err      error
}

func (r relationRow) Scan(dest ...any) error {
	if r.err != nil {
		return r.err
	}
	*(dest[0].(*string)) = r.schema
	*(dest[1].(*string)) = r.relation
	return nil
}

func TestResolveRelationUsesPostgresResolutionNotCurrentSchema(t *testing.T) {
	query := &relationQuery{
		t: t, wantTable: "articles", schema: "public", relation: "articles",
	}

	schema, relation, err := resolveRelation(context.Background(), query, "articles")
	if err != nil {
		t.Fatal(err)
	}
	if !query.queryCalled {
		t.Fatal("PostgreSQL was not asked to resolve the relation")
	}
	if schema != "public" || relation != "articles" {
		t.Errorf("got %s.%s, want public.articles", schema, relation)
	}
}

func TestResolveRelationReportsMissingTable(t *testing.T) {
	want := errors.New("no rows")
	query := &relationQuery{t: t, wantTable: "missing", err: want}

	_, _, err := resolveRelation(context.Background(), query, "missing")
	if !errors.Is(err, want) {
		t.Errorf("got %v, want wrapped lookup error", err)
	}
}
