// Package auth stores console users, sessions, account ownership, and course usage.
package auth

import (
	"crypto/pbkdf2"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"database/sql"
	"encoding/base64"
	"encoding/hex"
	"errors"
	"fmt"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	_ "modernc.org/sqlite"
)

const (
	RoleOwner = "owner"
	RoleUser  = "user"

	StatusActive   = "active"
	StatusDisabled = "disabled"

	sessionLifetime = 7 * 24 * time.Hour
	hashIterations  = 210_000
)

var (
	ErrNotFound = errors.New("user not found")
	ErrConflict = errors.New("user already exists")
	ErrInvalid  = errors.New("invalid user data")
	ErrDenied   = errors.New("access denied")
)

type User struct {
	ID              string   `json:"id"`
	Username        string   `json:"username"`
	Role            string   `json:"role"`
	Status          string   `json:"status"`
	Credits         int      `json:"credits"`
	PlanExpiresAt   *float64 `json:"plan_expires_at"`
	PlanCourseLimit *int     `json:"plan_course_limit"`
	AccountIDs      []string `json:"account_ids"`
	CreatedAt       float64  `json:"created_at"`
	UpdatedAt       float64  `json:"updated_at"`
	passwordHash    string
}

func (u User) IsOwner() bool { return u.Role == RoleOwner }

func (u User) PlanActive(now time.Time) bool {
	return u.PlanExpiresAt != nil && *u.PlanExpiresAt > float64(now.Unix()) &&
		u.PlanCourseLimit != nil && *u.PlanCourseLimit > 0
}

type Store struct {
	db *sql.DB
}

func Open(dir, ownerUsername, ownerPassword string) (*Store, error) {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, err
	}
	filename, err := filepath.Abs(filepath.Join(dir, "users.sqlite3"))
	if err != nil {
		return nil, err
	}
	if info, err := os.Lstat(filename); err == nil && info.Mode()&os.ModeSymlink != 0 {
		return nil, errors.New("the user database must not be a symbolic link")
	} else if err != nil && !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	path := filepath.ToSlash(filename)
	if !strings.HasPrefix(path, "/") {
		path = "/" + path
	}
	location := &url.URL{Scheme: "file", Path: path}
	query := url.Values{}
	query.Add("_pragma", "busy_timeout(10000)")
	query.Add("_pragma", "foreign_keys(ON)")
	query.Add("_pragma", "synchronous(FULL)")
	query.Set("_txlock", "immediate")
	location.RawQuery = query.Encode()
	db, err := sql.Open("sqlite", location.String())
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	fail := func(err error) (*Store, error) {
		_ = db.Close()
		return nil, err
	}
	if err := db.Ping(); err != nil {
		return fail(err)
	}
	if err := os.Chmod(filename, 0o600); err != nil {
		return fail(err)
	}
	if _, err := db.Exec("PRAGMA journal_mode=WAL"); err != nil {
		return fail(err)
	}
	if _, err := db.Exec(`
		CREATE TABLE IF NOT EXISTS users (
			id TEXT PRIMARY KEY,
			username TEXT NOT NULL UNIQUE COLLATE NOCASE,
			password_hash TEXT NOT NULL,
			role TEXT NOT NULL CHECK(role IN ('owner','user')),
			status TEXT NOT NULL CHECK(status IN ('active','disabled')),
			credits INTEGER NOT NULL DEFAULT 0 CHECK(credits >= 0),
			plan_expires_at REAL,
			plan_course_limit INTEGER CHECK(plan_course_limit IS NULL OR plan_course_limit > 0),
			created_at REAL NOT NULL,
			updated_at REAL NOT NULL
		);
		CREATE TABLE IF NOT EXISTS sessions (
			token_hash TEXT PRIMARY KEY,
			user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
			expires_at REAL NOT NULL,
			created_at REAL NOT NULL
		);
		CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
		CREATE TABLE IF NOT EXISTS user_accounts (
			account_id TEXT PRIMARY KEY,
			user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
			created_at REAL NOT NULL
		);
		CREATE INDEX IF NOT EXISTS user_accounts_user ON user_accounts(user_id);
		CREATE TABLE IF NOT EXISTS job_usage (
			job_id TEXT PRIMARY KEY,
			user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
			courses INTEGER NOT NULL CHECK(courses > 0),
			source TEXT NOT NULL CHECK(source IN ('credits','plan')),
			created_at REAL NOT NULL
		);
		CREATE INDEX IF NOT EXISTS job_usage_user ON job_usage(user_id, created_at DESC);
	`); err != nil {
		return fail(err)
	}
	store := &Store{db: db}
	count := 0
	if err := db.QueryRow("SELECT count(*) FROM users").Scan(&count); err != nil {
		return fail(err)
	}
	if count == 0 {
		if ownerUsername == "" || ownerPassword == "" {
			return fail(errors.New("an initial owner username and password are required"))
		}
		if _, err := store.CreateUser(ownerUsername, ownerPassword, RoleOwner, 0, nil, nil); err != nil {
			return fail(err)
		}
	}
	return store, nil
}

