// Package store keeps items in private file or server database storage.
package store

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"xingguang/internal/persistence"
)

const (
	StatusTodo  = "todo"
	StatusDoing = "doing"
	StatusDone  = "done"

	maxTitle  = 200
	maxDetail = 5000
)

var (
	ErrNotFound = errors.New("item not found")
	ErrInvalid  = errors.New("invalid item")
)

// Item is one local note the person keeps for themselves.
type Item struct {
	ID        string    `json:"id"`
	Title     string    `json:"title"`
	Detail    string    `json:"detail"`
	Status    string    `json:"status"`
	CreatedAt time.Time `json:"createdAt"`
	UpdatedAt time.Time `json:"updatedAt"`
	Seq       int64     `json:"seq"`
}

// Store is safe for concurrent use.
type Store struct {
	mu    sync.Mutex
	path  string
	items []Item
	seq   int64
	db    *persistence.Database
}

type fileData struct {
	Items []Item `json:"items"`
}

// Open loads items from dir/items.json. The file is created on the first save.
func Open(dir string) (*Store, error) {
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return nil, err
	}
	s := &Store{path: filepath.Join(dir, "items.json")}
	if persistence.Enabled() {
		db, err := persistence.Open(dir)
		if err != nil {
			return nil, err
		}
		s.db = db
		if err := db.Import("items", s.path, func(raw []byte) error {
			var data fileData
			return json.Unmarshal(raw, &data)
		}); err != nil {
			_ = db.Close()
			return nil, err
		}
	}
	if err := s.load(); err != nil {
		_ = s.Close()
		return nil, err
	}
	return s, nil
}

func (s *Store) load() error {
	var raw []byte
	var err error
	if s.db != nil {
		raw, err = s.db.Read("items")
	} else {
		raw, err = os.ReadFile(s.path)
	}
	if errors.Is(err, os.ErrNotExist) {
		s.items = []Item{}
		return nil
	}
	if err != nil {
		return err
	}
	var data fileData
	if err := json.Unmarshal(raw, &data); err != nil {
		return fmt.Errorf("read items: %w", err)
	}
	if data.Items == nil {
		data.Items = []Item{}
	}
	s.items = data.Items
	for _, item := range s.items {
		if item.Seq > s.seq {
			s.seq = item.Seq
		}
	}
	return nil
}

// List returns a copy ordered by most recently updated.
func (s *Store) List() []Item {
	s.mu.Lock()
	defer s.mu.Unlock()
	out := make([]Item, len(s.items))
	copy(out, s.items)
	sort.Slice(out, func(i, j int) bool {
		return out[i].Seq > out[j].Seq
	})
	return out
}

// Create validates the fields, stores the item, and writes the file.
func (s *Store) Create(title, detail, status string) (Item, error) {
	title, detail, status, err := normalize(title, detail, status, true)
	if err != nil {
		return Item{}, err
	}
	now := time.Now().UTC().Truncate(time.Second)
	s.mu.Lock()
	defer s.mu.Unlock()
	s.seq++
	item := Item{
		ID:        newID(),
		Title:     title,
		Detail:    detail,
		Status:    status,
		CreatedAt: now,
		UpdatedAt: now,
		Seq:       s.seq,
	}
	s.items = append(s.items, item)
	if err := s.save(); err != nil {
		s.items = s.items[:len(s.items)-1]
		return Item{}, err
	}
	return item, nil
}

// Update replaces the visible fields of an existing item.
func (s *Store) Update(id, title, detail, status string) (Item, error) {
	title, detail, status, err := normalize(title, detail, status, false)
	if err != nil {
		return Item{}, err
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	for i := range s.items {
		if s.items[i].ID != id {
			continue
		}
		prev := s.items[i]
		s.items[i].Title = title
		s.items[i].Detail = detail
		s.items[i].Status = status
		s.seq++
		s.items[i].UpdatedAt = time.Now().UTC().Truncate(time.Second)
		s.items[i].Seq = s.seq
		if err := s.save(); err != nil {
			s.items[i] = prev
			return Item{}, err
		}
		return s.items[i], nil
	}
	return Item{}, ErrNotFound
}

// Delete removes an item by id.
func (s *Store) Delete(id string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	for i := range s.items {
		if s.items[i].ID != id {
			continue
		}
		prev := append([]Item(nil), s.items...)
		s.items = append(s.items[:i], s.items[i+1:]...)
		if err := s.save(); err != nil {
			s.items = prev
			return err
		}
		return nil
	}
	return ErrNotFound
}

func (s *Store) save() error {
	raw, err := json.MarshalIndent(fileData{Items: s.items}, "", "  ")
	if err != nil {
		return err
	}
	raw = append(raw, '\n')
	if s.db != nil {
		return s.db.Write("items", raw)
	}
	tmp := s.path + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o600); err != nil {
		return err
	}
	_ = os.Remove(s.path)
	if err := os.Rename(tmp, s.path); err != nil {
		return err
	}
	return nil
}

func (s *Store) Close() error {
	if s.db != nil {
		return s.db.Close()
	}
	return nil
}

func (s *Store) StorageSummary() map[string]any {
	if s.db != nil {
		return s.db.Summary()
	}
	return map[string]any{"engine": "json", "status": "ok"}
}

func normalize(title, detail, status string, defaultStatus bool) (string, string, string, error) {
	title = strings.TrimSpace(title)
	detail = strings.TrimSpace(detail)
	status = strings.TrimSpace(status)
	if title == "" || len([]rune(title)) > maxTitle {
		return "", "", "", fmt.Errorf("%w: title", ErrInvalid)
	}
	if len([]rune(detail)) > maxDetail {
		return "", "", "", fmt.Errorf("%w: detail", ErrInvalid)
	}
	if status == "" && defaultStatus {
		status = StatusTodo
	}
	switch status {
	case StatusTodo, StatusDoing, StatusDone:
		return title, detail, status, nil
	default:
		return "", "", "", fmt.Errorf("%w: status", ErrInvalid)
	}
}

func newID() string {
	var buf [8]byte
	if _, err := rand.Read(buf[:]); err != nil {
		return fmt.Sprintf("%d", time.Now().UnixNano())
	}
	return hex.EncodeToString(buf[:])
}
