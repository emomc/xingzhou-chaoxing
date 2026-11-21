// Package server exposes the local item store over HTTP on this machine only.
package server

import (
	"embed"
	"encoding/json"
	"errors"
	"io"
	"io/fs"
	"net"
	"net/http"
	"net/url"
	"strings"
	"sync"

	"xingguang/internal/auth"
	"xingguang/internal/backend"
	"xingguang/internal/store"
)

//go:embed static/*
var staticFS embed.FS

// Server serves the API and the bundled pages.
type Server struct {
	store          *store.Store
	backend        *backend.Client
	mux            *http.ServeMux
	mu             sync.RWMutex
	onShutdown     func()
	aiWindowsURL   string
	aiWindowsToken string
	authStore      *auth.Store
	authRequired   bool
	loginMu        sync.Mutex
	loginAttempts  map[string]*loginAttempt
	quotaMu        sync.Mutex
}

type itemBody struct {
	Title  string `json:"title"`
	Detail string `json:"detail"`
	Status string `json:"status"`
}

// New opens the store in dataDir and prepares routes.
func New(dataDir string) (*Server, error) {
	return newServer(dataDir, "", "", false)
}

// NewWithOwner enables application login and seeds the owner on first start.
func NewWithOwner(dataDir, ownerUsername, ownerPassword string) (*Server, error) {
	return newServer(dataDir, ownerUsername, ownerPassword, true)
}

func newServer(dataDir, ownerUsername, ownerPassword string, authRequired bool) (*Server, error) {
	st, err := store.Open(dataDir)
	if err != nil {
		return nil, err
	}
	client, err := backend.Open(dataDir)
	if err != nil {
		_ = st.Close()
		return nil, err
	}
	s := &Server{
		store: st, backend: client, mux: http.NewServeMux(),
		authRequired: authRequired, loginAttempts: make(map[string]*loginAttempt),
	}
	if authRequired {
		s.authStore, err = auth.Open(dataDir, ownerUsername, ownerPassword)
		if err != nil {
			_ = client.Close()
			_ = st.Close()
			return nil, err
		}
	}
	s.routes()
	return s, nil
}

func (s *Server) Close() error {
	if s.authStore != nil {
		return errors.Join(s.authStore.Close(), s.backend.Close(), s.store.Close())
	}
	return errors.Join(s.backend.Close(), s.store.Close())
}

// Handler is the HTTP handler for the app.
func (s *Server) Handler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		w.Header().Set("X-Content-Type-Options", "nosniff")
		host := r.Host
		if value, _, err := net.SplitHostPort(host); err == nil {
			host = value
		}
		host = strings.Trim(host, "[]")
		ip := net.ParseIP(host)
		if !strings.EqualFold(host, "localhost") && (ip == nil || !ip.IsLoopback()) {
			writeError(w, http.StatusForbidden, "不允许的本机地址")
			return
		}
		if origin := r.Header.Get("Origin"); origin != "" {
			u, err := url.Parse(origin)
			if err != nil || u.Host != r.Host {
				writeError(w, http.StatusForbidden, "不允许跨站请求")
				return
			}
		}
		if !s.authRequired {
			s.mux.ServeHTTP(w, r)
			return
		}
		if r.URL.Path == "/api/session" {
			s.mux.ServeHTTP(w, r)
			return
		}
		user, ok := s.sessionUser(r)
		if !ok {
			if strings.HasPrefix(r.URL.Path, "/api/") {
				writeError(w, http.StatusUnauthorized, "请先登录")
				return
			}
			if r.URL.Path == "/" || r.URL.Path == "/index.html" {
				http.Redirect(w, r, "/login.html", http.StatusSeeOther)
				return
			}
			s.mux.ServeHTTP(w, r)
			return
		}
		if r.URL.Path == "/login.html" {
			http.Redirect(w, r, "/", http.StatusSeeOther)
			return
		}
		if strings.HasPrefix(r.URL.Path, "/api/") && !user.IsOwner() && !regularRouteAllowed(r) {
			writeError(w, http.StatusForbidden, "当前用户无权访问该功能")
			return
		}
		s.mux.ServeHTTP(w, s.withUser(r, user))
	})
}

