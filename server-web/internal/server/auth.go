package server

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"strings"
	"time"

	"xingguang/internal/auth"
)

const sessionCookie = "xingguang_session"

type contextKey string

const userKey contextKey = "user"

type loginAttempt struct {
	count   int
	resetAt time.Time
}

func userFrom(r *http.Request) (auth.User, bool) {
	user, ok := r.Context().Value(userKey).(auth.User)
	return user, ok
}

func (s *Server) sessionUser(r *http.Request) (auth.User, bool) {
	if s.authRequired == false || s.authStore == nil {
		return auth.User{Username: "owner", Role: auth.RoleOwner, Status: auth.StatusActive}, false
	}
	cookie, err := r.Cookie(sessionCookie)
	if err != nil {
		return auth.User{}, false
	}
	user, err := s.authStore.SessionUser(cookie.Value)
	return user, err == nil
}

func userView(user auth.User) map[string]any {
	return map[string]any{
		"id": user.ID, "username": user.Username, "role": user.Role, "status": user.Status,
		"credits": user.Credits, "plan_expires_at": user.PlanExpiresAt,
		"plan_course_limit": user.PlanCourseLimit,
		"plan_active":       user.PlanActive(time.Now()),
		"account_ids":       user.AccountIDs,
		"created_at":        user.CreatedAt, "updated_at": user.UpdatedAt,
	}
}

func (s *Server) authRoutes() {
	s.mux.HandleFunc("GET /api/session", s.session)
	s.mux.HandleFunc("POST /api/session", s.login)
	s.mux.HandleFunc("DELETE /api/session", s.logout)
	s.mux.HandleFunc("PUT /api/session/password", s.changePassword)
	s.mux.HandleFunc("GET /api/users", s.listUsers)
	s.mux.HandleFunc("POST /api/users", s.createUser)
	s.mux.HandleFunc("PUT /api/users/{id}", s.updateUser)
	s.mux.HandleFunc("DELETE /api/users/{id}", s.deleteUser)
	s.mux.HandleFunc("PUT /api/users/{id}/accounts", s.assignUserAccounts)
}

func (s *Server) session(w http.ResponseWriter, r *http.Request) {
	user, ok := s.sessionUser(r)
	if !ok {
		writeError(w, http.StatusUnauthorized, "请先登录")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"user": userView(user)})
}

func (s *Server) login(w http.ResponseWriter, r *http.Request) {
	if r.URL.RawQuery != "" {
		writeError(w, http.StatusBadRequest, "登录参数不正确")
		return
	}
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 8<<10))
	if err != nil {
		writeError(w, http.StatusBadRequest, "登录参数不正确")
		return
	}
	var body struct {
		Username string `json:"username"`
		Password string `json:"password"`
	}
	if json.Unmarshal(raw, &body) != nil {
		writeError(w, http.StatusBadRequest, "登录参数不正确")
		return
	}
	key := strings.ToLower(strings.TrimSpace(body.Username)) + "|" + remoteIP(r)
	now := time.Now()
	s.loginMu.Lock()
	attempt := s.loginAttempts[key]
	if attempt != nil && now.After(attempt.resetAt) {
		delete(s.loginAttempts, key)
		attempt = nil
	}
	if attempt != nil && attempt.count >= 5 {
		s.loginMu.Unlock()
		writeError(w, http.StatusTooManyRequests, "登录尝试过多，请稍后重试")
		return
	}
	s.loginMu.Unlock()

	user, err := s.authStore.Authenticate(body.Username, body.Password)
	if err != nil {
		s.loginMu.Lock()
		attempt = s.loginAttempts[key]
		if attempt == nil {
			attempt = &loginAttempt{resetAt: now.Add(15 * time.Minute)}
			s.loginAttempts[key] = attempt
		}
		attempt.count++
		s.loginMu.Unlock()
		writeError(w, http.StatusUnauthorized, "用户名或密码错误")
		return
	}
	s.loginMu.Lock()
	delete(s.loginAttempts, key)
	s.loginMu.Unlock()
	token, expires, err := s.authStore.CreateSession(user.ID)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "无法创建登录会话")
		return
	}
	http.SetCookie(w, &http.Cookie{
		Name: sessionCookie, Value: token, Path: "/", Expires: expires,
		MaxAge: int(time.Until(expires).Seconds()), HttpOnly: true,
		Secure: secureRequest(r), SameSite: http.SameSiteStrictMode,
	})
	writeJSON(w, http.StatusOK, map[string]any{"user": userView(user)})
}

