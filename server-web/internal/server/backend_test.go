package server

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"net/url"
	"slices"
	"strings"
	"testing"
)

func TestResourceAndQuestionBankReadOnlyProxy(t *testing.T) {
	const token = "offline-test-backend-token-with-enough-characters"
	const questionID = "0123456789abcdef0123456789abcdef"
	var paths []string
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+token || r.Method != http.MethodGet {
			t.Error("inspection proxy must authenticate read-only requests")
		}
		paths = append(paths, r.URL.Path)
		if r.URL.Path == "/v1/question-bank" {
			if r.URL.Query().Get("search") != "题目 & A..B? /v1/jobs" ||
				r.URL.Query().Get("course_id") != "101" || r.URL.Query().Get("page") != "2" {
				t.Errorf("query was not preserved: %v", r.URL.Query())
			}
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"status":"ok"}`))
	}))
	defer upstream.Close()
	app, err := New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer app.Close()
	if err := app.ConfigureBackend(upstream.URL, token); err != nil {
		t.Fatal(err)
	}
	local := httptest.NewServer(app.Handler())
	defer local.Close()
	for _, path := range []string{
		"resources", "question-bank/stats", "pacing", "question-bank/" + questionID,
		"question-bank/" + questionID + "/images/image_10",
		"question-bank?search=" + url.QueryEscape("题目 & A..B? /v1/jobs") + "&course_id=101&page=2",
		"image-questions?search=" + url.QueryEscape("A..B & '") + "&status=review",
		"image-questions/" + questionID, "image-questions/" + questionID + "/images/image_1",
	} {
		result := getJSON(t, local.URL+"/api/backend/"+path)
		if result["status"] != "ok" {
			t.Fatal("inspection route did not forward the upstream response")
		}
	}
	for _, path := range []string{
		"question-bank/invalid", "question-bank?token=not-allowed",
		"question-bank?page=1&page=2", "question-bank?search=%zz",
		"question-bank?search=" + strings.Repeat("a", 4097),
		"question-bank/" + questionID + "/images/image_11",
		"question-bank/" + questionID + "/images/image_0",
		"question-bank/invalid/images/image_1",
		"image-questions/invalid", "image-questions?token=not-allowed",
		"image-questions?page=1&page=2", "image-questions?search=%zz",
		"image-questions?search=" + strings.Repeat("a", 4097),
		"image-questions/" + questionID + "/images/image_11",
		"image-questions/invalid/images/image_1",
	} {
		sendJSON(t, http.MethodGet, local.URL+"/api/backend/"+path, nil, http.StatusBadRequest)
	}
	if len(paths) != 9 {
		t.Fatalf("invalid inspection request reached the backend: %v", paths)
	}
	for _, path := range []string{"resources", "question-bank", "question-bank/stats", "pacing",
		"question-bank/" + questionID,
		"question-bank/" + questionID + "/images/image_1", "image-questions",
		"image-questions/" + questionID, "image-questions/" + questionID + "/images/image_1"} {
		response, err := http.Post(local.URL+"/api/backend/"+path, "application/json", strings.NewReader("{}"))
		if err != nil {
			t.Fatal(err)
		}
		response.Body.Close()
		if response.StatusCode != http.StatusMethodNotAllowed {
			t.Fatalf("inspection write status %d", response.StatusCode)
		}
	}
	if len(paths) != 9 {
		t.Fatal("inspection write reached the backend")
	}
}

func TestBackendConnectionAndProxy(t *testing.T) {
	const token = "offline-test-backend-token-with-enough-characters"
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+token {
			t.Error("proxy did not provide the token")
		}
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/v1/jobs" {
			_, _ = w.Write([]byte(`{"jobs":[{"id":"test-job","status":"queued"}]}`))
		} else {
			_, _ = w.Write([]byte(`{"status":"ok"}`))
		}
	}))
	defer upstream.Close()
	app, err := New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	local := httptest.NewServer(app.Handler())
	defer local.Close()
	saved := sendJSON(t, "PUT", local.URL+"/api/backend", map[string]string{
		"url": upstream.URL, "token": token,
	}, http.StatusOK)
	if saved["configured"] != true || saved["token"] != nil {
		t.Fatalf("public configuration %#v", saved)
	}
	jobs := getJSON(t, local.URL+"/api/backend/jobs")
	jobJSON, _ := json.Marshal(jobs)
	if !strings.Contains(string(jobJSON), "test-job") {
		t.Fatal("missing remote task")
	}
	request, err := http.NewRequest(http.MethodDelete,
		local.URL+"/api/backend/jobs/"+strings.Repeat("a", 32), nil)
	if err != nil {
		t.Fatal(err)
	}
	deleteResponse, err := http.DefaultClient.Do(request)
	if err != nil {
		t.Fatal(err)
	}
	deleteResponse.Body.Close()
	if deleteResponse.StatusCode != http.StatusOK {
		t.Fatalf("delete proxy status %d", deleteResponse.StatusCode)
	}
	response, err := http.Get(local.URL + "/backend.js")
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(response.Body)
	response.Body.Close()
	if response.StatusCode != 200 || !bytes.Contains(raw, []byte("refreshBackend")) {
		t.Fatal("backend client asset was not embedded")
	}
}

func TestAIChannelSwitchProxy(t *testing.T) {
	const token = "offline-test-backend-token-with-enough-characters"
	var methods []string
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/ai/channel" || r.Header.Get("Authorization") != "Bearer "+token {
			t.Error("AI channel proxy path or authentication failed")
		}
		methods = append(methods, r.Method)
		w.Header().Set("Content-Type", "application/json")
		if r.Method == http.MethodGet {
			_, _ = w.Write([]byte(`{"channel":"web","api":{"configured":true}}`))
			return
		}
		raw, _ := io.ReadAll(r.Body)
		if string(raw) != `{"channel":"api"}` {
			t.Errorf("AI channel body = %s", raw)
		}
		_, _ = w.Write([]byte(`{"channel":"api","api":{"configured":true}}`))
	}))
	defer upstream.Close()
	app, err := New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer app.Close()
	if err := app.ConfigureBackend(upstream.URL, token); err != nil {
		t.Fatal(err)
	}
	local := httptest.NewServer(app.Handler())
	defer local.Close()
	if result := getJSON(t, local.URL+"/api/backend/ai/channel"); result["channel"] != "web" {
		t.Fatalf("AI channel read = %#v", result)
	}
	if result := sendJSON(t, http.MethodPut, local.URL+"/api/backend/ai/channel",
		map[string]string{"channel": "api"}, http.StatusOK); result["channel"] != "api" {
		t.Fatalf("AI channel switch = %#v", result)
	}
	if !slices.Equal(methods, []string{http.MethodGet, http.MethodPut}) {
		t.Fatalf("AI channel methods = %v", methods)
	}
}

func TestVideoGuardProxy(t *testing.T) {
	const token = "offline-test-backend-token-with-enough-characters"
	var seen []string
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/video/guard" || r.Header.Get("Authorization") != "Bearer "+token {
			t.Error("video guard proxy path or authentication failed")
		}
		seen = append(seen, r.Method+" "+r.URL.Path)
		w.Header().Set("Content-Type", "application/json")
		if r.Method == http.MethodGet {
			_, _ = w.Write([]byte(`{"max_speed":1.5,"quiet_hours":true,"auto_pause":false,"recheck":8,` +
				`"rollbacks":{"count":1,"items":[{"job_id":"job-1","dropped":620,"action":"slow"}]}}`))
			return
		}
		raw, _ := io.ReadAll(r.Body)
		if string(raw) != `{"auto_pause":true,"max_speed":1.2,"quiet_hours":false,"recheck":6}` {
			t.Errorf("video guard body = %s", raw)
		}
		_, _ = w.Write([]byte(`{"max_speed":1.2,"quiet_hours":false,"auto_pause":true,"recheck":6}`))
	}))
	defer upstream.Close()
	app, err := New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer app.Close()
	if err := app.ConfigureBackend(upstream.URL, token); err != nil {
		t.Fatal(err)
	}
	local := httptest.NewServer(app.Handler())
	defer local.Close()
	guard := getJSON(t, local.URL+"/api/backend/video/guard")
	if guard["max_speed"] != float64(1.5) || guard["quiet_hours"] != true {
		t.Fatalf("video guard read = %#v", guard)
	}
	rollbacks, ok := guard["rollbacks"].(map[string]any)
	if !ok || rollbacks["count"] != float64(1) {
		t.Fatalf("video guard rollbacks = %#v", guard["rollbacks"])
	}
	saved := sendJSON(t, http.MethodPut, local.URL+"/api/backend/video/guard", map[string]any{
		"max_speed": 1.2, "quiet_hours": false, "auto_pause": true, "recheck": 6,
	}, http.StatusOK)
	if saved["auto_pause"] != true || saved["recheck"] != float64(6) {
		t.Fatalf("video guard save = %#v", saved)
	}
	if !slices.Equal(seen, []string{"GET /v1/video/guard", "PUT /v1/video/guard"}) {
		t.Fatalf("video guard proxy order = %v", seen)
	}
}

func TestJobsArchiveFilterIsForwardedAndValidated(t *testing.T) {
	const token = "offline-test-backend-token-with-enough-characters"
	calls := 0
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Method != http.MethodGet || r.URL.Path != "/v1/jobs" ||
			r.Header.Get("Authorization") != "Bearer "+token {
			t.Errorf("archive filter was not safely forwarded: %s %s", r.Method, r.URL.String())
		}
		if calls == 1 && (r.URL.Query().Get("archived") != "1" || r.URL.Query().Get("limit") != "50") {
			t.Errorf("first archive query was not forwarded: %s", r.URL.String())
		}
		if calls == 2 && (r.URL.Query().Get("archived") != "0" || r.URL.Query().Get("limit") != "10") {
			t.Errorf("second archive query retained stale parameters: %s", r.URL.String())
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"jobs":[{"id":"archived-job","archived_at":123}]}`))
	}))
	defer upstream.Close()
	app, err := New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer app.Close()
	if err := app.ConfigureBackend(upstream.URL, token); err != nil {
		t.Fatal(err)
	}
	local := httptest.NewServer(app.Handler())
	defer local.Close()

	result := getJSON(t, local.URL+"/api/backend/jobs?archived=1&limit=50")
	items, ok := result["jobs"].([]any)
	if !ok || len(items) != 1 {
		t.Fatalf("archive query response %#v", result)
	}
	_ = getJSON(t, local.URL+"/api/backend/jobs?archived=0&limit=10")
	for _, path := range []string{
		"/api/backend/jobs?archived=yes",
		"/api/backend/jobs?archived=0&archived=1",
		"/api/backend/jobs?token=hidden",
	} {
		response, err := http.Get(local.URL + path)
		if err != nil {
			t.Fatal(err)
		}
		response.Body.Close()
		if response.StatusCode != http.StatusBadRequest {
			t.Fatalf("invalid archive query %s returned %d", path, response.StatusCode)
		}
	}
	if calls != 2 {
		t.Fatalf("invalid query reached the backend %d times", calls-2)
	}
}

