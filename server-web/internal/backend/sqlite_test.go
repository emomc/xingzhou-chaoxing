package backend

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSQLiteConnectionMigrationAndDisconnect(t *testing.T) {
	dir := t.TempDir()
	original, _ := json.Marshal(Config{URL: "http://127.0.0.1:3100", Token: testToken})
	source := filepath.Join(dir, "backend.json")
	if err := os.WriteFile(source, original, 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("XINGGUANG_STORAGE", "sqlite")
	client, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = client.Close() })
	if client.cfg.Token != testToken {
		t.Fatal("connection was not migrated")
	}
	summary, _ := json.Marshal(client.Summary())
	if strings.Contains(string(summary), testToken) {
		t.Fatal("private token leaked in summary")
	}
	if err := client.Configure("http://127.0.0.1:3100", testToken+"-updated"); err != nil {
		t.Fatal(err)
	}
	if err := client.Disconnect(); err != nil {
		t.Fatal(err)
	}
	again, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = again.Close() })
	if again.cfg.URL != "" || again.cfg.Token != "" {
		t.Fatal("disconnect resurrected the legacy connection")
	}
	preserved, _ := os.ReadFile(source)
	if string(preserved) != string(original) {
		t.Fatal("legacy connection file was modified")
	}
}
