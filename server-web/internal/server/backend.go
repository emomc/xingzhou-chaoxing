package server

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/url"
	"regexp"
	"slices"

	"xingguang/internal/backend"
)

var backendID = regexp.MustCompile(`^[a-f0-9]{32}$`)
var backendCourseID = regexp.MustCompile(`^[0-9]{1,20}$`)
var backendImageID = regexp.MustCompile(`^image_([1-9]|10)$`)

func (s *Server) accountAccess(r *http.Request) (map[string]bool, bool) {
	if !s.authRequired {
		return nil, true
	}
	user, ok := userFrom(r)
	if !ok {
		return nil, false
	}
	if user.IsOwner() {
		return nil, true
	}
	allowed := make(map[string]bool, len(user.AccountIDs))
	for _, id := range user.AccountIDs {
		allowed[id] = true
	}
	return allowed, true
}

func (s *Server) canAccessAccount(r *http.Request, accountID string) bool {
	allowed, ok := s.accountAccess(r)
	return ok && (allowed == nil || allowed[accountID])
}

func (s *Server) canAccessJob(r *http.Request, jobID string) bool {
	if !s.authRequired {
		return true
	}
	if user, ok := userFrom(r); ok && user.IsOwner() {
		return true
	}
	code, raw, err := s.backend.Request(r.Context(), http.MethodGet, "/v1/jobs/"+jobID, nil, "")
	if err != nil || code != http.StatusOK {
		return false
	}
	var job map[string]any
	if json.Unmarshal(raw, &job) != nil {
		return false
	}
	accountID, _ := job["account_id"].(string)
	return s.canAccessAccount(r, accountID)
}

func (s *Server) jobAccountMap(r *http.Request) (map[string]string, error) {
	result := map[string]string{}
	for _, path := range []string{"/v1/jobs?limit=1000", "/v1/jobs?archived=1&limit=1000"} {
		code, raw, err := s.backend.Request(r.Context(), http.MethodGet, path, nil, "")
		if err != nil {
			return nil, err
		}
		if code != http.StatusOK {
			return nil, errors.New("backend job list failed")
		}
		var payload struct {
			Jobs []struct {
				ID        string `json:"id"`
				AccountID string `json:"account_id"`
			} `json:"jobs"`
		}
		if json.Unmarshal(raw, &payload) != nil {
			return nil, errors.New("invalid backend job list")
		}
		for _, job := range payload.Jobs {
			result[job.ID] = job.AccountID
		}
	}
	return result, nil
}

func (s *Server) canAccessBatch(r *http.Request) bool {
	if !s.authRequired {
		return true
	}
	if user, ok := userFrom(r); ok && user.IsOwner() {
		return true
	}
	raw, err := io.ReadAll(io.LimitReader(r.Body, (64<<10)+1))
	if err != nil {
		return false
	}
	if len(raw) > 64<<10 {
		return false
	}
	r.Body = io.NopCloser(bytes.NewReader(raw))
	var body struct {
		JobIDs []string `json:"job_ids"`
	}
	if json.Unmarshal(raw, &body) != nil || len(body.JobIDs) == 0 {
		return false
	}
	jobs, err := s.jobAccountMap(r)
	if err != nil {
		return false
	}
	for _, id := range body.JobIDs {
		accountID, ok := jobs[id]
		if !ok || !s.canAccessAccount(r, accountID) {
			return false
		}
	}
	return true
}

func writeBackendRaw(w http.ResponseWriter, code int, raw []byte) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	_, _ = w.Write(raw)
}

func (s *Server) scopedAccounts(w http.ResponseWriter, r *http.Request) {
	allowed, ok := s.accountAccess(r)
	if !ok {
		writeError(w, http.StatusUnauthorized, "请先登录")
		return
	}
	code, raw, err := s.backend.Request(r.Context(), http.MethodGet, "/v1/accounts", nil, "")
	if err != nil {
		writeError(w, http.StatusBadGateway, err.Error())
		return
	}
	if code != http.StatusOK || allowed == nil {
		writeBackendRaw(w, code, raw)
		return
	}
	var payload struct {
		Accounts []map[string]any `json:"accounts"`
	}
	if json.Unmarshal(raw, &payload) != nil {
		writeError(w, http.StatusBadGateway, "后端返回了无效响应")
		return
	}
	filtered := make([]map[string]any, 0, len(payload.Accounts))
	for _, account := range payload.Accounts {
		if id, _ := account["id"].(string); allowed[id] {
			filtered = append(filtered, account)
		}
	}
	raw, _ = json.Marshal(map[string]any{"accounts": filtered})
	writeBackendRaw(w, http.StatusOK, raw)
}