func TestBatchJobProxyPreservesExplicitTargetsAndPartialFailures(t *testing.T) {
	const token = "offline-test-backend-token-with-enough-characters"
	const firstID = "0123456789abcdef0123456789abcdef"
	const secondID = "fedcba9876543210fedcba9876543210"
	calls := 0
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Method != http.MethodPost || r.URL.Path != "/v1/jobs/batch" ||
			r.Header.Get("Authorization") != "Bearer "+token {
			t.Error("batch request was not authenticated or routed correctly")
		}
		var payload struct {
			Action string   `json:"action"`
			JobIDs []string `json:"job_ids"`
		}
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Fatal(err)
		}
		if payload.Action != "resume" || len(payload.JobIDs) != 2 ||
			payload.JobIDs[0] != firstID || payload.JobIDs[1] != secondID {
			t.Errorf("explicit batch targets were changed: %#v", payload)
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]any{
			"action": "resume", "requested": 2, "succeeded": 1, "failed": 1,
			"results": []any{
				map[string]any{"id": firstID, "ok": true,
					"job": map[string]any{"id": firstID, "status": "queued"}},
				map[string]any{"id": secondID, "ok": false,
					"error": "Job status changed", "code": "STATE_CONFLICT"},
			},
		})
	}))
	defer upstream.Close()
	app, err := New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer app.Close()
	if err := app.ConfigureBackend(upstream.URL, token); err != nil {
		t.Fatal(err)
	}
	local := httptest.NewServer(app.Handler())
	defer local.Close()
	result := sendJSON(t, http.MethodPost, local.URL+"/api/backend/jobs/batch",
		map[string]any{"action": "resume", "job_ids": []string{firstID, secondID}}, http.StatusOK)
	results, ok := result["results"].([]any)
	if !ok || len(results) != 2 || result["succeeded"] != float64(1) || result["failed"] != float64(1) {
		t.Fatalf("partial batch result was lost: %#v", result)
	}
	if results[1].(map[string]any)["code"] != "STATE_CONFLICT" {
		t.Fatal("batch failure detail was lost")
	}
	response, err := http.Get(local.URL + "/api/backend/jobs/batch")
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusNotFound {
		t.Fatalf("unsupported batch method status %d", response.StatusCode)
	}
	if calls != 1 {
		t.Fatal("unsupported batch method reached upstream")
	}
}

