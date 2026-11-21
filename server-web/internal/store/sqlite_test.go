package store

import (
	"os"
	"path/filepath"
	"testing"
)

func TestSQLiteItemsMigrateAndDoNotWriteLegacyFile(t *testing.T) {
	dir := t.TempDir()
	legacy, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	item, err := legacy.Create("Existing note", "Preserve this", StatusTodo)
	if err != nil {
		t.Fatal(err)
	}
	source := filepath.Join(dir, "items.json")
	original, err := os.ReadFile(source)
	if err != nil {
		t.Fatal(err)
	}
	t.Setenv("XINGGUANG_STORAGE", "sqlite")
	dbStore, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = dbStore.Close() })
	if len(dbStore.List()) != 1 || dbStore.List()[0].ID != item.ID {
		t.Fatal("existing notes were not migrated")
	}
	if _, err := dbStore.Update(item.ID, "Updated in database", "", StatusDone); err != nil {
		t.Fatal(err)
	}
	again, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = again.Close() })
	if got := again.List()[0]; got.Title != "Updated in database" || got.Status != StatusDone {
		t.Fatalf("database update was not persisted: %+v", got)
	}
	preserved, _ := os.ReadFile(source)
	if string(preserved) != string(original) {
		t.Fatal("legacy file was modified after migration")
	}
	if dbStore.StorageSummary()["engine"] != "sqlite" {
		t.Fatal("database mode was not reported")
	}
}