func (s *Store) Close() error { return s.db.Close() }

func normalizeUsername(username string) (string, error) {
	username = strings.TrimSpace(username)
	if username == "" || len([]rune(username)) > 64 || strings.ContainsAny(username, " \t\r\n") {
		return "", ErrInvalid
	}
	for _, char := range username {
		if char < 0x20 || char == 0x7f {
			return "", ErrInvalid
		}
	}
	return username, nil
}

func validPassword(password string) bool {
	return len(password) >= 8 && len(password) <= 200 && !strings.ContainsAny(password, "\r\n")
}

func hashPassword(password string) (string, error) {
	if !validPassword(password) {
		return "", ErrInvalid
	}
	salt := make([]byte, 16)
	if _, err := rand.Read(salt); err != nil {
		return "", err
	}
	key, err := pbkdf2.Key(sha256.New, password, salt, hashIterations, 32)
	if err != nil {
		return "", err
	}
	return fmt.Sprintf("pbkdf2-sha256$%d$%s$%s", hashIterations,
		base64.RawStdEncoding.EncodeToString(salt),
		base64.RawStdEncoding.EncodeToString(key)), nil
}

func verifyPassword(encoded, password string) bool {
	parts := strings.Split(encoded, "$")
	if len(parts) != 4 || parts[0] != "pbkdf2-sha256" {
		return false
	}
	var iterations int
	if _, err := fmt.Sscanf(parts[1], "%d", &iterations); err != nil ||
		iterations < 50_000 || iterations > 2_000_000 {
		return false
	}
	salt, err := base64.RawStdEncoding.DecodeString(parts[2])
	if err != nil {
		return false
	}
	expected, err := base64.RawStdEncoding.DecodeString(parts[3])
	if err != nil {
		return false
	}
	actual, err := pbkdf2.Key(sha256.New, password, salt, iterations, len(expected))
	return err == nil && subtle.ConstantTimeCompare(actual, expected) == 1
}

func newID() (string, error) {
	raw := make([]byte, 16)
	if _, err := rand.Read(raw); err != nil {
		return "", err
	}
	return hex.EncodeToString(raw), nil
}

func (s *Store) CreateUser(username, password, role string, credits int,
	planExpiresAt *float64, planCourseLimit *int) (User, error) {
	username, err := normalizeUsername(username)
	if err != nil || !validPassword(password) ||
		(role != RoleOwner && role != RoleUser) || credits < 0 ||
		(planCourseLimit != nil && *planCourseLimit < 1) {
		return User{}, ErrInvalid
	}
	id, err := newID()
	if err != nil {
		return User{}, err
	}
	hash, err := hashPassword(password)
	if err != nil {
		return User{}, err
	}
	now := float64(time.Now().Unix())
	_, err = s.db.Exec(
		"INSERT INTO users(id,username,password_hash,role,status,credits,"+
			"plan_expires_at,plan_course_limit,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
		id, username, hash, role, StatusActive, credits, planExpiresAt, planCourseLimit, now, now,
	)
	if err != nil {
		if strings.Contains(strings.ToLower(err.Error()), "unique") {
			return User{}, ErrConflict
		}
		return User{}, err
	}
	return s.User(id)
}

func (s *Store) userRow(where string, args ...any) (User, error) {
	var user User
	var planExpires sql.NullFloat64
	var limit sql.NullInt64
	err := s.db.QueryRow(
		"SELECT id,username,password_hash,role,status,credits,plan_expires_at,"+
			"plan_course_limit,created_at,updated_at FROM users WHERE "+where, args...,
	).Scan(&user.ID, &user.Username, &user.passwordHash, &user.Role, &user.Status,
		&user.Credits, &planExpires, &limit, &user.CreatedAt, &user.UpdatedAt)
	if errors.Is(err, sql.ErrNoRows) {
		return User{}, ErrNotFound
	}
	if err != nil {
		return User{}, err
	}
	if planExpires.Valid {
		value := planExpires.Float64
		user.PlanExpiresAt = &value
	}
	if limit.Valid {
		value := int(limit.Int64)
		user.PlanCourseLimit = &value
	}
	rows, err := s.db.Query("SELECT account_id FROM user_accounts WHERE user_id=? ORDER BY account_id", user.ID)
	if err != nil {
		return User{}, err
	}
	defer rows.Close()
	user.AccountIDs = []string{}
	for rows.Next() {
		var accountID string
		if err := rows.Scan(&accountID); err != nil {
			return User{}, err
		}
		user.AccountIDs = append(user.AccountIDs, accountID)
	}
	return user, rows.Err()
}

