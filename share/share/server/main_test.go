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

func TestMarkdownIndex(t *testing.T) {
	registerTextTypes()
	root := t.TempDir()
	key := "abcdefghijklmnopqrstuvwxyz123456"
	shareDir := filepath.Join(root, key)
	if err := os.Mkdir(shareDir, 0700); err != nil {
		t.Fatal(err)
	}
	content := "# Guide\n\n| Name | Value |\n| --- | --- |\n| one | two |\n\n- [x] Done\n\n~~old~~\n\n[asset](image.png)\n\n<script>alert(1)</script>\n"
	if err := os.WriteFile(filepath.Join(shareDir, "index.md"), []byte(content), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(shareDir, "image.png"), []byte("image"), 0600); err != nil {
		t.Fatal(err)
	}
	server := &server{root: root, tmpl: template.Must(template.ParseFS(templateFS, "templates/*.html")), auth: newAuth([]byte(strings.Repeat("x", 32)), defaultAuthPath, "", "", defaultSessionTTL)}
	shares := server.collectShares()
	if len(shares) != 1 || !shares[0].IsSite {
		t.Fatal("Markdown index should appear as a page on the dashboard")
	}

	request := func(url string) *httptest.ResponseRecorder {
		response := httptest.NewRecorder()
		server.ServeHTTP(response, httptest.NewRequest(http.MethodGet, url, nil))
		return response
	}

	page := request("/" + key + "/")
	if page.Code != http.StatusOK || !strings.HasPrefix(page.Header().Get("Content-Type"), "text/html") {
		t.Fatalf("Markdown index response: %d %q", page.Code, page.Header().Get("Content-Type"))
	}
	for _, fragment := range []string{"<h1", "Guide</h1>", "<table>", "type=\"checkbox\"", "<del>old</del>", "href=\"image.png\""} {
		if !strings.Contains(page.Body.String(), fragment) {
			t.Errorf("rendered page missing %q", fragment)
		}
	}
	if strings.Contains(page.Body.String(), "<script>alert(1)</script>") {
		t.Error("raw HTML executed in Markdown page")
	}

	listing := request("/" + key + "/?files")
	if !strings.Contains(listing.Body.String(), "index.md") || !strings.Contains(listing.Body.String(), "Open page") {
		t.Error("files view does not expose Markdown source and page link")
	}
	plain := request("/" + key + "/index.md")
	if plain.Code != http.StatusOK || !strings.HasPrefix(plain.Header().Get("Content-Type"), "text/plain") || plain.Body.String() != content {
		t.Error("direct Markdown file should remain plain text")
	}

	if err := os.WriteFile(filepath.Join(shareDir, "index.html"), []byte("<h1>HTML wins</h1>"), 0600); err != nil {
		t.Fatal(err)
	}
	htmlPage := request("/" + key + "/")
	if !strings.Contains(htmlPage.Body.String(), "HTML wins") || strings.Contains(htmlPage.Body.String(), "Guide</h1>") {
		t.Error("HTML index did not take precedence")
	}
}
