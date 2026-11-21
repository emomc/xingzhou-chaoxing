package auth

import (
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func openTest(t *testing.T) (*Store, string) {
	t.Helper()
	dir := t.TempDir()
	store, err := Open(dir, "owner", "offline-owner-password")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	return store, dir
}

func TestOwnerSessionPasswordAndPersistence(t *testing.T) {
	store, dir := openTest(t)
	owner, err := store.Authenticate("owner", "offline-owner-password")
	if err != nil || !owner.IsOwner() {
		t.Fatal("owner authentication failed")
	}
	if _, err := store.Authenticate("owner", "wrong-password"); !errors.Is(err, ErrDenied) {
		t.Fatal("wrong password was accepted")
	}
	token, _, err := store.CreateSession(owner.ID)
	if err != nil || token == "" {
		t.Fatal(err)
	}
	if current, err := store.SessionUser(token); err != nil || current.ID != owner.ID {
		t.Fatal("session lookup failed")
	}
	if err := store.DeleteSession(token); err != nil {
		t.Fatal(err)
	}
	if _, err := store.SessionUser(token); !errors.Is(err, ErrDenied) {
		t.Fatal("deleted session remained active")
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(filepath.Join(dir, Filename()))
	if err != nil {
		t.Fatal(err)
	}
	if len(raw) == 0 {
		t.Fatal("empty user database")
	}
	reopened, err := Open(dir, "ignored", "ignored-password")
	if err != nil {
		t.Fatal(err)
	}
	defer reopened.Close()
	if _, err := reopened.Authenticate("owner", "offline-owner-password"); err != nil {
		t.Fatal("owner did not persist")
	}
}

func Filename() string { return "users.sqlite3" }

func TestUsersAccountsAndNonRefundableCourseCharges(t *testing.T) {
	store, _ := openTest(t)
	user, err := store.CreateUser("student", "student-password", RoleUser, 3, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := store.CreateUser("STUDENT", "another-password", RoleUser, 1, nil, nil); !errors.Is(err, ErrConflict) {
		t.Fatal("duplicate username was accepted case-insensitively")
	}
	accountID := "a" + string(make([]byte, 0)) + "0123456789abcdef0123456789abcde"
	if len(accountID) != 32 {
		t.Fatal("bad fixture")
	}
	otherID := "b" + accountID[1:]
	updated, err := store.ReplaceAccounts(user.ID, []string{accountID, otherID})
	if err != nil || len(updated.AccountIDs) != 2 {
		t.Fatal("account assignment failed")
	}
	if owner, err := store.AccountOwner(accountID); err != nil || owner != user.ID {
		t.Fatal("account owner lookup failed")
	}
	jobID := "1" + accountID[1:]
	charge, err := store.ChargeJob(user.ID, jobID, 2)
	if err != nil || charge.Source != "credits" || charge.Courses != 2 {
		t.Fatalf("credit charge = %+v, %v", charge, err)
	}
	current, _ := store.User(user.ID)
	if current.Credits != 1 {
		t.Fatalf("remaining credits = %d", current.Credits)
	}
	if duplicate, err := store.ChargeJob(user.ID, jobID, 2); err != nil || duplicate.Source != "" {
		t.Fatal("same job was charged twice")
	}
	if _, err := store.ChargeJob(user.ID, "2"+accountID[1:], 2); !errors.Is(err, ErrDenied) {
		t.Fatal("insufficient credits were accepted")
	}
	current, _ = store.User(user.ID)
	if current.Credits != 1 {
		t.Fatal("failed charge changed credits")
	}
	expires := float64(time.Now().Add(24 * time.Hour).Unix())
	limit := 5
	if _, err := store.UpdateUser(user.ID, "", StatusActive, 1, &expires, &limit); err != nil {
		t.Fatal(err)
	}
	planCharge, err := store.ChargeJob(user.ID, "3"+accountID[1:], 5)
	if err != nil || planCharge.Source != "plan" {
		t.Fatalf("plan charge = %+v, %v", planCharge, err)
	}
	current, _ = store.User(user.ID)
	if current.Credits != 1 {
		t.Fatal("plan usage deducted credits")
	}
	if _, err := store.ChargeJob(user.ID, "4"+accountID[1:], 6); !errors.Is(err, ErrDenied) {
		t.Fatal("plan course limit was bypassed")
	}
}