func (s *Store) User(id string) (User, error) {
	return s.userRow("id=?", id)
}

func (s *Store) UserByName(username string) (User, error) {
	return s.userRow("username=?", strings.TrimSpace(username))
}

func (s *Store) ListUsers() ([]User, error) {
	rows, err := s.db.Query("SELECT id FROM users ORDER BY role='owner' DESC, created_at")
	if err != nil {
		return nil, err
	}
	var ids []string
	for rows.Next() {
		var id string
		if err := rows.Scan(&id); err != nil {
			rows.Close()
			return nil, err
		}
		ids = append(ids, id)
	}
	rows.Close()
	users := make([]User, 0, len(ids))
	for _, id := range ids {
		user, err := s.User(id)
		if err != nil {
			return nil, err
		}
		users = append(users, user)
	}
	return users, nil
}

func (s *Store) UpdateUser(id, password, status string, credits int,
	planExpiresAt *float64, planCourseLimit *int) (User, error) {
	user, err := s.User(id)
	if err != nil {
		return User{}, err
	}
	if user.IsOwner() && status == StatusDisabled {
		return User{}, ErrDenied
	}
	if status != StatusActive && status != StatusDisabled || credits < 0 ||
		(planCourseLimit != nil && *planCourseLimit < 1) {
		return User{}, ErrInvalid
	}
	hash := user.passwordHash
	if password != "" {
		hash, err = hashPassword(password)
		if err != nil {
			return User{}, err
		}
	}
	_, err = s.db.Exec(
		"UPDATE users SET password_hash=?,status=?,credits=?,plan_expires_at=?,"+
			"plan_course_limit=?,updated_at=? WHERE id=?",
		hash, status, credits, planExpiresAt, planCourseLimit, float64(time.Now().Unix()), id,
	)
	if err != nil {
		return User{}, err
	}
	if status == StatusDisabled {
		_, _ = s.db.Exec("DELETE FROM sessions WHERE user_id=?", id)
	}
	return s.User(id)
}

func (s *Store) DeleteUser(id string) error {
	user, err := s.User(id)
	if err != nil {
		return err
	}
	if user.IsOwner() {
		return ErrDenied
	}
	_, err = s.db.Exec("DELETE FROM users WHERE id=?", id)
	return err
}

func (s *Store) Authenticate(username, password string) (User, error) {
	user, err := s.UserByName(username)
	if err != nil {
		verifyPassword("pbkdf2-sha256$210000$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", password)
		return User{}, ErrDenied
	}
	if user.Status != StatusActive || !verifyPassword(user.passwordHash, password) {
		return User{}, ErrDenied
	}
	return user, nil
}

func tokenHash(token string) string {
	sum := sha256.Sum256([]byte(token))
	return hex.EncodeToString(sum[:])
}

func (s *Store) CreateSession(userID string) (string, time.Time, error) {
	raw := make([]byte, 32)
	if _, err := rand.Read(raw); err != nil {
		return "", time.Time{}, err
	}
	token := base64.RawURLEncoding.EncodeToString(raw)
	expires := time.Now().Add(sessionLifetime)
	_, err := s.db.Exec(
		"INSERT INTO sessions(token_hash,user_id,expires_at,created_at) VALUES (?,?,?,?)",
		tokenHash(token), userID, float64(expires.Unix()), float64(time.Now().Unix()),
	)
	return token, expires, err
}

func (s *Store) SessionUser(token string) (User, error) {
	if token == "" {
		return User{}, ErrDenied
	}
	var userID string
	err := s.db.QueryRow(
		"SELECT user_id FROM sessions WHERE token_hash=? AND expires_at>?",
		tokenHash(token), float64(time.Now().Unix()),
	).Scan(&userID)
	if errors.Is(err, sql.ErrNoRows) {
		return User{}, ErrDenied
	}
	if err != nil {
		return User{}, err
	}
	user, err := s.User(userID)
	if err != nil || user.Status != StatusActive {
		return User{}, ErrDenied
	}
	return user, nil
}

func (s *Store) DeleteSession(token string) error {
	_, err := s.db.Exec("DELETE FROM sessions WHERE token_hash=?", tokenHash(token))
	return err
}

