package main

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"fmt"
	"html/template"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestArchiveInactiveAfterSevenDays(t *testing.T) {
	root := t.TempDir()
	now := time.Now().UTC()
	keys := []string{strings.Repeat("a", 32), strings.Repeat("b", 32), strings.Repeat("c", 32), strings.Repeat("d", 32)}
	for _, key := range keys {
		dir := filepath.Join(root, key)
		if err := os.Mkdir(dir, 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(filepath.Join(dir, "index.md"), []byte("# Example\n"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	for key, age := range map[string]time.Duration{keys[0]: 8 * 24 * time.Hour, keys[1]: 6 * 24 * time.Hour, keys[3]: 8 * 24 * time.Hour} {
		if err := touchVisit(filepath.Join(root, key), now.Add(-age)); err != nil {
			t.Fatal(err)
		}
	}
	server := &server{root: root, tmpl: template.Must(template.ParseFS(templateFS, "templates/*.html")), auth: newAuth([]byte(strings.Repeat("x", 32)), defaultAuthPath, "", "", defaultSessionTTL)}
	response := httptest.NewRecorder()
	server.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/"+keys[3]+"/", nil))
	if response.Code != http.StatusOK {
		t.Fatalf("visit before scan: %d", response.Code)
	}
	server.archiveInactive(now)
	if _, err := os.Stat(filepath.Join(root, archiveDirName, keys[0], "index.md")); err != nil {
		t.Fatalf("idle share was not archived: %v", err)
	}
	for _, key := range keys[1:] {
		if _, err := os.Stat(filepath.Join(root, key, "index.md")); err != nil {
			t.Errorf("active share %s was archived: %v", key, err)
		}
	}
	if _, err := os.Stat(filepath.Join(root, keys[2], lastVisitFile)); err != nil {
		t.Fatalf("legacy share did not get a grace period: %v", err)
	}
}

func TestArchivedShareCannotBeReachedThroughActiveLinks(t *testing.T) {
	root := t.TempDir()
	archivedKey := strings.Repeat("a", 32)
	activeKey := strings.Repeat("b", 32)
	for _, key := range []string{archivedKey, activeKey} {
		if err := os.Mkdir(filepath.Join(root, key), 0700); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.WriteFile(filepath.Join(root, archivedKey, "index.md"), []byte("# Hidden\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, archivedKey, "secret.html"), []byte("<h1>Hidden archive content</h1>"), 0600); err != nil {
		t.Fatal(err)
	}
	secret := []byte(strings.Repeat("x", 32))
	server := &server{root: root, secret: secret, tmpl: template.Must(template.ParseFS(templateFS, "templates/*.html")), auth: newAuth(secret, defaultAuthPath, "", "", defaultSessionTTL)}
	if err := server.archiveShare(archivedKey); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(root, archiveDirName, archivedKey), filepath.Join(root, activeKey, "leak")); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(root, archiveDirName, archivedKey), filepath.Join(root, strings.Repeat("c", 32))); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(filepath.Join(root, archiveDirName, archivedKey, "secret.html"), filepath.Join(root, activeKey, indexFile)); err != nil {
		t.Fatal(err)
	}
	indexResponse := httptest.NewRecorder()
	server.ServeHTTP(indexResponse, httptest.NewRequest(http.MethodGet, "/"+activeKey+"/", nil))
	if strings.Contains(indexResponse.Body.String(), "Hidden archive content") {
		t.Error("HTML index symlink exposed archived content")
	}
	payload := base64.RawURLEncoding.EncodeToString([]byte(fmt.Sprintf("%s|%s||%d", tokenVersion, folderHash(archivedKey), time.Now().Add(time.Hour).Unix())))
	mac := hmac.New(sha256.New, secret)
	mac.Write([]byte(payload))
	token := payload + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
	for _, url := range []string{
		"/" + activeKey + "/leak/index.md",
		"/" + strings.Repeat("c", 32) + "/index.md",
		"/l/" + token + "/",
	} {
		response := httptest.NewRecorder()
		server.ServeHTTP(response, httptest.NewRequest(http.MethodGet, url, nil))
		if response.Code != http.StatusNotFound {
			t.Errorf("archived content leaked at %s: %d", url, response.Code)
		}
	}
}
