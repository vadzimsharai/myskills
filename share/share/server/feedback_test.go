package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const feedbackKey = "fbKeyFbKeyFbKeyFbKey1234"

func enableFeedback(t *testing.T, dir string) string {
	t.Helper()
	p := filepath.Join(dir, feedbackFile)
	if err := os.WriteFile(p, nil, 0600); err != nil {
		t.Fatal(err)
	}
	return p
}

func TestFeedbackDisabledIsNotFound(t *testing.T) {
	s, root := newTestServer(t)
	makeShare(t, root, feedbackKey, fullMeta{Title: "q"})
	rec := do(s, httptest.NewRequest(http.MethodPost, "/"+feedbackKey+"/feedback", strings.NewReader(`{"a":1}`)))
	if rec.Code == http.StatusCreated {
		t.Fatalf("feedback accepted without opt-in")
	}
	if _, err := os.Stat(filepath.Join(root, feedbackKey, feedbackFile)); err == nil {
		t.Fatalf("log created without opt-in")
	}
}

func TestFeedbackAppendAndRead(t *testing.T) {
	s, root := newTestServer(t)
	dir := makeShare(t, root, feedbackKey, fullMeta{Title: "q"})
	logPath := enableFeedback(t, dir)

	for _, body := range []string{`{"item":"A1","answer":"yes"}`, " {\"item\":\"B2\",\n\"answer\":\"no\"} "} {
		rec := do(s, httptest.NewRequest(http.MethodPost, "/"+feedbackKey+"/feedback", strings.NewReader(body)))
		if rec.Code != http.StatusCreated {
			t.Fatalf("post: %d %s", rec.Code, rec.Body.String())
		}
	}
	raw, _ := os.ReadFile(logPath)
	lines := strings.Split(strings.TrimSpace(string(raw)), "\n")
	if len(lines) != 2 {
		t.Fatalf("want 2 lines, got %d: %q", len(lines), raw)
	}
	var e feedbackEntry
	if err := json.Unmarshal([]byte(lines[1]), &e); err != nil || e.At == "" || string(e.Data) != `{"item":"B2","answer":"no"}` {
		t.Fatalf("bad entry %q: %v", lines[1], err)
	}

	rec := do(s, httptest.NewRequest(http.MethodGet, "/"+feedbackKey+"/feedback", nil))
	if rec.Code != http.StatusOK || rec.Body.String() != string(raw) {
		t.Fatalf("get: %d %q", rec.Code, rec.Body.String())
	}
}

func TestFeedbackRejectsNonObjects(t *testing.T) {
	s, root := newTestServer(t)
	dir := makeShare(t, root, feedbackKey, fullMeta{Title: "q"})
	logPath := enableFeedback(t, dir)
	for _, body := range []string{``, `[1]`, `"x"`, `{bad`, strings.Repeat("x", maxFeedbackBody+1)} {
		rec := do(s, httptest.NewRequest(http.MethodPost, "/"+feedbackKey+"/feedback", strings.NewReader(body)))
		if rec.Code == http.StatusCreated {
			t.Fatalf("accepted %q", body[:min(len(body), 20)])
		}
	}
	if raw, _ := os.ReadFile(logPath); len(raw) != 0 {
		t.Fatalf("log written: %q", raw)
	}
}

func TestFeedbackLogIsNotServedAsFile(t *testing.T) {
	s, root := newTestServer(t)
	dir := makeShare(t, root, feedbackKey, fullMeta{Title: "q"})
	enableFeedback(t, dir)
	rec := do(s, httptest.NewRequest(http.MethodGet, "/"+feedbackKey+"/"+feedbackFile, nil))
	if rec.Code != http.StatusNotFound {
		t.Fatalf("dotfile served: %d", rec.Code)
	}
}

func TestFeedbackCapsTotalSize(t *testing.T) {
	s, root := newTestServer(t)
	dir := makeShare(t, root, feedbackKey, fullMeta{Title: "q"})
	logPath := enableFeedback(t, dir)
	if err := os.Truncate(logPath, maxFeedbackTotal); err != nil {
		t.Fatal(err)
	}
	rec := do(s, httptest.NewRequest(http.MethodPost, "/"+feedbackKey+"/feedback", strings.NewReader(`{"a":1}`)))
	if rec.Code != http.StatusInsufficientStorage {
		t.Fatalf("want 507, got %d", rec.Code)
	}
}