func (s *Store) ReplaceAccounts(userID string, accountIDs []string) (User, error) {
	user, err := s.User(userID)
	if err != nil {
		return User{}, err
	}
	if user.IsOwner() {
		return User{}, ErrDenied
	}
	seen := make(map[string]bool, len(accountIDs))
	for _, accountID := range accountIDs {
		if len(accountID) != 32 || strings.Trim(accountID, "0123456789abcdef") != "" || seen[accountID] {
			return User{}, ErrInvalid
		}
		seen[accountID] = true
	}
	tx, err := s.db.Begin()
	if err != nil {
		return User{}, err
	}
	defer tx.Rollback()
	if _, err := tx.Exec("DELETE FROM user_accounts WHERE user_id=?", userID); err != nil {
		return User{}, err
	}
	now := float64(time.Now().Unix())
	for _, accountID := range accountIDs {
		if _, err := tx.Exec(
			"INSERT INTO user_accounts(account_id,user_id,created_at) VALUES (?,?,?) "+
				"ON CONFLICT(account_id) DO UPDATE SET user_id=excluded.user_id,created_at=excluded.created_at",
			accountID, userID, now,
		); err != nil {
			return User{}, err
		}
	}
	if err := tx.Commit(); err != nil {
		return User{}, err
	}
	return s.User(userID)
}

func (s *Store) AccountOwner(accountID string) (string, error) {
	var userID string
	err := s.db.QueryRow("SELECT user_id FROM user_accounts WHERE account_id=?", accountID).Scan(&userID)
	if errors.Is(err, sql.ErrNoRows) {
		return "", ErrNotFound
	}
	return userID, err
}

type Charge struct {
	Source  string `json:"source"`
	Courses int    `json:"courses"`
}

func (s *Store) CheckCharge(userID string, courses int) (string, error) {
	if courses < 1 || courses > 50 {
		return "", ErrInvalid
	}
	var status string
	var credits int
	var expires sql.NullFloat64
	var limit sql.NullInt64
	if err := s.db.QueryRow(
		"SELECT status,credits,plan_expires_at,plan_course_limit FROM users WHERE id=?",
		userID,
	).Scan(&status, &credits, &expires, &limit); err != nil {
		return "", err
	}
	if status != StatusActive {
		return "", ErrDenied
	}
	if expires.Valid && limit.Valid && expires.Float64 > float64(time.Now().Unix()) {
		if courses > int(limit.Int64) {
			return "", fmt.Errorf("%w: monthly plan allows at most %d courses per task",
				ErrDenied, limit.Int64)
		}
		return "plan", nil
	}
	if credits < courses {
		return "", fmt.Errorf("%w: only %d course credits remain", ErrDenied, credits)
	}
	return "credits", nil
}

func (s *Store) ChargeJob(userID, jobID string, courses int) (Charge, error) {
	if len(jobID) != 32 || courses < 1 || courses > 50 {
		return Charge{}, ErrInvalid
	}
	tx, err := s.db.Begin()
	if err != nil {
		return Charge{}, err
	}
	defer tx.Rollback()
	var exists int
	if err := tx.QueryRow("SELECT count(*) FROM job_usage WHERE job_id=?", jobID).Scan(&exists); err != nil {
		return Charge{}, err
	}
	if exists > 0 {
		return Charge{}, nil
	}
	var credits int
	var status, role string
	var expires sql.NullFloat64
	var limit sql.NullInt64
	if err := tx.QueryRow(
		"SELECT role,status,credits,plan_expires_at,plan_course_limit FROM users WHERE id=?",
		userID,
	).Scan(&role, &status, &credits, &expires, &limit); err != nil {
		return Charge{}, err
	}
	if status != StatusActive {
		return Charge{}, ErrDenied
	}
	source := "credits"
	if expires.Valid && limit.Valid && expires.Float64 > float64(time.Now().Unix()) {
		if courses > int(limit.Int64) {
			return Charge{}, fmt.Errorf("%w: monthly plan allows at most %d courses per task",
				ErrDenied, limit.Int64)
		}
		source = "plan"
	} else {
		if credits < courses {
			return Charge{}, fmt.Errorf("%w: only %d course credits remain", ErrDenied, credits)
		}
		if _, err := tx.Exec("UPDATE users SET credits=credits-?,updated_at=? WHERE id=? AND credits>=?",
			courses, float64(time.Now().Unix()), userID, courses); err != nil {
			return Charge{}, err
		}
	}
	if _, err := tx.Exec(
		"INSERT INTO job_usage(job_id,user_id,courses,source,created_at) VALUES (?,?,?,?,?)",
		jobID, userID, courses, source, float64(time.Now().Unix()),
	); err != nil {
		return Charge{}, err
	}
	if err := tx.Commit(); err != nil {
		return Charge{}, err
	}
	return Charge{Source: source, Courses: courses}, nil
}