func TestLocalAPIRejectsCrossOriginAndRebinding(t *testing.T) {
	app, _ := New(t.TempDir())
	local := httptest.NewServer(app.Handler())
	defer local.Close()
	for _, kind := range []string{"origin", "host"} {
		req, _ := http.NewRequest("GET", local.URL+"/api/backend", nil)
		if kind == "origin" {
			req.Header.Set("Origin", "https://untrusted.invalid")
		} else {
			req.Host = "rebound.invalid"
		}
		response, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		response.Body.Close()
		if response.StatusCode != http.StatusForbidden {
			t.Fatalf("%s accepted with status %d", kind, response.StatusCode)
		}
	}
}

func TestUnconfiguredBackendAndInvalidActions(t *testing.T) {
	app, _ := New(t.TempDir())
	local := httptest.NewServer(app.Handler())
	defer local.Close()
	resp, err := http.Get(local.URL + "/api/backend/jobs")
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusBadGateway {
		t.Fatalf("unconfigured status %d", resp.StatusCode)
	}
	sendJSON(t, "POST", local.URL+"/api/backend/jobs/not-an-id/cancel", map[string]string{}, http.StatusBadRequest)
}

func TestAccountDeletionProxyPreservesProtectionAndEmptyResponse(t *testing.T) {
	const accountID = "0123456789abcdef0123456789abcdef"
	const token = "offline-test-backend-token-with-enough-characters"
	status := http.StatusConflict
	calls := 0
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.Method != http.MethodDelete || r.URL.Path != "/v1/accounts/"+accountID ||
			r.Header.Get("Authorization") != "Bearer "+token {
			t.Error("account deletion was not authenticated or routed correctly")
		}
		w.WriteHeader(status)
		if status == http.StatusConflict {
			_, _ = w.Write([]byte(`{"error":"Cancel or delete this account's jobs before deleting the account"}`))
		}
	}))
	defer upstream.Close()
	app, err := New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer app.Close()
	if err := app.ConfigureBackend(upstream.URL, token); err != nil {
		t.Fatal(err)
	}
	local := httptest.NewServer(app.Handler())
	defer local.Close()
	for _, expected := range []int{http.StatusConflict, http.StatusNoContent} {
		status = expected
		req, _ := http.NewRequest(http.MethodDelete, local.URL+"/api/backend/accounts/"+accountID, nil)
		response, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		raw, _ := io.ReadAll(response.Body)
		response.Body.Close()
		if response.StatusCode != expected {
			t.Fatalf("account delete status %d, expected %d", response.StatusCode, expected)
		}
		if expected == http.StatusNoContent && len(raw) != 0 {
			t.Fatal("successful account deletion must retain its empty response")
		}
		if expected == http.StatusConflict && !bytes.Contains(raw, []byte("Cancel or delete")) {
			t.Fatal("account deletion protection was lost")
		}
	}
	sendJSON(t, http.MethodDelete, local.URL+"/api/backend/accounts/invalid", nil, http.StatusBadRequest)
	if calls != 2 {
		t.Fatal("invalid account deletion reached the backend")
	}
}

