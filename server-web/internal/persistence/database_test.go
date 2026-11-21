package persistence

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
)

func openTestDatabase(t *testing.T, dir string) *Database {
	t.Helper()
	db, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = db.Close() })
	return db
}

func TestLegacyImportIsOnceAndOriginalRemains(t *testing.T) {
	dir := t.TempDir()
	source := filepath.Join(dir, "backend.json")
	original := []byte(`{"url":"http://127.0.0.1:3100","token":"test-only"}`)
	if err := os.WriteFile(source, original, 0o600); err != nil {
		t.Fatal(err)
	}
	db := openTestDatabase(t, dir)
	validate := func(raw []byte) error {
		var value map[string]any
		return json.Unmarshal(raw, &value)
	}
	if err := db.Import("backend", source, validate); err != nil {
		t.Fatal(err)
	}
	if err := db.Write("backend", []byte(`{"url":"https://example.invalid"}`)); err != nil {
		t.Fatal(err)
	}
	if err := db.Import("backend", source, validate); err != nil {
		t.Fatal(err)
	}
	raw, err := db.Read("backend")
	if err != nil || strings.Contains(string(raw), "test-only") {
		t.Fatalf("legacy setting replaced database state: %s %v", raw, err)
	}
	if err := db.Delete("backend"); err != nil {
		t.Fatal(err)
	}
	if err := db.Import("backend", source, validate); err != nil {
		t.Fatal(err)
	}
	if _, err := db.Read("backend"); !os.IsNotExist(err) {
		t.Fatalf("deleted setting was resurrected: %v", err)
	}
	preserved, err := os.ReadFile(source)
	if err != nil || string(preserved) != string(original) {
		t.Fatal("migration changed the original file")
	}
}

func TestMalformedLegacyImportCanBeRepairedWithoutPartialCommit(t *testing.T) {
	dir := t.TempDir()
	source := filepath.Join(dir, "items.json")
	if err := os.WriteFile(source, []byte("invalid-json"), 0o600); err != nil {
		t.Fatal(err)
	}
	db := openTestDatabase(t, dir)
	validate := func(raw []byte) error {
		var value any
		return json.Unmarshal(raw, &value)
	}
	if err := db.Import("items", source, validate); err == nil {
		t.Fatal("malformed source was accepted")
	}
	if got := db.Summary()["legacy_imports"]; got != 0 {
		t.Fatalf("failed import was committed: %v", got)
	}
	if err := os.WriteFile(source, []byte(`{"items":[]}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := db.Import("items", source, validate); err != nil {
		t.Fatal(err)
	}
}

func TestWALAndConcurrentDocumentsSurviveReopen(t *testing.T) {
	dir := t.TempDir()
	db := openTestDatabase(t, dir)
	const writers = 30
	var wg sync.WaitGroup
	for i := 0; i < writers; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			if err := db.Write(string(rune('A'+i)), []byte(`{"saved":true}`)); err != nil {
				t.Error(err)
			}
		}(i)
	}
	wg.Wait()
	again := openTestDatabase(t, dir)
	summary := again.Summary()
	if summary["documents"] != writers || summary["journal_mode"] != "wal" {
		t.Fatalf("database summary = %+v", summary)
	}
	var mode string
	if err := again.db.QueryRow("PRAGMA journal_mode").Scan(&mode); err != nil || mode != "wal" {
		t.Fatalf("journal mode = %s %v", mode, err)
	}
	if err := again.Write("bad", []byte("not-json")); err == nil {
		t.Fatal("invalid JSON document was committed")
	}
}

func TestFutureSchemaIsNeverDowngraded(t *testing.T) {
	dir := t.TempDir()
	db := openTestDatabase(t, dir)
	if _, err := db.db.Exec("PRAGMA user_version=99"); err != nil {
		t.Fatal(err)
	}
	if _, err := Open(dir); err == nil {
		t.Fatal("future schema was accepted")
	}
	var version int
	if err := db.db.QueryRow("PRAGMA user_version").Scan(&version); err != nil || version != 99 {
		t.Fatal("future schema was overwritten")
	}
}
