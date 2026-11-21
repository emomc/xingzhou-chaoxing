// Package persistence provides private, versioned console storage.
package persistence

import (
	"database/sql"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const Filename = "console.sqlite3"

type Database struct {
	db   *sql.DB
	path string
}

func Enabled() bool {
	return os.Getenv("XINGGUANG_STORAGE") == "sqlite"
}

func Open(dir string) (*Database, error) {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, err
	}
	filename, err := filepath.Abs(filepath.Join(dir, Filename))
	if err != nil {
		return nil, err
	}
	if info, err := os.Lstat(filename); err == nil && info.Mode()&os.ModeSymlink != 0 {
		return nil, errors.New("the console database must not be a symbolic link")
	} else if err != nil && !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	path := filepath.ToSlash(filename)
	if !strings.HasPrefix(path, "/") {
		path = "/" + path
	}
	location := &url.URL{Scheme: "file", Path: path}
	query := url.Values{}
	query.Add("_pragma", "busy_timeout(10000)")
	query.Add("_pragma", "foreign_keys(ON)")
	query.Add("_pragma", "synchronous(FULL)")
	query.Set("_txlock", "immediate")
	location.RawQuery = query.Encode()
	db, err := sql.Open("sqlite", location.String())
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	fail := func(err error) (*Database, error) {
		_ = db.Close()
		return nil, err
	}
	if err := db.Ping(); err != nil {
		return fail(err)
	}
	if err := os.Chmod(filename, 0o600); err != nil {
		return fail(err)
	}
	var version int
	if err := db.QueryRow("PRAGMA user_version").Scan(&version); err != nil {
		return fail(err)
	}
	if version > 1 {
		return fail(errors.New("the console database requires a newer application"))
	}
	if _, err := db.Exec("PRAGMA journal_mode=WAL"); err != nil {
		return fail(err)
	}
	tx, err := db.Begin()
	if err != nil {
		return fail(err)
	}
	defer tx.Rollback()
	failTransaction := func(err error) (*Database, error) {
		_ = tx.Rollback()
		return fail(err)
	}
	for _, statement := range []string{
		`CREATE TABLE IF NOT EXISTS documents (
			name TEXT PRIMARY KEY,
			value TEXT NOT NULL CHECK(json_valid(value)),
			revision INTEGER NOT NULL DEFAULT 1,
			updated_at INTEGER NOT NULL
		)`,
		`CREATE TABLE IF NOT EXISTS legacy_imports (
			name TEXT PRIMARY KEY,
			source TEXT NOT NULL,
			imported_at INTEGER NOT NULL
		)`,
		"PRAGMA user_version=1",
	} {
		if _, err := tx.Exec(statement); err != nil {
			return failTransaction(err)
		}
	}
	if err := tx.Commit(); err != nil {
		return failTransaction(err)
	}
	return &Database{db: db, path: filename}, nil
}

// Import records an absent source too, so deleting a setting cannot resurrect it.
func (d *Database) Import(name, filename string, validate func([]byte) error) error {
	tx, err := d.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var exists int
	if err := tx.QueryRow("SELECT count(*) FROM legacy_imports WHERE name=?", name).Scan(&exists); err != nil {
		return err
	}
	if exists != 0 {
		return nil
	}
	raw, err := os.ReadFile(filename)
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err == nil {
		if err := validate(raw); err != nil {
			return fmt.Errorf("legacy %s migration was not committed: %w", name, err)
		}
		if _, err := tx.Exec(
			"INSERT OR IGNORE INTO documents(name,value,updated_at) VALUES (?,?,?)",
			name, string(raw), time.Now().Unix(),
		); err != nil {
			return err
		}
	}
	if _, err := tx.Exec("INSERT INTO legacy_imports VALUES (?,?,?)",
		name, filepath.Base(filename), time.Now().Unix()); err != nil {
		return err
	}
	return tx.Commit()
}

func (d *Database) Read(name string) ([]byte, error) {
	var raw string
	err := d.db.QueryRow("SELECT value FROM documents WHERE name=?", name).Scan(&raw)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, os.ErrNotExist
	}
	return []byte(raw), err
}

func (d *Database) Write(name string, raw []byte) error {
	_, err := d.db.Exec(
		`INSERT INTO documents(name,value,updated_at) VALUES (?,?,?)
		ON CONFLICT(name) DO UPDATE SET value=excluded.value,
		revision=documents.revision+1,updated_at=excluded.updated_at`,
		name, string(raw), time.Now().Unix(),
	)
	return err
}

func (d *Database) Delete(name string) error {
	_, err := d.db.Exec("DELETE FROM documents WHERE name=?", name)
	return err
}

func (d *Database) Summary() map[string]any {
	var documents, imports int
	if err := d.db.QueryRow("SELECT count(*) FROM documents").Scan(&documents); err != nil {
		return map[string]any{"engine": "sqlite", "status": "unavailable"}
	}
	if err := d.db.QueryRow("SELECT count(*) FROM legacy_imports").Scan(&imports); err != nil {
		return map[string]any{"engine": "sqlite", "status": "unavailable"}
	}
	var size int64
	for _, suffix := range []string{"", "-wal"} {
		if info, err := os.Stat(d.path + suffix); err == nil {
			size += info.Size()
		}
	}
	return map[string]any{
		"engine": "sqlite", "status": "ok", "journal_mode": "wal",
		"schema_version": 1, "documents": documents, "legacy_imports": imports,
		"database_bytes": size,
	}
}

func (d *Database) Close() error {
	return d.db.Close()
}
