package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"html/template"
	"io"
	"mime/multipart"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const (
	testLogin    = "owner"
	testPassword = "correct-horse-battery"
)

func newTestServer(t *testing.T) (*server, string) {
	t.Helper()
	root := t.TempDir()
	return &server{
		root:   root,
		secret: []byte(strings.Repeat("s", 32)),
		tmpl:   template.Must(template.ParseFS(templateFS, "templates/*.html")),
		auth:   newAuth([]byte(strings.Repeat("s", 32)), defaultAuthPath, testLogin, testPassword, defaultSessionTTL),
	}, root
}

func makeShare(t *testing.T, root, key string, meta fullMeta) string {
	t.Helper()
	dir := filepath.Join(root, key)
	if err := os.Mkdir(dir, 0700); err != nil {
		t.Fatal(err)
	}
	raw, _ := json.Marshal(meta)
	if err := os.WriteFile(filepath.Join(dir, metaFileName), raw, 0600); err != nil {
		t.Fatal(err)
	}
	return dir
}

func do(s *server, req *http.Request) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	s.ServeHTTP(rec, req)
	return rec
}

func ownerCookie(t *testing.T, s *server) *http.Cookie {
	t.Helper()
	token, err := s.auth.issue()
	if err != nil {
		t.Fatal(err)
	}
	return &http.Cookie{Name: sessionCookie, Value: token}
}

func signTestLink(secret []byte, key, prefix string, exp time.Time) string {
	payload := base64.RawURLEncoding.EncodeToString([]byte(fmt.Sprintf("%s|%s|%s|%d", tokenVersion, folderHash(key), prefix, exp.Unix())))
	mac := hmac.New(sha256.New, secret)
	mac.Write([]byte(payload))
	return payload + "." + base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}

func decodeUpload(t *testing.T, rec *httptest.ResponseRecorder) []uploadedFile {
	t.Helper()
	if rec.Code != http.StatusCreated {
		t.Fatalf("upload: %d %s", rec.Code, rec.Body.String())
	}
	var body struct{ Files []uploadedFile }
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	return body.Files
}

func TestPrivateShareServesOnlyRandomNamedFiles(t *testing.T) {
	s, root := newTestServer(t)
	key := strings.Repeat("k", 32)
	dir := makeShare(t, root, key, fullMeta{Title: "Shots", Private: true})
	secretName := strings.Repeat("r", 32) + ".png"
	for _, name := range []string{secretName, "index.html", "report.pdf"} {
		if err := os.WriteFile(filepath.Join(dir, name), []byte("data"), 0600); err != nil {
			t.Fatal(err)
		}
	}
	if err := os.MkdirAll(filepath.Join(dir, strings.Repeat("d", 32)), 0700); err != nil {
		t.Fatal(err)
	}

	for _, path := range []string{"/" + key, "/" + key + "/", "/" + key + "/?files", "/" + key + "/index.html",
		"/" + key + "/report.pdf", "/" + key + "/" + strings.Repeat("d", 32) + "/"} {
		if rec := do(s, httptest.NewRequest(http.MethodGet, path, nil)); rec.Code != http.StatusNotFound {
			t.Errorf("GET %s = %d, want 404", path, rec.Code)
		}
	}
	if rec := do(s, httptest.NewRequest(http.MethodGet, "/"+key+"/"+secretName, nil)); rec.Code != http.StatusOK || rec.Body.String() != "data" {
		t.Errorf("random-named file: %d", rec.Code)
	}

	owner := httptest.NewRequest(http.MethodGet, "/"+key+"/?files", nil)
	owner.AddCookie(ownerCookie(t, s))
	rec := do(s, owner)
	if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), "report.pdf") || !strings.Contains(rec.Body.String(), "Private share") {
		t.Errorf("owner listing: %d", rec.Code)
	}
	if strings.Contains(rec.Body.String(), "New folder") {
		t.Error("private listing offers folder creation")
	}
}

func TestSignedFolderLinkIntoPrivateShareHasNoListing(t *testing.T) {
	s, root := newTestServer(t)
	key := strings.Repeat("k", 32)
	makeShare(t, root, key, fullMeta{Title: "Shots", Private: true})
	token := signTestLink(s.secret, key, "", time.Now().Add(time.Hour))
	if rec := do(s, httptest.NewRequest(http.MethodGet, "/l/"+token+"/", nil)); rec.Code != http.StatusNotFound {
		t.Errorf("signed folder link listed a private share: %d", rec.Code)
	}
}

