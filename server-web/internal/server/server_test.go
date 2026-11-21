package server

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"xingguang/internal/store"
)

func TestAPIFlow(t *testing.T) {
	srv, err := New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	ts := httptest.NewServer(srv.Handler())
	t.Cleanup(ts.Close)

	health := getJSON(t, ts.URL+"/api/health")
	if health["status"] != "ok" {
		t.Fatalf("health = %v", health)
	}

	page, err := http.Get(ts.URL + "/")
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(page.Body)
	page.Body.Close()
	if page.StatusCode != http.StatusOK || !bytes.Contains(body, []byte("星光")) {
		t.Fatalf("page status %d body %q", page.StatusCode, body)
	}

	created := sendJSON(t, http.MethodPost, ts.URL+"/api/items", map[string]string{
		"title":  "写框架说明",
		"detail": "本地保存",
		"status": "todo",
	}, http.StatusCreated)
	id, _ := created["id"].(string)
	if id == "" {
		t.Fatal("missing id")
	}

	listed := getJSON(t, ts.URL+"/api/items")
	items, _ := listed["items"].([]any)
	if len(items) != 1 {
		t.Fatalf("items = %v", listed)
	}

	updated := sendJSON(t, http.MethodPut, ts.URL+"/api/items/"+id, map[string]string{
		"title":  "写框架说明",
		"detail": "已改",
		"status": "done",
	}, http.StatusOK)
	if updated["status"] != store.StatusDone || updated["detail"] != "已改" {
		t.Fatalf("updated = %v", updated)
	}

	req, err := http.NewRequest(http.MethodDelete, ts.URL+"/api/items/"+id, nil)
	if err != nil {
		t.Fatal(err)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusNoContent {
		t.Fatalf("delete status = %d", resp.StatusCode)
	}
	after := getJSON(t, ts.URL+"/api/items")["items"]
	items, ok := after.([]any)
	if !ok || len(items) != 0 {
		t.Fatalf("after delete = %#v", after)
	}
}

func TestAPIErrorsAndShutdown(t *testing.T) {
	srv, err := New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	called := make(chan struct{}, 1)
	srv.SetShutdown(func() { called <- struct{}{} })
	ts := httptest.NewServer(srv.Handler())
	t.Cleanup(ts.Close)

	sendJSON(t, http.MethodPost, ts.URL+"/api/items", map[string]string{"title": " "}, http.StatusBadRequest)
	sendJSON(t, http.MethodPost, ts.URL+"/api/items", map[string]string{"title": "a", "status": "nope"}, http.StatusBadRequest)
	req, _ := http.NewRequest(http.MethodPost, ts.URL+"/api/items", bytes.NewBufferString("{"))
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("bad json status = %d", resp.StatusCode)
	}

	missing, _ := http.NewRequest(http.MethodDelete, ts.URL+"/api/items/missing", nil)
	resp, err = http.DefaultClient.Do(missing)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("missing status = %d", resp.StatusCode)
	}

	sendJSON(t, http.MethodPost, ts.URL+"/api/shutdown", map[string]string{}, http.StatusOK)
	select {
	case <-called:
	case <-time.After(2 * time.Second):
		t.Fatal("shutdown callback was not called")
	}
}

func getJSON(t *testing.T, url string) map[string]any {
	t.Helper()
	resp, err := http.Get(url)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("%s status %d", url, resp.StatusCode)
	}
	var out map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatal(err)
	}
	return out
}

func sendJSON(t *testing.T, method, url string, payload any, want int) map[string]any {
	t.Helper()
	raw, err := json.Marshal(payload)
	if err != nil {
		t.Fatal(err)
	}
	req, err := http.NewRequest(method, url, bytes.NewReader(raw))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != want {
		t.Fatalf("%s %s status = %d body %s", method, url, resp.StatusCode, body)
	}
	if len(bytes.TrimSpace(body)) == 0 {
		return nil
	}
	var out map[string]any
	if err := json.Unmarshal(body, &out); err != nil {
		t.Fatal(err)
	}
	return out
}