func TestAccountCourseDiscoveryProxy(t *testing.T) {
	const accountID = "0123456789abcdef0123456789abcdef"
	const token = "offline-test-backend-token-with-enough-characters"
	var methods []string
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+token {
			t.Error("course proxy did not authenticate")
		}
		if r.URL.Path != "/v1/accounts/"+accountID+"/courses" {
			t.Errorf("unexpected upstream path %s", r.URL.Path)
		}
		methods = append(methods, r.Method)
		w.Header().Set("Content-Type", "application/json")
		if r.Method == http.MethodPost {
			body, _ := io.ReadAll(r.Body)
			if string(body) != "{}" {
				t.Error("course request body was not forwarded")
			}
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte(`{"state":"queued","courses":[]}`))
		} else {
			_, _ = w.Write([]byte(`{"state":"done","courses":[{"id":"101","title":"Offline course"}]}`))
		}
	}))
	defer upstream.Close()
	app, err := New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if err := app.ConfigureBackend(upstream.URL, token); err != nil {
		t.Fatal(err)
	}
	local := httptest.NewServer(app.Handler())
	defer local.Close()
	path := local.URL + "/api/backend/accounts/" + accountID + "/courses"
	started := sendJSON(t, http.MethodPost, path, map[string]string{}, http.StatusAccepted)
	if started["state"] != "queued" {
		t.Fatal("course request did not retain the async response")
	}
	result := getJSON(t, path)
	if result["state"] != "done" || len(result["courses"].([]any)) != 1 {
		t.Fatal("course list was not forwarded")
	}
	if strings.Join(methods, ",") != "POST,GET" {
		t.Fatalf("unexpected methods %v", methods)
	}
	for _, method := range []string{http.MethodGet, http.MethodPost} {
		sendJSON(t, method, local.URL+"/api/backend/accounts/invalid/courses",
			map[string]string{}, http.StatusBadRequest)
	}
	if len(methods) != 2 {
		t.Fatal("invalid account ID reached upstream")
	}
}