func (s *Server) logout(w http.ResponseWriter, r *http.Request) {
	if cookie, err := r.Cookie(sessionCookie); err == nil {
		_ = s.authStore.DeleteSession(cookie.Value)
	}
	http.SetCookie(w, &http.Cookie{
		Name: sessionCookie, Value: "", Path: "/", MaxAge: -1,
		HttpOnly: true, Secure: secureRequest(r), SameSite: http.SameSiteStrictMode,
	})
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) changePassword(w http.ResponseWriter, r *http.Request) {
	user, ok := userFrom(r)
	if !ok {
		writeError(w, http.StatusUnauthorized, "请先登录")
		return
	}
	var body struct {
		CurrentPassword string `json:"current_password"`
		NewPassword     string `json:"new_password"`
	}
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 8<<10))
	if err != nil || json.Unmarshal(raw, &body) != nil {
		writeError(w, http.StatusBadRequest, "密码参数不正确")
		return
	}
	if _, err := s.authStore.Authenticate(user.Username, body.CurrentPassword); err != nil {
		writeError(w, http.StatusForbidden, "当前密码不正确")
		return
	}
	status := user.Status
	expires, limit := user.PlanExpiresAt, user.PlanCourseLimit
	if _, err := s.authStore.UpdateUser(user.ID, body.NewPassword, status,
		user.Credits, expires, limit); err != nil {
		writeError(w, http.StatusBadRequest, "新密码不符合要求")
		return
	}
	writeJSON(w, http.StatusOK, map[string]bool{"ok": true})
}

func (s *Server) requireOwner(w http.ResponseWriter, r *http.Request) (auth.User, bool) {
	user, ok := userFrom(r)
	if !ok || !user.IsOwner() {
		writeError(w, http.StatusForbidden, "只有最高管理员可以执行此操作")
		return auth.User{}, false
	}
	return user, true
}

func (s *Server) listUsers(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.requireOwner(w, r); !ok {
		return
	}
	users, err := s.authStore.ListUsers()
	if err != nil {
		writeError(w, http.StatusInternalServerError, "无法读取用户列表")
		return
	}
	result := make([]map[string]any, 0, len(users))
	for _, user := range users {
		result = append(result, userView(user))
	}
	writeJSON(w, http.StatusOK, map[string]any{"users": result})
}

type userPayload struct {
	Username        string   `json:"username"`
	Password        string   `json:"password"`
	Credits         int      `json:"credits"`
	PlanExpiresAt   *float64 `json:"plan_expires_at"`
	PlanCourseLimit *int     `json:"plan_course_limit"`
	Status          string   `json:"status"`
}

func readUserPayload(w http.ResponseWriter, r *http.Request) (userPayload, bool) {
	var body userPayload
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 16<<10))
	if err != nil || json.Unmarshal(raw, &body) != nil {
		writeError(w, http.StatusBadRequest, "用户参数不正确")
		return body, false
	}
	if body.PlanExpiresAt != nil && *body.PlanExpiresAt <= float64(time.Now().Unix()) {
		writeError(w, http.StatusBadRequest, "月卡到期时间必须晚于当前时间")
		return body, false
	}
	return body, true
}

