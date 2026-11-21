package server

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/cookiejar"
	"net/http/httptest"
	"sync/atomic"
	"testing"
)

func authJSON(t *testing.T, client *http.Client, method, url string, body any, want int) map[string]any {
	t.Helper()
	var reader io.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		reader = bytes.NewReader(raw)
	}
	request, err := http.NewRequest(method, url, reader)
	if err != nil {
		t.Fatal(err)
	}
	if body != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	response, err := client.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	defer response.Body.Close()
	raw, _ := io.ReadAll(response.Body)
	if response.StatusCode != want {
		t.Fatalf("%s %s = %d, want %d: %s", method, url, response.StatusCode, want, raw)
	}
	if len(bytes.TrimSpace(raw)) == 0 {
		return nil
	}
	var result map[string]any
	if err := json.Unmarshal(raw, &result); err != nil {
		t.Fatal(err)
	}
	return result
}

func TestUserLoginIsolationAndNonRefundableCourseQuota(t *testing.T) {
	const ownerPassword = "offline-owner-password"
	const token = "offline-backend-token-with-more-than-32-characters"
	const accountA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
	const accountB = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
	var creates atomic.Int32
	var selections atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+token {
			t.Error("missing backend token")
		}
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.URL.Path == "/v1/health":
			_, _ = w.Write([]byte(`{"status":"ok","jobs":{"running":2},"answers":{"running":1,"queued":0},"max_workers":30}`))
		case r.URL.Path == "/v1/accounts":
			_, _ = w.Write([]byte(`{"accounts":[{"id":"` + accountA + `","label":"A"},{"id":"` + accountB + `","label":"B"}]}`))
		case r.URL.Path == "/v1/jobs" && r.Method == http.MethodGet:
			_, _ = w.Write([]byte(`{"jobs":[{"id":"` + accountA[:31] + `1","account_id":"` + accountA + `","status":"running"},{"id":"` + accountB[:31] + `2","account_id":"` + accountB + `","status":"queued"}]}`))
		case r.URL.Path == "/v1/jobs" && r.Method == http.MethodPost:
			creates.Add(1)
			var body map[string]any
			raw, _ := io.ReadAll(r.Body)
			if json.Unmarshal(raw, &body) != nil || body["quota_confirmed"] != nil {
				t.Error("quota confirmation reached the backend")
			}
			if _, ok := body["point_ids"]; ok {
				selections.Add(1)
			}
			_, _ = w.Write([]byte(`{"id":"` + accountA[:31] + `3","account_id":"` + accountA + `","status":"queued"}`))
		case r.URL.Path == "/v1/jobs/"+accountA[:31]+"1":
			_, _ = w.Write([]byte(`{"id":"` + accountA[:31] + `1","account_id":"` + accountA + `","status":"running"}`))
		case r.URL.Path == "/v1/jobs/"+accountB[:31]+"2":
			_, _ = w.Write([]byte(`{"id":"` + accountB[:31] + `2","account_id":"` + accountB + `","status":"queued"}`))
		case r.URL.Path == "/v1/jobs/"+accountA[:31]+"3/cancel":
			w.WriteHeader(http.StatusOK)
			_, _ = w.Write([]byte(`{"id":"cancelled"}`))
		case r.URL.Path == "/v1/jobs/"+accountA[:31]+"3":
			_, _ = w.Write([]byte(`{"id":"` + accountA[:31] + `3","account_id":"` + accountA + `","status":"queued"}`))
		default:
			w.WriteHeader(http.StatusNotFound)
			_, _ = w.Write([]byte(`{"error":"offline"}`))
		}
	}))
	defer upstream.Close()
	app, err := NewWithOwner(t.TempDir(), "owner", ownerPassword)
	if err != nil {
		t.Fatal(err)
	}
	defer app.Close()
	if err := app.ConfigureBackend(upstream.URL, token); err != nil {
		t.Fatal(err)
	}
	server := httptest.NewServer(app.Handler())
	defer server.Close()
	jar, _ := cookiejar.New(nil)
	client := &http.Client{Jar: jar}

	authJSON(t, client, http.MethodGet, server.URL+"/api/backend/accounts", nil, http.StatusUnauthorized)
	owner := authJSON(t, client, http.MethodPost, server.URL+"/api/session",
		map[string]string{"username": "owner", "password": ownerPassword}, http.StatusOK)
	ownerID := owner["user"].(map[string]any)["id"].(string)
	if ownerID == "" {
		t.Fatal("owner id missing")
	}
	created := authJSON(t, client, http.MethodPost, server.URL+"/api/users", map[string]any{
		"username": "student", "password": "student-password", "credits": 2,
	}, http.StatusCreated)
	userID := created["user"].(map[string]any)["id"].(string)
	authJSON(t, client, http.MethodPut, server.URL+"/api/users/"+userID+"/accounts",
		map[string]any{"account_ids": []string{accountA}}, http.StatusOK)

	studentJar, _ := cookiejar.New(nil)
	student := &http.Client{Jar: studentJar}
	authJSON(t, student, http.MethodPost, server.URL+"/api/session",
		map[string]string{"username": "student", "password": "student-password"}, http.StatusOK)
	accounts := authJSON(t, student, http.MethodGet, server.URL+"/api/backend/accounts", nil, http.StatusOK)
	if len(accounts["accounts"].([]any)) != 1 {
		t.Fatal("student saw another account")
	}
	jobs := authJSON(t, student, http.MethodGet, server.URL+"/api/backend/jobs", nil, http.StatusOK)
	if len(jobs["jobs"].([]any)) != 1 {
		t.Fatal("student saw another job")
	}
	authJSON(t, student, http.MethodGet, server.URL+"/api/backend/jobs/"+accountB[:31]+"2", nil, http.StatusForbidden)
	authJSON(t, student, http.MethodGet, server.URL+"/api/backend/resources", nil, http.StatusForbidden)
	authJSON(t, student, http.MethodPost, server.URL+"/api/backend/accounts", map[string]string{}, http.StatusForbidden)
	authJSON(t, student, http.MethodGet, server.URL+"/api/ai-windows", nil, http.StatusForbidden)
	authJSON(t, student, http.MethodGet, server.URL+"/api/backend/ai/provider", nil, http.StatusForbidden)
	authJSON(t, student, http.MethodPut, server.URL+"/api/backend/ai/provider",
		map[string]any{"model": "text-model"}, http.StatusForbidden)
	authJSON(t, student, http.MethodGet, server.URL+"/api/backend/ai/models", nil, http.StatusForbidden)
	authJSON(t, student, http.MethodPost, server.URL+"/api/backend/ai/test", map[string]any{}, http.StatusForbidden)
	authJSON(t, student, http.MethodGet, server.URL+"/api/backend/ai/conversations", nil, http.StatusForbidden)
	authJSON(t, student, http.MethodPost, server.URL+"/api/backend/jobs", map[string]any{
		"account_id": accountB, "course_ids": []string{"101"}, "quota_confirmed": true,
	}, http.StatusForbidden)
	authJSON(t, student, http.MethodPost, server.URL+"/api/backend/jobs", map[string]any{
		"account_id": accountA, "course_ids": []string{"101", "202"},
	}, http.StatusBadRequest)
	createdJob := authJSON(t, student, http.MethodPost, server.URL+"/api/backend/jobs", map[string]any{
		"account_id": accountA, "course_ids": []string{"101", "202"}, "quota_confirmed": true,
		"point_ids": map[string][]string{"101": {"9001"}},
	}, http.StatusOK)
	if createdJob["id"] == nil || creates.Load() != 1 {
		t.Fatal("quota-confirmed job was not created")
	}
	if selections.Load() != 1 {
		t.Fatal("chapter selection was not forwarded to the backend")
	}
	session := authJSON(t, student, http.MethodGet, server.URL+"/api/session", nil, http.StatusOK)
	if session["user"].(map[string]any)["credits"].(float64) != 0 {
		t.Fatal("course credits were not deducted")
	}
	authJSON(t, student, http.MethodPost, server.URL+"/api/backend/jobs", map[string]any{
		"account_id": accountA, "course_ids": []string{"101"}, "quota_confirmed": true,
	}, http.StatusConflict)
}
