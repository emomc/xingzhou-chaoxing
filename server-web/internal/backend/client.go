// Package backend connects the local app to an explicitly configured task API.
package backend

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"

	"xingguang/internal/persistence"
)

var ErrUnconfigured = errors.New("尚未连接后端")
var questionImagePath = regexp.MustCompile(`^/v1/(question-bank|image-questions)/[a-f0-9]{32}/images/image_([1-9]|10)$`)

type Config struct {
	URL   string `json:"url"`
	Token string `json:"token"`
}

// Client stores the operator token on this device, never in browser storage.
type Client struct {
	mu   sync.RWMutex
	path string
	cfg  Config
	http *http.Client
	slow *http.Client
	db   *persistence.Database
}

func Open(dir string) (*Client, error) {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.MaxIdleConnsPerHost = 8
	noRedirect := func(_ *http.Request, _ []*http.Request) error {
		return http.ErrUseLastResponse
	}
	c := &Client{
		path: filepath.Join(dir, "backend.json"),
		http: &http.Client{
			Timeout:       15 * time.Second,
			Transport:     transport,
			CheckRedirect: noRedirect,
		},
		slow: &http.Client{
			Timeout:       330 * time.Second,
			Transport:     transport,
			CheckRedirect: noRedirect,
		},
	}
	if persistence.Enabled() {
		db, err := persistence.Open(dir)
		if err != nil {
			return nil, err
		}
		c.db = db
		if err := db.Import("backend", c.path, func(raw []byte) error {
			var cfg Config
			if err := json.Unmarshal(raw, &cfg); err != nil {
				return err
			}
			return validate(cfg)
		}); err != nil {
			_ = db.Close()
			return nil, err
		}
	}
	var raw []byte
	var err error
	if c.db != nil {
		raw, err = c.db.Read("backend")
	} else {
		raw, err = os.ReadFile(c.path)
	}
	if errors.Is(err, os.ErrNotExist) {
		return c, nil
	}
	if err != nil {
		_ = c.Close()
		return nil, errors.New("无法读取后端连接配置")
	}
	if err := json.Unmarshal(raw, &c.cfg); err != nil {
		_ = c.Close()
		return nil, errors.New("后端连接配置已损坏")
	}
	if err := validate(c.cfg); err != nil {
		_ = c.Close()
		return nil, err
	}
	return c, nil
}

func validate(cfg Config) error {
	u, err := url.Parse(cfg.URL)
	if err != nil || u.Hostname() == "" || u.User != nil || u.RawQuery != "" ||
		u.Fragment != "" || (u.Path != "" && u.Path != "/") {
		return errors.New("后端地址应为完整的 HTTP(S) 根地址")
	}
	loopback := u.Hostname() == "localhost"
	if ip := net.ParseIP(u.Hostname()); ip != nil {
		loopback = ip.IsLoopback()
	}
	if u.Scheme != "https" && !(u.Scheme == "http" && loopback) {
		return errors.New("远程后端必须使用 HTTPS；HTTP 仅允许本机回环地址")
	}
	if len(cfg.Token) < 32 || len(cfg.Token) > 512 ||
		strings.ContainsAny(cfg.Token, " \r\n\t") {
		return errors.New("访问令牌必须至少 32 个字符，且不能含空白")
	}
	return nil
}

// Summary deliberately omits the token.
func (c *Client) Summary() map[string]any {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return map[string]any{"url": c.cfg.URL, "configured": c.cfg.URL != ""}
}