func (s *Server) scopedJobs(w http.ResponseWriter, r *http.Request) {
	allowed, ok := s.accountAccess(r)
	if !ok {
		writeError(w, http.StatusUnauthorized, "请先登录")
		return
	}
	query, err := url.ParseQuery(r.URL.RawQuery)
	if err != nil || len(r.URL.RawQuery) > 4096 {
		writeError(w, http.StatusBadRequest, "任务查询参数不正确")
		return
	}
	for key, values := range query {
		if !slices.Contains([]string{"archived", "limit"}, key) || len(values) != 1 {
			writeError(w, http.StatusBadRequest, "任务查询参数不正确")
			return
		}
	}
	if value := query.Get("archived"); value != "" && value != "0" && value != "1" {
		writeError(w, http.StatusBadRequest, "任务查询参数不正确")
		return
	}
	path := "/v1/jobs"
	if len(query) > 0 {
		path += "?" + query.Encode()
	}
	code, raw, err := s.backend.Request(r.Context(), http.MethodGet, path, nil, "")
	if err != nil {
		writeError(w, http.StatusBadGateway, err.Error())
		return
	}
	if code != http.StatusOK || allowed == nil {
		writeBackendRaw(w, code, raw)
		return
	}
	var payload struct {
		Jobs []map[string]any `json:"jobs"`
	}
	if json.Unmarshal(raw, &payload) != nil {
		writeError(w, http.StatusBadGateway, "后端返回了无效响应")
		return
	}
	filtered := make([]map[string]any, 0, len(payload.Jobs))
	for _, job := range payload.Jobs {
		if id, _ := job["account_id"].(string); allowed[id] {
			filtered = append(filtered, job)
		}
	}
	raw, _ = json.Marshal(map[string]any{"jobs": filtered})
	writeBackendRaw(w, http.StatusOK, raw)
}

func (s *Server) scopedHealth(w http.ResponseWriter, r *http.Request) {
	if !s.authRequired {
		s.proxyBackend(w, r, "/v1/health")
		return
	}
	user, ok := userFrom(r)
	if !ok {
		writeError(w, http.StatusUnauthorized, "请先登录")
		return
	}
	code, raw, err := s.backend.Request(r.Context(), http.MethodGet, "/v1/health", nil, "")
	if err != nil {
		writeError(w, http.StatusBadGateway, err.Error())
		return
	}
	if code != http.StatusOK || user.IsOwner() {
		writeBackendRaw(w, code, raw)
		return
	}
	var health map[string]any
	if json.Unmarshal(raw, &health) != nil {
		writeError(w, http.StatusBadGateway, "后端返回了无效响应")
		return
	}
	allowed, _ := s.accountAccess(r)
	counts := map[string]int{}
	code, jobsRaw, err := s.backend.Request(r.Context(), http.MethodGet, "/v1/jobs?limit=1000", nil, "")
	if err == nil && code == http.StatusOK {
		var payload struct {
			Jobs []map[string]any `json:"jobs"`
		}
		if json.Unmarshal(jobsRaw, &payload) == nil {
			for _, job := range payload.Jobs {
				if accountID, _ := job["account_id"].(string); allowed[accountID] {
					status, _ := job["status"].(string)
					counts[status]++
				}
			}
		}
	}
	health["jobs"] = counts
	health["visible_jobs"] = counts
	health["archived_jobs"] = 0
	health["answers"] = map[string]int{"running": 0, "queued": 0}
	health["question_bank"] = map[string]int{"entries": 0, "hits": 0, "shared_waits": 0}
	delete(health, "storage")
	raw, _ = json.Marshal(health)
	writeBackendRaw(w, http.StatusOK, raw)
}