func TestTokenUploadIntoPrivateShare(t *testing.T) {
	s, root := newTestServer(t)
	key := strings.Repeat("k", 32)
	dir := makeShare(t, root, key, fullMeta{Title: "Shots", Private: true})
	plain, _, err := s.createToken(dir, "laptop", tokenKindAlways, 0, time.Now())
	if err != nil {
		t.Fatal(err)
	}

	req := httptest.NewRequest(http.MethodPost, "/"+key+"/upload/"+plain+"?name=shot.PNG", strings.NewReader("pixels"))
	req.Header.Set("Content-Type", "image/png")
	req.Header.Set("X-Forwarded-Proto", "https")
	req.Host = "share.example"
	files := decodeUpload(t, do(s, req))
	if len(files) != 1 || !isPrivateFileName(files[0].Name) || !strings.HasSuffix(files[0].Name, ".png") || files[0].Size != 6 {
		t.Fatalf("stored as %+v", files)
	}
	if files[0].URL != "https://share.example/"+key+"/"+files[0].Name {
		t.Errorf("url %q", files[0].URL)
	}
	if rec := do(s, httptest.NewRequest(http.MethodGet, "/"+key+"/"+files[0].Name, nil)); rec.Body.String() != "pixels" {
		t.Error("uploaded file not served")
	}

	raw, _ := os.ReadFile(filepath.Join(dir, tokensFileName))
	if strings.Contains(string(raw), plain) {
		t.Error("plain token stored on disk")
	}
	if info, _ := os.Stat(filepath.Join(dir, tokensFileName)); info.Mode().Perm() != tokensFilePerm {
		t.Errorf("tokens file mode %v", info.Mode().Perm())
	}
	if rec := do(s, httptest.NewRequest(http.MethodGet, "/"+key+"/"+tokensFileName, nil)); rec.Code != http.StatusNotFound {
		t.Error("tokens file served")
	}
	// Forever tokens keep working.
	again := httptest.NewRequest(http.MethodPut, "/"+key+"/upload/"+plain+"/second.jpg", strings.NewReader("x"))
	if files := decodeUpload(t, do(s, again)); !strings.HasSuffix(files[0].Name, ".jpg") {
		t.Errorf("PUT name %q", files[0].Name)
	}
}

func TestTokenUploadIntoPublicShareKeepsNames(t *testing.T) {
	s, root := newTestServer(t)
	key := strings.Repeat("p", 32)
	dir := makeShare(t, root, key, fullMeta{Title: "Docs"})
	plain, _, _ := s.createToken(dir, "", tokenKindAlways, 0, time.Now())

	var body bytes.Buffer
	mw := multipart.NewWriter(&body)
	for _, name := range []string{"a.txt", "a.txt"} {
		part, _ := mw.CreateFormFile("files", name)
		io.WriteString(part, "hello")
	}
	mw.Close()
	req := httptest.NewRequest(http.MethodPost, "/"+key+"/upload/"+plain+"?plain", &body)
	req.Header.Set("Content-Type", mw.FormDataContentType())
	rec := do(s, req)
	if rec.Code != http.StatusCreated {
		t.Fatalf("multipart: %d %s", rec.Code, rec.Body.String())
	}
	lines := strings.Fields(rec.Body.String())
	if len(lines) != 2 || !strings.HasSuffix(lines[0], "/a.txt") || !strings.HasSuffix(lines[1], "/a-1.txt") {
		t.Errorf("plain reply %q", rec.Body.String())
	}
	for _, name := range []string{"a.txt", "a-1.txt"} {
		if _, err := os.Stat(filepath.Join(dir, name)); err != nil {
			t.Errorf("%s missing", name)
		}
	}
}