func TestAccountCourseDetailProxy(t *testing.T) {
	const accountID = "0123456789abcdef0123456789abcdef"
	const token = "offline-test-backend-token-with-enough-characters"
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/accounts/"+accountID+"/courses/101" {
			t.Errorf("unexpected upstream detail path %s", r.URL.Path)
		}
		if r.Header.Get("Authorization") != "Bearer "+token {
			t.Error("detail proxy did not authenticate")
		}
		w.Header().Set("Content-Type", "application/json")
		if r.Method == http.MethodPost {
			w.WriteHeader(http.StatusAccepted)
			_, _ = w.Write([]byte(`{"state":"queued","account_id":"` + accountID + `","course_id":"101"}`))
			return
		}
		_, _ = w.Write([]byte(`{"state":"done","account_id":"` + accountID + `","course_id":"101","detail":{"points":[]}}`))
	}))
	defer upstream.Close()
	app, err := New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	if err := app.ConfigureBackend(upstream.URL, token); err != nil {
		t.Fatal(err)
	}
	local := httptest.NewServer(app.Handler())
	defer local.Close()
	path := local.URL + "/api/backend/accounts/" + accountID + "/courses/101"
	started := sendJSON(t, http.MethodPost, path, map[string]string{}, http.StatusAccepted)
	if started["course_id"] != "101" {
		t.Fatal("detail request did not preserve course ID")
	}
	result := getJSON(t, path)
	if result["state"] != "done" {
		t.Fatal("course detail was not forwarded")
	}
	sendJSON(t, http.MethodGet, local.URL+"/api/backend/accounts/invalid/courses/101",
		map[string]string{}, http.StatusBadRequest)
	sendJSON(t, http.MethodGet, local.URL+"/api/backend/accounts/"+accountID+"/courses/not-a-course",
		map[string]string{}, http.StatusBadRequest)
}