func (s *Server) createScopedJob(w http.ResponseWriter, r *http.Request) {
	if !s.authRequired {
		s.proxyBackend(w, r, "/v1/jobs")
		return
	}
	user, ok := userFrom(r)
	if !ok {
		writeError(w, http.StatusUnauthorized, "请先登录")
		return
	}
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 64<<10))
	if err != nil {
		writeError(w, http.StatusRequestEntityTooLarge, "请求内容过大")
		return
	}
	if user.IsOwner() {
		code, data, err := s.backend.Request(r.Context(), http.MethodPost, "/v1/jobs", raw,
			r.Header.Get("Idempotency-Key"))
		if err != nil {
			writeError(w, http.StatusBadGateway, err.Error())
			return
		}
		writeBackendRaw(w, code, data)
		return
	}
	var body map[string]any
	if json.Unmarshal(raw, &body) != nil {
		writeError(w, http.StatusBadRequest, "任务参数不正确")
		return
	}
	accountID, _ := body["account_id"].(string)
	if !s.canAccessAccount(r, accountID) {
		writeError(w, http.StatusForbidden, "无权使用该账号")
		return
	}
	courses, ok := body["course_ids"].([]any)
	if !ok || len(courses) == 0 || len(courses) > 50 {
		writeError(w, http.StatusBadRequest, "课程数量不正确")
		return
	}
	confirmed, _ := body["quota_confirmed"].(bool)
	if !confirmed {
		writeError(w, http.StatusBadRequest, "请先确认课程额度消耗")
		return
	}
	s.quotaMu.Lock()
	defer s.quotaMu.Unlock()
	if _, err := s.authStore.CheckCharge(user.ID, len(courses)); err != nil {
		writeError(w, http.StatusConflict, "课程额度不足或月度套餐已失效")
		return
	}
	delete(body, "quota_confirmed")
	forward, _ := json.Marshal(body)
	code, data, err := s.backend.Request(r.Context(), http.MethodPost, "/v1/jobs", forward,
		r.Header.Get("Idempotency-Key"))
	if err != nil {
		writeError(w, http.StatusBadGateway, err.Error())
		return
	}
	if code == http.StatusCreated || code == http.StatusOK {
		var job struct {
			ID string `json:"id"`
		}
		if json.Unmarshal(data, &job) != nil || len(job.ID) != 32 {
			writeError(w, http.StatusBadGateway, "后端任务响应异常")
			return
		}
		if _, err := s.authStore.ChargeJob(user.ID, job.ID, len(courses)); err != nil {
			_, _, _ = s.backend.Request(r.Context(), http.MethodPost, "/v1/jobs/"+job.ID+"/cancel",
				[]byte("{}"), "")
			writeError(w, http.StatusConflict, "课程额度扣除失败，任务已取消")
			return
		}
	}
	writeBackendRaw(w, code, data)
}

func (s *Server) ConfigureBackend(base, token string) error {
	return s.backend.Configure(base, token)
}