func TestTokenLifetimes(t *testing.T) {
	s, root := newTestServer(t)
	key := strings.Repeat("t", 32)
	dir := makeShare(t, root, key, fullMeta{Private: true})
	now := time.Now()
	upload := func(token string) int {
		return do(s, httptest.NewRequest(http.MethodPost, "/"+key+"/upload/"+token, strings.NewReader("x"))).Code
	}

	once, _, _ := s.createToken(dir, "", tokenKindOnce, 0, now)
	if code := upload(once); code != http.StatusCreated {
		t.Fatalf("one-time first use: %d", code)
	}
	if code := upload(once); code != http.StatusNotFound {
		t.Errorf("one-time second use: %d", code)
	}

	expired, _, _ := s.createToken(dir, "", tokenKindTTL, time.Minute, now.Add(-time.Hour))
	if code := upload(expired); code != http.StatusNotFound {
		t.Errorf("expired token: %d", code)
	}

	revoked, tok, _ := s.createToken(dir, "", tokenKindAlways, 0, now)
	if err := s.revokeToken(dir, tok.ID); err != nil {
		t.Fatal(err)
	}
	if code := upload(revoked); code != http.StatusNotFound {
		t.Errorf("revoked token: %d", code)
	}

	other := strings.Repeat("o", 32)
	makeShare(t, root, other, fullMeta{})
	valid, _, _ := s.createToken(dir, "", tokenKindAlways, 0, now)
	if code := do(s, httptest.NewRequest(http.MethodPost, "/"+other+"/upload/"+valid, strings.NewReader("x"))).Code; code != http.StatusNotFound {
		t.Errorf("token accepted by another share: %d", code)
	}
	if code := do(s, httptest.NewRequest(http.MethodPost, "/"+key+"/upload/"+valid, nil)).Code; code != http.StatusBadRequest {
		t.Errorf("empty body: %d", code)
	}
	if code := do(s, httptest.NewRequest(http.MethodPost, "/l/"+valid+"/upload/"+valid, strings.NewReader("x"))).Code; code == http.StatusCreated {
		t.Error("upload through a signed-link path")
	}
}

func TestTokenPipeScriptKeepsOneTimeTokenForUpload(t *testing.T) {
	s, root := newTestServer(t)
	key := strings.Repeat("p", 32)
	dir := makeShare(t, root, key, fullMeta{Private: true})
	once, _, _ := s.createToken(dir, "", tokenKindOnce, 10*time.Minute, time.Now())
	path := "/" + key + "/upload/" + once
	pipe := func() *httptest.ResponseRecorder {
		req := httptest.NewRequest(http.MethodGet, path, nil)
		req.Header.Set("X-Forwarded-Proto", "https")
		req.Host = "share.example"
		return do(s, req)
	}

	for range 2 {
		rec := pipe()
		if rec.Code != http.StatusOK || !strings.Contains(rec.Body.String(), "UPLOAD_URL='https://share.example"+path+"'") {
			t.Fatalf("pipe script: %d %q", rec.Code, rec.Body.String())
		}
	}
	if code := do(s, httptest.NewRequest(http.MethodPost, path, strings.NewReader("x"))).Code; code != http.StatusCreated {
		t.Fatalf("upload after fetching the script: %d", code)
	}
	if code := pipe().Code; code != http.StatusNotFound {
		t.Errorf("pipe script after the token was used: %d", code)
	}
	if code := do(s, httptest.NewRequest(http.MethodGet, "/"+key+"/upload/"+strings.Repeat("x", 43), nil)).Code; code != http.StatusNotFound {
		t.Errorf("pipe script for an unknown token: %d", code)
	}
}

func TestLiveTokenKeepsShareFromArchive(t *testing.T) {
	s, root := newTestServer(t)
	key := strings.Repeat("i", 32)
	dir := makeShare(t, root, key, fullMeta{Private: true})
	now := time.Now()
	if _, _, err := s.createToken(dir, "", tokenKindAlways, 0, now); err != nil {
		t.Fatal(err)
	}
	if err := touchVisit(dir, now.Add(-30*24*time.Hour)); err != nil {
		t.Fatal(err)
	}
	s.archiveInactive(now)
	if _, err := os.Stat(dir); err != nil {
		t.Error("upload inbox was archived")
	}
}

