// Command xingguang serves the server web console behind a reverse proxy.
package main

import (
	"context"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"time"

	"xingguang/internal/server"
)

func main() {
	host := flag.String("host", "127.0.0.1", "address to listen on")
	port := flag.Int("port", 0, "port to listen on, 0 picks a free port")
	openBrowser := flag.Bool("open", false, "open the app after startup")
	dataDir := flag.String("data", "", "directory for local data")
	backendURL := flag.String("backend-url", os.Getenv("XINGGUANG_BACKEND_URL"), "task service root URL")
	backendTokenFile := flag.String("backend-token-file", "", "private file containing the task API token")
	aiWindowsURL := flag.String("ai-windows-url", os.Getenv("XINGGUANG_AI_WINDOWS_URL"), "loopback AI browser display URL")
	aiWindowsTokenFile := flag.String("ai-windows-token-file", os.Getenv("XINGGUANG_AI_WINDOWS_TOKEN_FILE"), "private AI display token file")
	ownerUsername := flag.String("owner-username", os.Getenv("XINGGUANG_OWNER_USERNAME"), "initial owner username")
	flag.Parse()
	ip := net.ParseIP(*host)
	if *host != "localhost" && (ip == nil || !ip.IsLoopback()) {
		log.Fatal("the local client must listen on a loopback address")
	}

	dir := *dataDir
	if dir == "" {
		base, err := os.UserConfigDir()
		if err != nil {
			log.Fatal(err)
		}
		dir = filepath.Join(base, "Xingguang")
	}

	if *ownerUsername == "" {
		*ownerUsername = "admin"
	}
	ownerPassword := os.Getenv("XINGGUANG_OWNER_PASSWORD")
	if ownerPassword == "" {
		log.Fatal("XINGGUANG_OWNER_PASSWORD is required for the initial owner account")
	}
	app, err := server.NewWithOwner(dir, *ownerUsername, ownerPassword)
	if err != nil {
		log.Fatal(err)
	}
	defer app.Close()
	if *aiWindowsURL != "" {
		raw, err := os.ReadFile(*aiWindowsTokenFile)
		if err != nil {
			log.Fatal("cannot read AI display token file")
		}
		if err := app.ConfigureAIWindows(*aiWindowsURL, strings.TrimSpace(string(raw))); err != nil {
			log.Fatal(err)
		}
	}
	if *backendURL != "" {
		token := os.Getenv("XINGGUANG_BACKEND_TOKEN")
		if *backendTokenFile != "" {
			raw, err := os.ReadFile(*backendTokenFile)
			if err != nil {
				log.Fatal("cannot read backend token file")
			}
			token = strings.TrimSpace(string(raw))
		}
		if err := app.ConfigureBackend(*backendURL, token); err != nil {
			log.Fatal(err)
		}
	}
	listener, err := net.Listen("tcp", net.JoinHostPort(*host, strconv.Itoa(*port)))
	if err != nil {
		log.Fatal(err)
	}

	httpServer := &http.Server{
		Handler: app.Handler(), ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout: 20 * time.Second, WriteTimeout: 25 * time.Second,
		IdleTimeout: 60 * time.Second, MaxHeaderBytes: 16 << 10,
	}
	app.SetShutdown(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		_ = httpServer.Shutdown(ctx)
	})

	url := "http://" + listener.Addr().String() + "/"
	fmt.Println(url)
	if *openBrowser {
		go func() {
			time.Sleep(200 * time.Millisecond)
			if err := openURL(url); err != nil {
				log.Printf("open window: %v", err)
			}
		}()
	}
	if err := httpServer.Serve(listener); err != nil && err != http.ErrServerClosed {
		log.Fatal(err)
	}
}

func openURL(url string) error {
	switch runtime.GOOS {
	case "windows":
		return exec.Command("rundll32", "url.dll,FileProtocolHandler", url).Start()
	case "darwin":
		return exec.Command("open", url).Start()
	default:
		return exec.Command("xdg-open", url).Start()
	}
}