// SetShutdown registers a function called after POST /api/shutdown responds.
func (s *Server) SetShutdown(fn func()) {
	s.mu.Lock()
	s.onShutdown = fn
	s.mu.Unlock()
}

func (s *Server) routes() {
	if s.authRequired {
		s.authRoutes()
	}
	s.backendRoutes()
	s.aiWindowRoutes()
	s.mux.HandleFunc("GET /api/health", s.health)
	s.mux.HandleFunc("GET /api/items", s.list)
	s.mux.HandleFunc("POST /api/items", s.create)
	s.mux.HandleFunc("PUT /api/items/{id}", s.update)
	s.mux.HandleFunc("DELETE /api/items/{id}", s.remove)
	s.mux.HandleFunc("POST /api/shutdown", s.shutdown)
	sub, err := fs.Sub(staticFS, "static")
	if err != nil {
		panic(err)
	}
	s.mux.Handle("GET /", noCache(http.FileServer(http.FS(sub))))
}

func (s *Server) health(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"status": "ok", "name": "xingguang", "storage": s.store.StorageSummary(),
	})
}

func (s *Server) list(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"items": s.store.List()})
}

func (s *Server) create(w http.ResponseWriter, r *http.Request) {
	body, err := readBody(w, r)
	if err != nil {
		writeError(w, http.StatusBadRequest, "无法读取内容")
		return
	}
	item, err := s.store.Create(body.Title, body.Detail, body.Status)
	if err != nil {
		writeStoreError(w, err)
		return
	}
	writeJSON(w, http.StatusCreated, item)
}

func (s *Server) update(w http.ResponseWriter, r *http.Request) {
	body, err := readBody(w, r)
	if err != nil {
		writeError(w, http.StatusBadRequest, "无法读取内容")
		return
	}
	item, err := s.store.Update(r.PathValue("id"), body.Title, body.Detail, body.Status)
	if err != nil {
		writeStoreError(w, err)
		return
	}
	writeJSON(w, http.StatusOK, item)
}

func (s *Server) remove(w http.ResponseWriter, r *http.Request) {
	if err := s.store.Delete(r.PathValue("id")); err != nil {
		writeStoreError(w, err)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) shutdown(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
	s.mu.RLock()
	fn := s.onShutdown
	s.mu.RUnlock()
	if fn != nil {
		go fn()
	}
}

func readBody(w http.ResponseWriter, r *http.Request) (itemBody, error) {
	defer r.Body.Close()
	raw, err := io.ReadAll(io.LimitReader(r.Body, 1<<20))
	if err != nil {
		return itemBody{}, err
	}
	if len(raw) == 0 {
		return itemBody{}, errors.New("empty")
	}
	var body itemBody
	if err := json.Unmarshal(raw, &body); err != nil {
		return itemBody{}, err
	}
	return body, nil
}

func writeStoreError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, store.ErrNotFound):
		writeError(w, http.StatusNotFound, "没有这条事项")
	case errors.Is(err, store.ErrInvalid):
		writeError(w, http.StatusBadRequest, "标题或状态不正确")
	default:
		writeError(w, http.StatusInternalServerError, "保存失败")
	}
}

func writeError(w http.ResponseWriter, code int, message string) {
	writeJSON(w, code, map[string]string{"error": message})
}

func writeJSON(w http.ResponseWriter, code int, value any) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	_ = json.NewEncoder(w).Encode(value)
}

type cacheOff struct{ next http.Handler }

func noCache(next http.Handler) http.Handler { return cacheOff{next: next} }

func (c cacheOff) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "no-store")
	c.next.ServeHTTP(w, r)
}
