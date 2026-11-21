package server

import (
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestAIDisplayPrivateAuthenticatedProxy(t *testing.T) {
	const token = "test-private-ai-window-token"
	var paths []string
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		paths = append(paths, r.URL.Path)
		if r.Header.Get("Authorization") != "Bearer "+token ||
			r.Header.Get("Cookie") != "" || r.Header.Get("X-Forwarded-Host") != "" {
			t.Error("display authentication or client header isolation failed")
		}
		if r.URL.Path == "/admin/browser-windows" {
			writeJSON(w, 200, map[string]any{"windows": []string{"deepseek", "doubao"}})
		} else {
			w.Header().Set("Content-Type", "image/jpeg")
			w.Header().Set("X-Frame-Captured-At", "1000")
			_, _ = w.Write([]byte("frame:" + r.URL.Path))
		}
	}))
	defer upstream.Close()
	app, err := New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer app.Close()
	if err := app.ConfigureAIWindows(upstream.URL, token); err != nil {
		t.Fatal(err)
	}
	local := httptest.NewServer(app.Handler())
	defer local.Close()
	getJSON(t, local.URL+"/api/ai-windows")
	for _, provider := range []string{"deepseek", "doubao"} {
		req, _ := http.NewRequest(http.MethodGet, local.URL+"/api/ai-windows/"+provider+"/frame", nil)
		req.Header.Set("Cookie", "must-not-forward")
		response, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		body, _ := io.ReadAll(response.Body)
		response.Body.Close()
		if response.StatusCode != 200 || response.Header.Get("Content-Type") != "image/jpeg" ||
			response.Header.Get("Cache-Control") != "no-store" ||
			response.Header.Get("X-Frame-Captured-At") != "1000" ||
			!strings.Contains(string(body), provider) || strings.Contains(string(body), token) {
			t.Fatal("invalid browser display proxy response")
		}
	}
	for _, path := range []string{
		"/api/ai-windows?token=x", "/api/ai-windows/deepseek/frame?url=http://example.com",
		"/api/ai-windows/other/frame",
	} {
		sendJSON(t, http.MethodGet, local.URL+path, nil, http.StatusBadRequest)
	}
	for _, method := range []string{http.MethodPost, http.MethodPut, http.MethodDelete} {
		req, _ := http.NewRequest(method, local.URL+"/api/ai-windows/doubao/frame", nil)
		response, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		response.Body.Close()
		if response.StatusCode != http.StatusMethodNotAllowed {
			t.Fatal("AI browser display accepted a mutating method")
		}
	}
	if len(paths) != 3 {
		t.Fatal("invalid display request reached the AI service")
	}
}

func TestAIWindowInputProxyIsFixedAndAuthenticated(t *testing.T) {
	const token = "test-private-ai-input-token"
	var received string
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost || r.URL.Path != "/admin/browser-windows/doubao/input" {
			t.Error("unexpected AI input path")
		}
		if r.Header.Get("Authorization") != "Bearer "+token ||
			r.Header.Get("Content-Type") != "application/json" ||
			r.Header.Get("Cookie") != "" {
			t.Error("AI input authentication or header isolation failed")
		}
		raw, _ := io.ReadAll(r.Body)
		received = string(raw)
		writeJSON(w, http.StatusOK, map[string]any{"ok": true})
	}))
	defer upstream.Close()
	app, err := New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer app.Close()
	if err := app.ConfigureAIWindows(upstream.URL, token); err != nil {
		t.Fatal(err)
	}
	local := httptest.NewServer(app.Handler())
	defer local.Close()

	req, _ := http.NewRequest(http.MethodPost, local.URL+"/api/ai-windows/doubao/input",
		strings.NewReader(`{"type":"click","x":0.5,"y":0.5}`))
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Cookie", "must-not-forward")
	response, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	response.Body.Close()
	if response.StatusCode != http.StatusOK || received != `{"type":"click","x":0.5,"y":0.5}` {
		t.Fatal("AI window input was not forwarded exactly")
	}
	for _, path := range []string{
		"/api/ai-windows/deepseek/input?token=x", "/api/ai-windows/other/input",
	} {
		sendJSON(t, http.MethodPost, local.URL+path, map[string]any{"type": "click"}, http.StatusBadRequest)
	}
	invalidMethod, err := http.Get(local.URL + "/api/ai-windows/doubao/input")
	if err != nil {
		t.Fatal(err)
	}
	invalidMethod.Body.Close()
	if invalidMethod.StatusCode != http.StatusNotFound {
		t.Fatal("AI window input accepted GET")
	}
	sendJSON(t, http.MethodPost, local.URL+"/api/ai-windows/doubao/input", "not-an-object", http.StatusBadRequest)
}

func TestAIWindowErrorsNeverExposeUpstreamDetailsOrFollowRedirects(t *testing.T) {
	var mode string
	leaked := false
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		leaked = true
	}))
	defer target.Close()
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch mode {
		case "redirect":
			http.Redirect(w, r, target.URL, http.StatusFound)
		case "oversized":
			w.Header().Set("Content-Type", "image/jpeg")
			_, _ = w.Write([]byte(strings.Repeat("a", (4<<20)+1)))
		case "invalid":
			w.Header().Set("Content-Type", "text/html")
			_, _ = w.Write([]byte("private-upstream-details"))
		default:
			w.WriteHeader(http.StatusUnauthorized)
			_, _ = w.Write([]byte("private-upstream-details"))
		}
	}))
	defer upstream.Close()
	app, err := New(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer app.Close()
	local := httptest.NewServer(app.Handler())
	defer local.Close()
	sendJSON(t, http.MethodGet, local.URL+"/api/ai-windows", nil, http.StatusServiceUnavailable)
	for _, invalid := range []string{
		"http://localhost:3000", "http://example.com", "https://127.0.0.1",
		"http://127.0.0.1/admin", "http://user:pass@127.0.0.1",
		"http://127.0.0.1?token=x", "http://127.0.0.1/#token",
	} {
		if app.ConfigureAIWindows(invalid, "test-token") == nil {
			t.Fatal("invalid AI display address accepted")
		}
	}
	if app.ConfigureAIWindows(upstream.URL, "test-token") != nil {
		t.Fatal("valid configuration rejected")
	}
	for _, value := range []string{"redirect", "oversized", "invalid", "unauthorized"} {
		mode = value
		want := http.StatusServiceUnavailable
		if value == "oversized" || value == "invalid" {
			want = http.StatusBadGateway
		}
		body := sendJSON(t, http.MethodGet, local.URL+"/api/ai-windows/doubao/frame", nil, want)
		if strings.Contains(body["error"].(string), "private") {
			t.Fatal("upstream error details leaked")
		}
	}
	if leaked {
		t.Fatal("display proxy followed an upstream redirect")
	}
}