func (s *Server) createUser(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.requireOwner(w, r); !ok {
		return
	}
	body, ok := readUserPayload(w, r)
	if !ok {
		return
	}
	if body.Credits < 0 {
		writeError(w, http.StatusBadRequest, "可用课程数不能为负数")
		return
	}
	user, err := s.authStore.CreateUser(body.Username, body.Password, auth.RoleUser,
		body.Credits, body.PlanExpiresAt, body.PlanCourseLimit)
	if err != nil {
		if errors.Is(err, auth.ErrConflict) {
			writeError(w, http.StatusConflict, "用户名已存在")
		} else {
			writeError(w, http.StatusBadRequest, "用户参数不正确")
		}
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{"user": userView(user)})
}

func (s *Server) updateUser(w http.ResponseWriter, r *http.Request) {
	owner, ok := s.requireOwner(w, r)
	if !ok {
		return
	}
	target, err := s.authStore.User(r.PathValue("id"))
	if err != nil {
		writeError(w, http.StatusNotFound, "用户不存在")
		return
	}
	body, ok := readUserPayload(w, r)
	if !ok {
		return
	}
	if target.IsOwner() {
		if target.ID != owner.ID {
			writeError(w, http.StatusForbidden, "不能修改最高管理员")
			return
		}
		writeError(w, http.StatusBadRequest, "最高管理员请使用修改密码功能")
		return
	}
	status := body.Status
	if status == "" {
		status = target.Status
	}
	s.quotaMu.Lock()
	user, err := s.authStore.UpdateUser(target.ID, body.Password, status,
		body.Credits, body.PlanExpiresAt, body.PlanCourseLimit)
	s.quotaMu.Unlock()
	if err != nil {
		if errors.Is(err, auth.ErrDenied) {
			writeError(w, http.StatusForbidden, "不能禁用最高管理员")
		} else {
			writeError(w, http.StatusBadRequest, "用户参数不正确")
		}
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"user": userView(user)})
}

func (s *Server) deleteUser(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.requireOwner(w, r); !ok {
		return
	}
	s.quotaMu.Lock()
	err := s.authStore.DeleteUser(r.PathValue("id"))
	s.quotaMu.Unlock()
	if err != nil {
		if errors.Is(err, auth.ErrDenied) {
			writeError(w, http.StatusForbidden, "不能删除最高管理员")
		} else {
			writeError(w, http.StatusNotFound, "用户不存在")
		}
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) assignUserAccounts(w http.ResponseWriter, r *http.Request) {
	if _, ok := s.requireOwner(w, r); !ok {
		return
	}
	var body struct {
		AccountIDs []string `json:"account_ids"`
	}
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 64<<10))
	if err != nil || json.Unmarshal(raw, &body) != nil || body.AccountIDs == nil {
		writeError(w, http.StatusBadRequest, "账号分配参数不正确")
		return
	}
	s.quotaMu.Lock()
	user, err := s.authStore.ReplaceAccounts(r.PathValue("id"), body.AccountIDs)
	s.quotaMu.Unlock()
	if err != nil {
		writeError(w, http.StatusBadRequest, "账号分配失败")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"user": userView(user)})
}

func remoteIP(r *http.Request) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err == nil {
		return host
	}
	return r.RemoteAddr
}

func secureRequest(r *http.Request) bool {
	return r.TLS != nil || strings.EqualFold(r.Header.Get("X-Forwarded-Proto"), "https")
}

func regularRouteAllowed(r *http.Request) bool {
	path := r.URL.Path
	switch {
	case path == "/api/backend" && r.Method == http.MethodGet:
		return true
	case path == "/api/backend/health":
		return true
	case path == "/api/backend/accounts":
		return r.Method == http.MethodGet
	case strings.HasPrefix(path, "/api/backend/accounts/") &&
		(strings.HasSuffix(path, "/courses") || strings.Contains(path, "/courses/")):
		return true
	case path == "/api/backend/jobs" || strings.HasPrefix(path, "/api/backend/jobs/"):
		return true
	case path == "/api/health", path == "/api/session", path == "/api/session/password":
		return true
	}
	return false
}

func (s *Server) withUser(r *http.Request, user auth.User) *http.Request {
	return r.WithContext(context.WithValue(r.Context(), userKey, user))
}
