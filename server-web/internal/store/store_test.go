package store

import (
	"fmt"
	"path/filepath"
	"sync"
	"testing"
)

func TestCreateListUpdateDeleteRoundTrip(t *testing.T) {
	dir := t.TempDir()
	s, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	item, err := s.Create("  买菜  ", "西红柿", "")
	if err != nil {
		t.Fatal(err)
	}
	if item.Title != "买菜" || item.Status != StatusTodo || item.ID == "" {
		t.Fatalf("unexpected item: %+v", item)
	}

	again, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	got := again.List()
	if len(got) != 1 || got[0].ID != item.ID || got[0].Detail != "西红柿" {
		t.Fatalf("reload = %+v", got)
	}

	updated, err := again.Update(item.ID, "买菜和水果", "西红柿、香蕉", StatusDoing)
	if err != nil {
		t.Fatal(err)
	}
	if updated.Status != StatusDoing || updated.CreatedAt != item.CreatedAt {
		t.Fatalf("update changed identity: %+v", updated)
	}
	if !updated.UpdatedAt.Equal(item.UpdatedAt) && updated.UpdatedAt.Before(item.UpdatedAt) {
		t.Fatal("updated time moved backwards")
	}

	if err := again.Delete(item.ID); err != nil {
		t.Fatal(err)
	}
	if len(again.List()) != 0 {
		t.Fatal("item still present")
	}
	if _, err := osStatItems(dir); err != nil {
		t.Fatal(err)
	}
	reloaded, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	if len(reloaded.List()) != 0 {
		t.Fatal("delete was not saved")
	}
}

func TestValidation(t *testing.T) {
	s, err := Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.Create("   ", "", StatusTodo); !errorsIsInvalid(err) {
		t.Fatalf("empty title err = %v", err)
	}
	if _, err := s.Create("ok", "", "later"); !errorsIsInvalid(err) {
		t.Fatalf("bad status err = %v", err)
	}
	long := make([]rune, maxTitle+1)
	for i := range long {
		long[i] = '题'
	}
	if _, err := s.Create(string(long), "", StatusTodo); !errorsIsInvalid(err) {
		t.Fatalf("long title err = %v", err)
	}
	if _, err := s.Update("missing", "ok", "", StatusDone); err != ErrNotFound {
		t.Fatalf("missing update err = %v", err)
	}
	if err := s.Delete("missing"); err != ErrNotFound {
		t.Fatalf("missing delete err = %v", err)
	}
	if len(s.List()) != 0 {
		t.Fatal("invalid writes changed the list")
	}
}

func TestConcurrentCreate(t *testing.T) {
	s, err := Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	const n = 30
	var wg sync.WaitGroup
	errCh := make(chan error, n)
	for i := 0; i < n; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			if _, err := s.Create(fmt.Sprintf("事项 %d", i), "", StatusTodo); err != nil {
				errCh <- err
			}
		}(i)
	}
	wg.Wait()
	close(errCh)
	for err := range errCh {
		t.Error(err)
	}
	if got := len(s.List()); got != n {
		t.Fatalf("count = %d, want %d", got, n)
	}
}

func TestListOrdersByRecentUpdate(t *testing.T) {
	s, err := Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	first, err := s.Create("先写", "", StatusTodo)
	if err != nil {
		t.Fatal(err)
	}
	second, err := s.Create("后写", "", StatusTodo)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.Update(first.ID, "先写但刚改过", "", StatusDone); err != nil {
		t.Fatal(err)
	}
	list := s.List()
	if list[0].ID != first.ID || list[1].ID != second.ID {
		t.Fatalf("order = %s then %s", list[0].ID, list[1].ID)
	}
}

func errorsIsInvalid(err error) bool {
	return err != nil && (err == ErrInvalid || len(err.Error()) > 0 && containsInvalid(err))
}

func containsInvalid(err error) bool {
	return len(err.Error()) >= len(ErrInvalid.Error()) && (err.Error() == ErrInvalid.Error() || hasPrefix(err.Error(), ErrInvalid.Error()))
}

func hasPrefix(s, prefix string) bool {
	return len(s) >= len(prefix) && s[:len(prefix)] == prefix
}

func osStatItems(dir string) (bool, error) {
	_, err := Open(dir)
	if err != nil {
		return false, err
	}
	path := filepath.Join(dir, "items.json")
	if _, err := stat(path); err != nil {
		return false, err
	}
	return true, nil
}
