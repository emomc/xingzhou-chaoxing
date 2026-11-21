package server

import (
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// ConfigureAIWindows keeps the AI administration credential on the loopback proxy.
func (s *Server) ConfigureAIWindows(rawURL, token string) error {
	u, err := url.Parse(rawURL)
	if err != nil || u.Scheme != "http" || u.User != nil || u.RawQuery != "" ||
		u.Fragment != "" || (u.Path != "" && u.Path != "/") || u.Opaque != "" {
		return errors.New("invalid AI display address")
	}
	ip := net.ParseIP(u.Hostname())
	if ip == nil || !ip.IsLoopback() || strings.TrimSpace(token) == "" ||
		strings.ContainsAny(token, "\r\n") {
		return errors.New("AI display requires a loopback address and private token")
	}
	s.mu.Lock()
	s.aiWindowsURL = strings.TrimRight(u.String(), "/")
	s.aiWindowsToken = token
	s.mu.Unlock()
	return nil
}

func (s *Server) aiWindowRoutes() {
	s.mux.HandleFunc("GET /api/ai-windows", s.proxyAIWindow)
	s.mux.HandleFunc("GET /api/ai-windows/{provider}/frame", s.proxyAIWindow)
	s.mux.HandleFunc("POST /api/ai-windows/{provider}/input", s.proxyAIWindowInput)
}

func (s *Server) proxyAIWindow(w http.ResponseWriter, r *http.Request) {
	if r.URL.RawQuery != "" {
		writeError(w, http.StatusBadRequest, "AI 窗口不接受查询参数")
		return
	}
	path := "/admin/browser-windows"
	isFrame := r.PathValue("provider") != ""
	if isFrame {
		provider := r.PathValue("provider")
		if provider != "deepseek" && provider != "doubao" {
			writeError(w, http.StatusBadRequest, "未知的 AI 窗口")
			return
		}
		path += "/" + provider + "/frame"
	}
	s.mu.RLock()
	root, token := s.aiWindowsURL, s.aiWindowsToken
	s.mu.RUnlock()
	if root == "" {
		writeError(w, http.StatusServiceUnavailable, "服务器尚未配置 AI 窗口")
		return
	}
	req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, root+path, nil)
	if err != nil {
		writeError(w, http.StatusBadGateway, "AI 窗口连接失败")
		return
	}
	req.Header.Set("Authorization", "Bearer "+token)
	client := &http.Client{
		Timeout:   7 * time.Second,
		Transport: aiWindowTransport,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}
	response, err := client.Do(req)
	if err != nil {
		writeError(w, http.StatusBadGateway, "AI 窗口暂不可用")
		return
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		writeError(w, http.StatusServiceUnavailable, "AI 窗口未就绪，稍后自动重连")
		return
	}
	contentType := response.Header.Get("Content-Type")
	if (isFrame && contentType != "image/jpeg") ||
		(!isFrame && !strings.HasPrefix(contentType, "application/json")) {
		writeError(w, http.StatusBadGateway, "AI 窗口响应异常")
		return
	}
	limit := int64(32 << 10)
	if isFrame {
		limit = 4 << 20
	}
	raw, err := io.ReadAll(io.LimitReader(response.Body, limit+1))
	if err != nil || int64(len(raw)) > limit {
		writeError(w, http.StatusBadGateway, "AI 窗口响应异常")
		return
	}
	w.Header().Set("Content-Type", contentType)
	w.Header().Set("Referrer-Policy", "no-referrer")
	if isFrame {
		w.Header().Set("X-Frame-Captured-At", response.Header.Get("X-Frame-Captured-At"))
	}
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(raw)
}

func (s *Server) proxyAIWindowInput(w http.ResponseWriter, r *http.Request) {
	if r.URL.RawQuery != "" {
		writeError(w, http.StatusBadRequest, "AI 窗口不接受查询参数")
		return
	}
	provider := r.PathValue("provider")
	if provider != "deepseek" && provider != "doubao" {
		writeError(w, http.StatusBadRequest, "未知的 AI 窗口")
		return
	}
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 4096))
	if err != nil {
		writeError(w, http.StatusRequestEntityTooLarge, "操作内容过大")
		return
	}
	var body map[string]any
	if len(raw) == 0 || json.Unmarshal(raw, &body) != nil || body == nil {
		writeError(w, http.StatusBadRequest, "操作内容不正确")
		return
	}
	s.mu.RLock()
	root, token := s.aiWindowsURL, s.aiWindowsToken
	s.mu.RUnlock()
	if root == "" {
		writeError(w, http.StatusServiceUnavailable, "服务器尚未配置 AI 窗口")
		return
	}
	path := "/admin/browser-windows/" + provider + "/input"
	req, err := http.NewRequestWithContext(r.Context(), http.MethodPost, root+path, strings.NewReader(string(raw)))
	if err != nil {
		writeError(w, http.StatusBadGateway, "AI 窗口连接失败")
		return
	}
	req.Header.Set("Authorization", "Bearer "+token)
	req.Header.Set("Content-Type", "application/json")
	response, err := (&http.Client{
		Timeout:   8 * time.Second,
		Transport: aiWindowTransport,
		CheckRedirect: func(_ *http.Request, _ []*http.Request) error {
			return http.ErrUseLastResponse
		},
	}).Do(req)
	if err != nil {
		writeError(w, http.StatusBadGateway, "AI 窗口暂不可用")
		return
	}
	defer response.Body.Close()
	data, err := io.ReadAll(io.LimitReader(response.Body, 16<<10))
	if err != nil {
		writeError(w, http.StatusBadGateway, "AI 窗口响应异常")
		return
	}
	if response.StatusCode < 200 || response.StatusCode >= 300 {
		status := response.StatusCode
		message := "AI 窗口操作未生效"
		switch status {
		case http.StatusConflict:
			message = "AI 正在处理，暂不可操作"
		case http.StatusBadRequest:
			message = "操作内容不正确"
		case http.StatusTooManyRequests:
			message = "操作过于频繁，请稍后再试"
		case http.StatusServiceUnavailable:
			message = "AI 窗口未就绪"
		default:
			status = http.StatusBadGateway
		}
		writeError(w, status, message)
		return
	}
	if !json.Valid(data) {
		writeError(w, http.StatusBadGateway, "AI 窗口响应异常")
		return
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(data)
}

var aiWindowTransport = &http.Transport{
	Proxy: nil,
	DialContext: (&net.Dialer{
		Timeout: 2 * time.Second, KeepAlive: 30 * time.Second,
	}).DialContext,
	MaxIdleConns: 8, MaxIdleConnsPerHost: 8,
	IdleConnTimeout: 60 * time.Second, ResponseHeaderTimeout: 6 * time.Second,
}
