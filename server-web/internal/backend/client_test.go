package backend

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const testToken = "offline-test-token-at-least-thirty-two-characters"

func TestConfigurationIsPrivateAndSurvivesReopen(t *testing.T) {
	dir := t.TempDir()
	client, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	if err := client.Configure("http://127.0.0.1:3100/", testToken); err != nil {
		t.Fatal(err)
	}
	summary, _ := json.Marshal(client.Summary())
	if strings.Contains(string(summary), testToken) {
		t.Fatal("token was included in the public summary")
	}
	if err := client.Configure("http://127.0.0.1:3100", testToken+"-updated"); err != nil {
		t.Fatal(err)
	}
	reopened, err := Open(dir)
	if err != nil {
		t.Fatal(err)
	}
	if reopened.cfg.Token != testToken+"-updated" {
		t.Fatal("updated token was not saved")
	}
	if err := reopened.Configure("http://127.0.0.1:3100", ""); err != nil {
		t.Fatal(err)
	}
	if err := reopened.Disconnect(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(dir, "backend.json")); !os.IsNotExist(err) {
		t.Fatal("configuration was not removed")
	}
}

func TestUnsafeURLsAndTokensAreRejected(t *testing.T) {
	for _, base := range []string{
		"http://example.org", "file:///etc/passwd", "https://user:secret@example.org",
		"https://example.org?token=x", "https://example.org/v1", "http://192.168.1.10:3100",
	} {
		if err := validate(Config{URL: base, Token: testToken}); err == nil {
			t.Fatalf("accepted unsafe URL %s", base)
		}
	}
	for _, token := range []string{"short", testToken + "\n"} {
		if err := validate(Config{URL: "https://example.org", Token: token}); err == nil {
			t.Fatal("accepted invalid token")
		}
	}
}

func TestProxyAuthenticationBodyAndIdempotency(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+testToken {
			t.Error("missing upstream authentication")
		}
		if r.Header.Get("Idempotency-Key") != "offline-key" {
			t.Error("missing idempotency key")
		}
		if r.URL.Path != "/v1/jobs" || r.Method != http.MethodPost {
			t.Error("wrong upstream route")
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		_, _ = w.Write([]byte(`{"status":"queued"}`))
	}))
	defer upstream.Close()
	client, _ := Open(t.TempDir())
	if err := client.Configure(upstream.URL, testToken); err != nil {
		t.Fatal(err)
	}
	code, raw, err := client.Request(context.Background(), "POST", "/v1/jobs", []byte(`{}`), "offline-key")
	if err != nil || code != http.StatusCreated || !strings.Contains(string(raw), "queued") {
		t.Fatalf("proxy response %d %s %v", code, raw, err)
	}
}

func TestRedirectNeverForwardsToken(t *testing.T) {
	called := false
	destination := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		called = true
		_, _ = w.Write([]byte(`{}`))
	}))
	defer destination.Close()
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Location", destination.URL)
		w.WriteHeader(http.StatusTemporaryRedirect)
		_, _ = w.Write([]byte(`{}`))
	}))
	defer upstream.Close()
	client, _ := Open(t.TempDir())
	_ = client.Configure(upstream.URL, testToken)
	_, _, err := client.Request(context.Background(), "GET", "/v1/health", nil, "")
	if err == nil || called {
		t.Fatalf("redirect error %v destination called %v", err, called)
	}
}

func TestEmptyDeletionResponseIsAcceptedButEmptyJSONResponseIsRejected(t *testing.T) {
	status := http.StatusNoContent
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(status)
	}))
	defer upstream.Close()
	client, err := Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	_ = client.Configure(upstream.URL, testToken)
	code, raw, err := client.Request(context.Background(), http.MethodDelete, "/v1/accounts/offline", nil, "")
	if err != nil || code != http.StatusNoContent || len(raw) != 0 {
		t.Fatalf("empty deletion response %d %s %v", code, raw, err)
	}
	status = http.StatusOK
	if _, _, err := client.Request(context.Background(), http.MethodGet, "/v1/accounts", nil, ""); err == nil {
		t.Fatal("an empty success response must not bypass JSON validation")
	}
}

func TestPathValidationIgnoresSearchContentButRejectsTraversal(t *testing.T) {
	calls := 0
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		calls++
		if r.URL.Query().Get("search") != "A..B /v1/jobs" {
			t.Error("search query changed")
		}
		_, _ = w.Write([]byte(`{}`))
	}))
	defer upstream.Close()
	client, err := Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	_ = client.Configure(upstream.URL, testToken)
	_, _, err = client.Request(context.Background(), "GET", "/v1/question-bank?search=A..B+%2Fv1%2Fjobs", nil, "")
	if err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{
		"/v1/../healthz", "/v1/%2e%2e/healthz", "https://other.invalid/v1/jobs",
		"//other.invalid/v1/jobs", "/healthz", "/v1/%zz",
	} {
		if _, _, err := client.Request(context.Background(), "GET", path, nil, ""); err == nil {
			t.Errorf("accepted unsafe path %s", path)
		}
	}
	if calls != 1 {
		t.Fatalf("unsafe path reached upstream: %d", calls)
	}
}

func TestOnlyQuestionImagesReceiveALargerBoundedResponseLimit(t *testing.T) {
	length := 3 << 20
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+testToken {
			t.Error("image request was not authenticated")
		}
		_, _ = w.Write([]byte(`{"data_url":"` + strings.Repeat("x", length) + `"}`))
	}))
	defer upstream.Close()
	client, err := Open(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer client.Close()
	if err := client.Configure(upstream.URL, testToken); err != nil {
		t.Fatal(err)
	}
	imagePath := "/v1/question-bank/" + strings.Repeat("a", 32) + "/images/image_1"
	code, raw, err := client.Request(context.Background(), http.MethodGet, imagePath, nil, "")
	if err != nil || code != http.StatusOK || len(raw) <= 2<<20 {
		t.Fatalf("bounded image response was rejected: code=%d, size=%d, error=%v", code, len(raw), err)
	}
	reviewPath := strings.Replace(imagePath, "question-bank", "image-questions", 1)
	if _, _, err := client.Request(context.Background(), http.MethodGet, reviewPath, nil, ""); err != nil {
		t.Fatalf("review image response was rejected: %v", err)
	}
	for _, path := range []string{
		"/v1/jobs", imagePath + "?extra=true", strings.Replace(imagePath, "image_1", "image_11", 1),
		reviewPath + "?extra=true", strings.Replace(reviewPath, "image_1", "image_11", 1),
	} {
		if _, _, err := client.Request(context.Background(), http.MethodGet, path, nil, ""); err == nil {
			t.Fatalf("normal response limit was relaxed for %s", path)
		}
	}
	length = 5 << 20
	if _, _, err := client.Request(context.Background(), http.MethodGet, imagePath, nil, ""); err == nil {
		t.Fatal("unbounded image response was accepted")
	}
}