func TestDashboardTokenFlowAndPrivacyToggle(t *testing.T) {
	s, root := newTestServer(t)
	key := strings.Repeat("q", 32)
	dir := makeShare(t, root, key, fullMeta{Title: "Inbox", Created: time.Now().UTC().Format(time.RFC3339)})
	cookie := ownerCookie(t, s)
	post := func(target string, form url.Values) *httptest.ResponseRecorder {
		req := httptest.NewRequest(http.MethodPost, target, strings.NewReader(form.Encode()))
		req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
		req.AddCookie(cookie)
		return do(s, req)
	}

	rec := post("/?action=edit", url.Values{"key": {key}, "title": {"Inbox"}, "privacy": {"1"}, "private": {"on"}})
	if rec.Code != http.StatusSeeOther || !loadFullMeta(dir).Private {
		t.Fatalf("enable private: %d", rec.Code)
	}
	post("/?action=edit", url.Values{"key": {key}, "title": {"Renamed"}, "back": {"/" + key + "/?files"}})
	if meta := loadFullMeta(dir); !meta.Private || meta.Title != "Renamed" {
		t.Errorf("rename from the file manager changed privacy: %+v", meta)
	}

	rec = post("/?action=token-create", url.Values{"key": {key}, "label": {"laptop"}, "kind": {"ttl"}, "ttl": {"7d"}})
	if rec.Code != http.StatusOK {
		t.Fatalf("token-create: %d %s", rec.Code, rec.Body.String())
	}
	tf, _ := loadTokens(dir)
	if len(tf.Tokens) != 1 || tf.Tokens[0].Label != "laptop" || tf.Tokens[0].kind() != tokenKindTTL {
		t.Fatalf("stored tokens %+v", tf.Tokens)
	}
	page := rec.Body.String()
	start := strings.Index(page, "/upload/")
	if start < 0 {
		t.Fatal("new token page has no upload URL")
	}
	plain := page[start+len("/upload/") : start+len("/upload/")+43]
	if hashToken(plain) != tf.Tokens[0].Hash {
		t.Error("shown token does not match the stored hash")
	}
	if rec := do(s, httptest.NewRequest(http.MethodPost, "/"+key+"/upload/"+plain, strings.NewReader("x"))); rec.Code != http.StatusCreated {
		t.Errorf("dashboard token upload: %d", rec.Code)
	}

	list := httptest.NewRequest(http.MethodGet, "/", nil)
	list.AddCookie(cookie)
	body := do(s, list).Body.String()
	if strings.Contains(body, plain) || !strings.Contains(body, "laptop") || !strings.Contains(body, "private") {
		t.Error("dashboard must list the token label but never the token")
	}

	post("/?action=token-revoke", url.Values{"key": {key}, "id": {tf.Tokens[0].ID}})
	if tf, _ := loadTokens(dir); len(tf.Tokens) != 0 {
		t.Error("revoke did not remove the token")
	}
}

func TestPrivateOwnerUploadGetsRandomNames(t *testing.T) {
	s, root := newTestServer(t)
	key := strings.Repeat("u", 32)
	dir := makeShare(t, root, key, fullMeta{Private: true})
	var body bytes.Buffer
	mw := multipart.NewWriter(&body)
	part, _ := mw.CreateFormFile("files", "holiday.jpg")
	io.WriteString(part, "img")
	mw.Close()
	req := httptest.NewRequest(http.MethodPost, "/"+key+"/?upload", &body)
	req.Header.Set("Content-Type", mw.FormDataContentType())
	req.AddCookie(ownerCookie(t, s))
	if rec := do(s, req); rec.Code != http.StatusSeeOther {
		t.Fatalf("owner upload: %d %s", rec.Code, rec.Body.String())
	}
	entries, _ := os.ReadDir(dir)
	for _, e := range entries {
		if !strings.HasPrefix(e.Name(), ".") && (!isPrivateFileName(e.Name()) || !strings.HasSuffix(e.Name(), ".jpg")) {
			t.Errorf("owner upload kept a guessable name: %s", e.Name())
		}
	}

	mkdir := httptest.NewRequest(http.MethodPost, "/"+key+"/?mkdir", strings.NewReader("name=docs"))
	mkdir.Header.Set("Content-Type", "application/x-www-form-urlencoded")
	mkdir.AddCookie(ownerCookie(t, s))
	if rec := do(s, mkdir); rec.Code != http.StatusBadRequest {
		t.Errorf("mkdir in private share: %d", rec.Code)
	}
}

func TestMaskPathHidesEverySecretSegment(t *testing.T) {
	key, token, file := strings.Repeat("k", 32), strings.Repeat("t", 43), strings.Repeat("f", 32)+".png"
	for _, p := range []string{"/" + key + "/upload/" + token, "/" + key + "/" + file, "/l/" + token + "." + token + "/x"} {
		masked := maskPath(p)
		if strings.Contains(masked, key) || strings.Contains(masked, token) || strings.Contains(masked, strings.Repeat("f", 32)) {
			t.Errorf("maskPath(%q) = %q", p, masked)
		}
	}
	if got := maskPath("/" + key + "/docs/readme.md"); got != "/~/docs/readme.md" {
		t.Errorf("maskPath keeps ordinary names: %q", got)
	}
}

func TestParseTTL(t *testing.T) {
	for in, want := range map[string]time.Duration{"30m": 30 * time.Minute, "12h": 12 * time.Hour, "7d": 7 * 24 * time.Hour} {
		if got, err := parseTTL(in); err != nil || got != want {
			t.Errorf("parseTTL(%q) = %v, %v", in, got, err)
		}
	}
	for _, in := range []string{"", "0d", "7", "1w", "-1h"} {
		if _, err := parseTTL(in); err == nil {
			t.Errorf("parseTTL(%q) accepted", in)
		}
	}
}