func TestAIProviderProxyAndConversationQuery(t *testing.T) {
	const token = "offline-test-backend-token-with-enough-characters"
	var seen []string
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+token {
			t.Errorf("provider proxy lost its authentication for %s", r.URL.Path)
		}
		seen = append(seen, r.Method+" "+r.URL.Path)
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/v1/ai/provider":
			if r.Method == http.MethodPut {
				raw, _ := io.ReadAll(r.Body)
				var body map[string]any
				if json.Unmarshal(raw, &body) != nil {
					t.Errorf("provider body was not JSON: %s", raw)
				}
				if body["protocol"] != "responses" || body["model"] != "text-model" {
					t.Errorf("provider body was not forwarded: %s", raw)
				}
				_, _ = w.Write([]byte(`{"configured":true,"protocol":"responses","model":"text-model","has_key":true}`))
				return
			}
			_, _ = w.Write([]byte(`{"configured":true,"protocol":"chat","model":"text-model"}`))
		case "/v1/ai/models":
			if got := r.URL.Query().Get("route"); got != "vision" {
				t.Errorf("model route query = %q", got)
			}
			_, _ = w.Write([]byte(`{"models":[{"id":"text-model"},{"id":"vision-model"}],"protocol":"chat"}`))
		case "/v1/ai/test":
			if r.Method != http.MethodPost {
				t.Errorf("provider test method = %s", r.Method)
			}
			_, _ = w.Write([]byte(`{"ok":true,"model":"text-model","reply":"{}"}`))
		case "/v1/ai/conversations":
			query := r.URL.Query()
			if query.Get("after") != "7" || query.Get("limit") != "20" {
				t.Errorf("conversation query was not preserved: %v", query)
			}
			_, _ = w.Write([]byte(`{"conversations":[{"id":8,"channel":"api"}],"latest":8}`))
		case "/v1/ai/vision-provider":
			raw, _ := io.ReadAll(r.Body)
			var body map[string]any
			_ = json.Unmarshal(raw, &body)
			_, _ = w.Write([]byte(`{"configured":true,"inherited":false,"model":"vision-model"}`))
		case "/v1/ai/vision-bench":
			if r.Method != http.MethodPost {
				t.Errorf("vision bench method = %s", r.Method)
			}
			_, _ = w.Write([]byte(`{"available":1,"graded":1,"correct":1,"accuracy":1}`))
		case "/v1/ai/vision-fallback", "/v1/ai/vision-direct":
			raw, _ := io.ReadAll(r.Body)
			var body map[string]any
			_ = json.Unmarshal(raw, &body)
			if _, ok := body["enabled"]; !ok {
				t.Errorf("vision switch body was not forwarded: %s", raw)
			}
			_, _ = w.Write([]byte(`{"channel":"api","vision_enabled":true}`))
		default:
			w.WriteHeader(http.StatusNotFound)
			_, _ = w.Write([]byte(`{"error":"offline"}`))
		}
	}))
	defer upstream.Close()
	app, err := New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer app.Close()
	if err := app.ConfigureBackend(upstream.URL, token); err != nil {
		t.Fatal(err)
	}
	local := httptest.NewServer(app.Handler())
	defer local.Close()

	if result := getJSON(t, local.URL+"/api/backend/ai/provider"); result["protocol"] != "chat" {
		t.Fatalf("provider read = %#v", result)
	}
	saved := sendJSON(t, http.MethodPut, local.URL+"/api/backend/ai/provider", map[string]any{
		"endpoint": "https://api.example.com/v1", "protocol": "responses",
		"model": "text-model", "vision_model": "", "timeout": 240,
		"model_fallbacks": []string{}, "api_key": "offline-provider-key",
	}, http.StatusOK)
	if saved["protocol"] != "responses" || saved["has_key"] != true {
		t.Fatalf("provider save = %#v", saved)
	}
	models := getJSON(t, local.URL+"/api/backend/ai/models?route=vision")
	if len(models["models"].([]any)) != 2 {
		t.Fatalf("model list = %#v", models)
	}
	if result := sendJSON(t, http.MethodPost, local.URL+"/api/backend/ai/test",
		map[string]any{}, http.StatusOK); result["ok"] != true {
		t.Fatalf("provider test = %#v", result)
	}
	history := getJSON(t, local.URL+"/api/backend/ai/conversations?after=7&limit=20")
	if len(history["conversations"].([]any)) != 1 {
		t.Fatalf("conversation history = %#v", history)
	}
	if vision := getJSON(t, local.URL+"/api/backend/ai/vision-provider"); vision["model"] != "vision-model" {
		t.Fatalf("vision provider read = %#v", vision)
	}
	savedVision := sendJSON(t, http.MethodPut, local.URL+"/api/backend/ai/vision-provider",
		map[string]any{"model": "vision-model"}, http.StatusOK)
	if savedVision["configured"] != true {
		t.Fatalf("vision provider save = %#v", savedVision)
	}
	bench := sendJSON(t, http.MethodPost, local.URL+"/api/backend/ai/vision-bench",
		map[string]any{"limit": 6, "mode": "direct"}, http.StatusOK)
	if bench["accuracy"] != float64(1) {
		t.Fatalf("vision bench = %#v", bench)
	}
	if switched := sendJSON(t, http.MethodPut, local.URL+"/api/backend/ai/vision-fallback",
		map[string]any{"enabled": false}, http.StatusOK); switched["channel"] != "api" {
		t.Fatalf("vision fallback = %#v", switched)
	}
	sendJSON(t, http.MethodPut, local.URL+"/api/backend/ai/vision-direct",
		map[string]any{"enabled": true}, http.StatusOK)
	sendJSON(t, http.MethodGet, local.URL+"/api/backend/ai/conversations?bogus=1",
		map[string]any{}, http.StatusBadRequest)
	sendJSON(t, http.MethodGet, local.URL+"/api/backend/ai/models?route=video",
		map[string]any{}, http.StatusBadRequest)
	want := []string{
		"GET /v1/ai/provider", "PUT /v1/ai/provider", "GET /v1/ai/models",
		"POST /v1/ai/test", "GET /v1/ai/conversations",
		"GET /v1/ai/vision-provider", "PUT /v1/ai/vision-provider", "POST /v1/ai/vision-bench",
		"PUT /v1/ai/vision-fallback", "PUT /v1/ai/vision-direct",
	}
	if !slices.Equal(seen, want) {
		t.Fatalf("provider proxy order = %v", seen)
	}
}