func (c *Client) Configure(base, token string) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	base = strings.TrimRight(strings.TrimSpace(base), "/")
	if token == "" && base == c.cfg.URL {
		token = c.cfg.Token
	}
	cfg := Config{URL: base, Token: token}
	if err := validate(cfg); err != nil {
		return err
	}
	raw, err := json.Marshal(cfg)
	if err != nil {
		return err
	}
	if c.db != nil {
		if err := c.db.Write("backend", raw); err != nil {
			return errors.New("无法保存数据库连接配置")
		}
		c.cfg = cfg
		return nil
	}
	tmp, err := os.CreateTemp(filepath.Dir(c.path), ".backend-*.tmp")
	if err != nil {
		return errors.New("无法保存连接配置")
	}
	defer os.Remove(tmp.Name())
	if _, err = tmp.Write(raw); err == nil {
		err = tmp.Sync()
	}
	closeErr := tmp.Close()
	if err != nil || closeErr != nil {
		return errors.New("无法保存连接配置")
	}
	if err := os.Rename(tmp.Name(), c.path); err != nil {
		return errors.New("无法替换连接配置")
	}
	c.cfg = cfg
	return nil
}

func (c *Client) Disconnect() error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.db != nil {
		if err := c.db.Delete("backend"); err != nil {
			return errors.New("无法清除数据库连接配置")
		}
	} else {
		if err := os.Remove(c.path); err != nil && !errors.Is(err, os.ErrNotExist) {
			return errors.New("无法清除连接配置")
		}
	}
	c.cfg = Config{}
	c.http.CloseIdleConnections()
	return nil
}

func (c *Client) Close() error {
	c.http.CloseIdleConnections()
	if c.db != nil {
		return c.db.Close()
	}
	return nil
}

// Request only accepts fixed API paths selected by the local server.
func (c *Client) Request(ctx context.Context, method, path string, body []byte, key string) (int, []byte, error) {
	return c.request(ctx, method, path, body, key, false)
}

// RequestSlow is for provider probes and model listing, which may legitimately
// take longer than the short deadline used by ordinary console calls.
func (c *Client) RequestSlow(ctx context.Context, method, path string, body []byte) (int, []byte, error) {
	return c.request(ctx, method, path, body, "", true)
}

func (c *Client) request(ctx context.Context, method, path string, body []byte, key string, slow bool) (int, []byte, error) {
	c.mu.RLock()
	cfg := c.cfg
	client := c.http
	if slow {
		client = c.slow
	}
	c.mu.RUnlock()
	if cfg.URL == "" {
		return 0, nil, ErrUnconfigured
	}
	target, parseErr := url.ParseRequestURI(path)
	if parseErr != nil || !strings.HasPrefix(path, "/v1/") || strings.Contains(target.Path, "..") {
		return 0, nil, errors.New("不允许的后端路径")
	}
	req, err := http.NewRequestWithContext(ctx, method, cfg.URL+path, bytes.NewReader(body))
	if err != nil {
		return 0, nil, errors.New("无法创建后端请求")
	}
	req.Header.Set("Authorization", "Bearer "+cfg.Token)
	req.Header.Set("Accept", "application/json")
	if len(body) > 0 {
		req.Header.Set("Content-Type", "application/json")
	}
	if key != "" {
		req.Header.Set("Idempotency-Key", key)
	}
	resp, err := client.Do(req)
	if err != nil {
		return 0, nil, errors.New("后端连接失败或超时")
	}
	defer resp.Body.Close()
	limit := int64(2 << 20)
	if method == http.MethodGet && questionImagePath.MatchString(path) {
		limit = 4 << 20
	}
	raw, err := io.ReadAll(io.LimitReader(resp.Body, limit+1))
	if err != nil || int64(len(raw)) > limit {
		return 0, nil, errors.New("后端返回了无效或过大的响应")
	}
	if resp.StatusCode == http.StatusNoContent && len(raw) == 0 {
		return resp.StatusCode, raw, nil
	}
	if !json.Valid(raw) {
		return 0, nil, errors.New("后端返回了无效或过大的响应")
	}
	if resp.StatusCode >= 300 && resp.StatusCode < 400 {
		return 0, nil, errors.New("后端返回重定向，连接已拒绝")
	}
	return resp.StatusCode, raw, nil
}
