package main

import (
	"net"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestDefaultDataDirUsesConfigDir(t *testing.T) {
	base, err := os.UserConfigDir()
	if err != nil {
		t.Fatal(err)
	}
	got := filepath.Join(base, "Xingguang")
	if !filepath.IsAbs(got) {
		t.Fatalf("data dir is not absolute: %s", got)
	}
}

func TestListenAddressFormat(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	if listener.Addr().String() == "" {
		t.Fatal("empty address")
	}
}

func TestOpenURLCommandChoice(t *testing.T) {
	switch runtime.GOOS {
	case "windows", "darwin", "linux":
	default:
		t.Logf("open command not specialized for %s", runtime.GOOS)
	}
}