func (s *Server) backendRoutes() {
	s.mux.HandleFunc("GET /api/backend", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusOK, s.backend.Summary())
	})
	s.mux.HandleFunc("PUT /api/backend", func(w http.ResponseWriter, r *http.Request) {
		var cfg backend.Config
		raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 4096))
		defer r.Body.Close()
		if err != nil || json.Unmarshal(raw, &cfg) != nil {
			writeError(w, http.StatusBadRequest, "连接配置不正确")
			return
		}
		if err := s.backend.Configure(cfg.URL, cfg.Token); err != nil {
			writeError(w, http.StatusBadRequest, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, s.backend.Summary())
	})
	s.mux.HandleFunc("DELETE /api/backend", func(w http.ResponseWriter, _ *http.Request) {
		if err := s.backend.Disconnect(); err != nil {
			writeError(w, http.StatusInternalServerError, err.Error())
			return
		}
		writeJSON(w, http.StatusOK, s.backend.Summary())
	})
	s.mux.HandleFunc("GET /api/backend/health", s.scopedHealth)
	s.mux.HandleFunc("GET /api/backend/accounts", s.scopedAccounts)
	s.mux.HandleFunc("GET /api/backend/jobs", s.scopedJobs)
	s.mux.HandleFunc("POST /api/backend/jobs", s.createScopedJob)
	for _, resource := range []string{"resources", "question-bank/stats", "pacing", "video/guard"} {
		path := "/v1/" + resource
		s.mux.HandleFunc("GET /api/backend/"+resource, func(w http.ResponseWriter, r *http.Request) {
			s.proxyBackend(w, r, path)
		})
	}
	s.mux.HandleFunc("PUT /api/backend/video/guard", func(w http.ResponseWriter, r *http.Request) {
		s.proxyBackend(w, r, "/v1/video/guard")
	})
	s.mux.HandleFunc("GET /api/backend/ai/channel", func(w http.ResponseWriter, r *http.Request) {
		s.proxyBackend(w, r, "/v1/ai/channel")
	})
	s.mux.HandleFunc("PUT /api/backend/ai/channel", func(w http.ResponseWriter, r *http.Request) {
		s.proxyBackend(w, r, "/v1/ai/channel")
	})
	s.mux.HandleFunc("GET /api/backend/ai/provider", func(w http.ResponseWriter, r *http.Request) {
		s.proxyBackendSlow(w, r, "/v1/ai/provider")
	})
	s.mux.HandleFunc("PUT /api/backend/ai/provider", func(w http.ResponseWriter, r *http.Request) {
		s.proxyBackendSlow(w, r, "/v1/ai/provider")
	})
	s.mux.HandleFunc("GET /api/backend/ai/models", func(w http.ResponseWriter, r *http.Request) {
		query, err := url.ParseQuery(r.URL.RawQuery)
		if err != nil || len(r.URL.RawQuery) > 64 {
			writeError(w, http.StatusBadRequest, "模型查询参数不正确")
			return
		}
		for key, values := range query {
			if key != "route" || len(values) != 1 ||
				(values[0] != "text" && values[0] != "vision") {
				writeError(w, http.StatusBadRequest, "模型查询参数不正确")
				return
			}
		}
		path := "/v1/ai/models"
		if len(query) > 0 {
			path += "?" + query.Encode()
		}
		s.proxyBackendSlow(w, r, path)
	})
	s.mux.HandleFunc("GET /api/backend/ai/vision-provider", func(w http.ResponseWriter, r *http.Request) {
		s.proxyBackendSlow(w, r, "/v1/ai/vision-provider")
	})
	s.mux.HandleFunc("PUT /api/backend/ai/vision-provider", func(w http.ResponseWriter, r *http.Request) {
		s.proxyBackendSlow(w, r, "/v1/ai/vision-provider")
	})
	s.mux.HandleFunc("POST /api/backend/ai/vision-bench", func(w http.ResponseWriter, r *http.Request) {
		s.proxyBackendSlow(w, r, "/v1/ai/vision-bench")
	})
	s.mux.HandleFunc("PUT /api/backend/ai/vision-fallback", func(w http.ResponseWriter, r *http.Request) {
		s.proxyBackend(w, r, "/v1/ai/vision-fallback")
	})
	s.mux.HandleFunc("PUT /api/backend/ai/vision-direct", func(w http.ResponseWriter, r *http.Request) {
		s.proxyBackend(w, r, "/v1/ai/vision-direct")
	})
	s.mux.HandleFunc("POST /api/backend/ai/test", func(w http.ResponseWriter, r *http.Request) {
		s.proxyBackendSlow(w, r, "/v1/ai/test")
	})
	s.mux.HandleFunc("GET /api/backend/ai/conversations", func(w http.ResponseWriter, r *http.Request) {
		query, err := url.ParseQuery(r.URL.RawQuery)
		if err != nil || len(r.URL.RawQuery) > 1024 {
			writeError(w, http.StatusBadRequest, "对话查询参数不正确")
			return
		}
		for key, values := range query {
			if !slices.Contains([]string{"after", "limit", "job_id"}, key) || len(values) != 1 {
				writeError(w, http.StatusBadRequest, "对话查询参数不正确")
				return
			}
		}
		path := "/v1/ai/conversations"
		if len(query) > 0 {
			path += "?" + query.Encode()
		}
		s.proxyBackendSlow(w, r, path)
	})
	s.mux.HandleFunc("GET /api/backend/question-bank", func(w http.ResponseWriter, r *http.Request) {
		query, err := url.ParseQuery(r.URL.RawQuery)
		if err != nil || len(r.URL.RawQuery) > 4096 {
			writeError(w, http.StatusBadRequest, "题库查询参数不正确")
			return
		}
		for key, values := range query {
			if !slices.Contains([]string{"search", "course_id", "type", "sort", "page", "page_size"}, key) ||
				len(values) != 1 {
				writeError(w, http.StatusBadRequest, "题库查询参数不正确")
				return
			}
		}
		path := "/v1/question-bank"
		if len(query) > 0 {
			path += "?" + query.Encode()
		}
		s.proxyBackend(w, r, path)
	})
	s.mux.HandleFunc("GET /api/backend/question-bank/{id}", func(w http.ResponseWriter, r *http.Request) {
		if !backendID.MatchString(r.PathValue("id")) {
			writeError(w, http.StatusBadRequest, "题目 ID 不正确")
			return
		}
		s.proxyBackend(w, r, "/v1/question-bank/"+r.PathValue("id"))
	})
	s.mux.HandleFunc("GET /api/backend/question-bank/{id}/images/{image_id}", func(w http.ResponseWriter, r *http.Request) {
		if !backendID.MatchString(r.PathValue("id")) || !backendImageID.MatchString(r.PathValue("image_id")) {
			writeError(w, http.StatusBadRequest, "题目图片 ID 不正确")
			return
		}
		s.proxyBackend(w, r, "/v1/question-bank/"+r.PathValue("id")+"/images/"+r.PathValue("image_id"))
	})
	s.mux.HandleFunc("GET /api/backend/image-questions", func(w http.ResponseWriter, r *http.Request) {
		query, err := url.ParseQuery(r.URL.RawQuery)
		if err != nil || len(r.URL.RawQuery) > 4096 {
			writeError(w, http.StatusBadRequest, "图片题查询参数不正确")
			return
		}
		for key, values := range query {
			if !slices.Contains([]string{"search", "course_id", "status", "page", "page_size"}, key) || len(values) != 1 {
				writeError(w, http.StatusBadRequest, "图片题查询参数不正确")
				return
			}
		}
		path := "/v1/image-questions"
		if len(query) > 0 {
			path += "?" + query.Encode()
		}
		s.proxyBackend(w, r, path)
	})
	s.mux.HandleFunc("GET /api/backend/image-questions/{id}", func(w http.ResponseWriter, r *http.Request) {
		if !backendID.MatchString(r.PathValue("id")) {
			writeError(w, http.StatusBadRequest, "图片题 ID 不正确")
			return
		}
		s.proxyBackend(w, r, "/v1/image-questions/"+r.PathValue("id"))
	})
	s.mux.HandleFunc("GET /api/backend/image-questions/{id}/images/{image_id}", func(w http.ResponseWriter, r *http.Request) {
		if !backendID.MatchString(r.PathValue("id")) || !backendImageID.MatchString(r.PathValue("image_id")) {
			writeError(w, http.StatusBadRequest, "题目图片 ID 不正确")
			return
		}
		s.proxyBackend(w, r, "/v1/image-questions/"+r.PathValue("id")+"/images/"+r.PathValue("image_id"))
	})
	s.mux.HandleFunc("POST /api/backend/accounts", func(w http.ResponseWriter, r *http.Request) {
		s.proxyBackend(w, r, "/v1/accounts")
	})
	s.mux.HandleFunc("PUT /api/backend/accounts/{id}", func(w http.ResponseWriter, r *http.Request) {
		if !backendID.MatchString(r.PathValue("id")) {
			writeError(w, http.StatusBadRequest, "账号 ID 不正确")
			return
		}
		s.proxyBackend(w, r, "/v1/accounts/"+r.PathValue("id"))
	})
	s.mux.HandleFunc("DELETE /api/backend/accounts/{id}", func(w http.ResponseWriter, r *http.Request) {
		if !backendID.MatchString(r.PathValue("id")) {
			writeError(w, http.StatusBadRequest, "账号 ID 不正确")
			return
		}
		s.proxyBackend(w, r, "/v1/accounts/"+r.PathValue("id"))
	})
	s.mux.HandleFunc("DELETE /api/backend/jobs/{id}", func(w http.ResponseWriter, r *http.Request) {
		if !backendID.MatchString(r.PathValue("id")) {
			writeError(w, http.StatusBadRequest, "任务 ID 不正确")
			return
		}
		if !s.canAccessJob(r, r.PathValue("id")) {
			writeError(w, http.StatusForbidden, "无权访问该任务")
			return
		}
		s.proxyBackend(w, r, "/v1/jobs/"+r.PathValue("id"))
	})
	for _, method := range []string{"GET", "POST"} {
		s.mux.HandleFunc(method+" /api/backend/accounts/{id}/courses", func(w http.ResponseWriter, r *http.Request) {
			if !backendID.MatchString(r.PathValue("id")) {
				writeError(w, http.StatusBadRequest, "账号 ID 不正确")
				return
			}
			if !s.canAccessAccount(r, r.PathValue("id")) {
				writeError(w, http.StatusForbidden, "无权访问该账号")
				return
			}
			s.proxyBackend(w, r, "/v1/accounts/"+r.PathValue("id")+"/courses")
		})
		s.mux.HandleFunc(method+" /api/backend/accounts/{id}/courses/{course_id}", func(w http.ResponseWriter, r *http.Request) {
			if !backendID.MatchString(r.PathValue("id")) ||
				!backendCourseID.MatchString(r.PathValue("course_id")) {
				writeError(w, http.StatusBadRequest, "课程 ID 不正确")
				return
			}
			if !s.canAccessAccount(r, r.PathValue("id")) {
				writeError(w, http.StatusForbidden, "无权访问该账号")
				return
			}
			path := "/v1/accounts/" + r.PathValue("id") + "/courses/" + r.PathValue("course_id")
			s.proxyBackend(w, r, path)
		})
	}
	s.mux.HandleFunc("GET /api/backend/jobs/{id}", func(w http.ResponseWriter, r *http.Request) {
		if r.PathValue("id") == "batch" {
			http.NotFound(w, r)
			return
		}
		if !backendID.MatchString(r.PathValue("id")) {
			writeError(w, http.StatusBadRequest, "任务 ID 不正确")
			return
		}
		if !s.canAccessJob(r, r.PathValue("id")) {
			writeError(w, http.StatusForbidden, "无权访问该任务")
			return
		}
		s.proxyBackend(w, r, "/v1/jobs/"+r.PathValue("id"))
	})
	s.mux.HandleFunc("GET /api/backend/jobs/{id}/events", func(w http.ResponseWriter, r *http.Request) {
		if !backendID.MatchString(r.PathValue("id")) {
			writeError(w, http.StatusBadRequest, "任务 ID 不正确")
			return
		}
		if !s.canAccessJob(r, r.PathValue("id")) {
			writeError(w, http.StatusForbidden, "无权访问该任务")
			return
		}
		path := "/v1/jobs/" + r.PathValue("id") + "/events"
		if after := r.URL.Query().Get("after"); after != "" {
			path += "?after=" + url.QueryEscape(after)
		}
		s.proxyBackend(w, r, path)
	})
	s.mux.HandleFunc("POST /api/backend/jobs/{id}/{action}", func(w http.ResponseWriter, r *http.Request) {
		action := r.PathValue("action")
		if !backendID.MatchString(r.PathValue("id")) ||
			(action != "pause" && action != "resume" && action != "cancel" && action != "retry" &&
				action != "archive" && action != "unarchive") {
			writeError(w, http.StatusBadRequest, "任务操作不正确")
			return
		}
		if !s.canAccessJob(r, r.PathValue("id")) {
			writeError(w, http.StatusForbidden, "无权访问该任务")
			return
		}
		s.proxyBackend(w, r, "/v1/jobs/"+r.PathValue("id")+"/"+action)
	})
	s.mux.HandleFunc("POST /api/backend/jobs/batch", func(w http.ResponseWriter, r *http.Request) {
		if !s.canAccessBatch(r) {
			writeError(w, http.StatusForbidden, "批量任务中包含无权访问的任务")
			return
		}
		s.proxyBackend(w, r, "/v1/jobs/batch")
	})
}

func (s *Server) proxyBackend(w http.ResponseWriter, r *http.Request, path string) {
	s.proxyBackendWith(w, r, path, false)
}

// proxyBackendSlow keeps the longer provider deadline for AI configuration work.
func (s *Server) proxyBackendSlow(w http.ResponseWriter, r *http.Request, path string) {
	s.proxyBackendWith(w, r, path, true)
}

func (s *Server) proxyBackendWith(w http.ResponseWriter, r *http.Request, path string, slow bool) {
	var raw []byte
	var err error
	if r.Body != nil {
		defer r.Body.Close()
		raw, err = io.ReadAll(http.MaxBytesReader(w, r.Body, 65536))
		if err != nil {
			writeError(w, http.StatusRequestEntityTooLarge, "请求内容过大")
			return
		}
	}
	var code int
	var data []byte
	if slow {
		code, data, err = s.backend.RequestSlow(r.Context(), r.Method, path, raw)
	} else {
		code, data, err = s.backend.Request(r.Context(), r.Method, path, raw, r.Header.Get("Idempotency-Key"))
	}
	if err != nil {
		writeError(w, http.StatusBadGateway, err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(code)
	_, _ = w.Write(data)
}
