package main

import (
	"html/template"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestManualArchiveAndRestore(t *testing.T) {
	root := t.TempDir()
	key := "abcdefghijklmnopqrstuvwxyz123456"
	shareDir := filepath.Join(root, key)
	if err := os.Mkdir(shareDir, 0700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(shareDir, "index.md"), []byte("# Private archive\n"), 0600); err != nil {
		t.Fatal(err)
	}
	auth := newAuth([]byte(strings.Repeat("x", 32)), defaultAuthPath, "owner", "example-password", defaultSessionTTL)
	server := &server{root: root, tmpl: template.Must(template.ParseFS(templateFS, "templates/*.html")), auth: auth}
	token, err := auth.issue()
	if err != nil {
		t.Fatal(err)
	}
	request := func(method, url, form string, signedIn bool) *httptest.ResponseRecorder {
		response := httptest.NewRecorder()
		req := httptest.NewRequest(method, url, strings.NewReader(form))
		if signedIn {
			req.AddCookie(&http.Cookie{Name: sessionCookie, Value: token})
		}
		if form != "" {
			req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
		}
		server.ServeHTTP(response, req)
		return response
	}

	if got := request(http.MethodGet, "/archive", "", false).Code; got != http.StatusNotFound {
		t.Fatalf("archive without session: %d", got)
	}
	archive := request(http.MethodPost, "/?action=archive", "key="+key, true)
	if archive.Code != http.StatusSeeOther {
		t.Fatalf("archive action: %d", archive.Code)
	}
	if got := request(http.MethodGet, "/"+key+"/", "", false).Code; got != http.StatusNotFound {
		t.Fatalf("public archived URL: %d", got)
	}
	if got := request(http.MethodGet, "/archive/"+key+"/", "", false).Code; got != http.StatusNotFound {
		t.Fatalf("private archived URL without session: %d", got)
	}
	privatePage := request(http.MethodGet, "/archive/"+key+"/", "", true)
	if privatePage.Code != http.StatusOK || !strings.Contains(privatePage.Body.String(), "Private archive") {
		t.Fatalf("private archived page: %d %q", privatePage.Code, privatePage.Body.String())
	}
	archiveList := request(http.MethodGet, "/archive", "", true)
	if archiveList.Code != http.StatusOK || !strings.Contains(archiveList.Body.String(), key) {
		t.Fatalf("archive list: %d", archiveList.Code)
	}
	restore := request(http.MethodPost, "/?action=restore", "key="+key, true)
	if restore.Code != http.StatusSeeOther {
		t.Fatalf("restore action: %d", restore.Code)
	}
	if got := request(http.MethodGet, "/"+key+"/", "", false).Code; got != http.StatusOK {
		t.Fatalf("restored public URL: %d", got)
	}
	if got := request(http.MethodGet, "/archive/"+key+"/", "", true).Code; got != http.StatusNotFound {
		t.Fatalf("restored archive URL: %d", got)
	}
}
