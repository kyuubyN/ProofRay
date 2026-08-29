package mongodb

import (
	"strings"
	"testing"

	"go.mongodb.org/mongo-driver/bson"
	"go.mongodb.org/mongo-driver/bson/primitive"
)

func TestFormatIDKeepsObjectIDReadable(t *testing.T) {
	id := primitive.NewObjectID()

	got, err := formatID(id)
	if err != nil {
		t.Fatal(err)
	}
	if got != id.Hex() {
		t.Errorf("got %q, want %q", got, id.Hex())
	}
}

func TestFormatIDPreservesScalarBSONTypes(t *testing.T) {
	stringID, err := formatID("42")
	if err != nil {
		t.Fatal(err)
	}
	integerID, err := formatID(int32(42))
	if err != nil {
		t.Fatal(err)
	}
	if stringID == integerID {
		t.Fatalf("string and int32 IDs collided: %q", stringID)
	}
	if !strings.Contains(integerID, `$numberInt`) {
		t.Errorf("int32 ID is not canonical Extended JSON: %q", integerID)
	}
}

func TestFormatIDPreservesTypesInsideCompositeBSONIDs(t *testing.T) {
	integerID, err := formatID(bson.D{{Key: "a", Value: int32(1)}})
	if err != nil {
		t.Fatal(err)
	}
	stringID, err := formatID(bson.D{{Key: "a", Value: "1"}})
	if err != nil {
		t.Fatal(err)
	}
	if integerID == stringID {
		t.Fatalf("composite BSON IDs collided: %q", integerID)
	}
	if !strings.Contains(integerID, `$numberInt`) || !strings.Contains(stringID, `"1"`) {
		t.Errorf("IDs are not type-preserving canonical Extended JSON:\nint: %s\nstring: %s", integerID, stringID)
	}
}

func TestFormatIDRejectsValuesBSONCannotEncode(t *testing.T) {
	if _, err := formatID(make(chan int)); err == nil {
		t.Error("unsupported _id type was silently assigned an unstable identity")
	}
}
