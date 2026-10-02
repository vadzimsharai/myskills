package main

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"io/fs"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

const (
	shortKeyLen = 8
	dateFormat  = "Monday, 2 January 2006"
	timeFormat  = "15:04"
)

type dashboardShare struct {
	Key          string
	ShortKey     string
	Title        string
	Description  string
	URL          string
	Created      time.Time
	CreatedLabel string
	IsSite       bool
	Private      bool
	Files        int
	Size         string
	Tokens       []tokenView
	LiveTokens   int
}

type tokenView struct {
	ID       string
	Label    string
	Kind     string
	Status   string
	Created  string
	Expires  string
	LastUsed string
}

// newTokenView is rendered exactly once, in the response to the POST that created it.
type newTokenView struct {
	ShareTitle string
	Token      string
	UploadURL  string
	Kind       string
	Expires    string
}

type dashboardGroup struct {
	Label  string
	Date   string
	Shares []dashboardShare
}

type dashboardData struct {
	Groups    []dashboardGroup
	Total     int
	Nonce     string
	AuthPath  string
	IsArchive bool
	NewToken  *newTokenView
}

type loginData struct {
	Nonce    string
	AuthPath string
	Error    string
}

type fullMeta struct {
	Title       string `json:"title"`
	Description string `json:"description"`
	Created     string `json:"created"`
	Private     bool   `json:"private,omitempty"`
}

func newNonce() string {
	b := make([]byte, 16)
	if _, err := rand.Read(b); err != nil {
		log.Printf("nonce: %v", err)
	}
	return base64.RawStdEncoding.EncodeToString(b)
}

// Dashboard and login pages are the only ones with a session context, so they get
// the strict headers the static shares deliberately do not.
func setPrivatePageHeaders(w http.ResponseWriter, nonce string) {
	h := w.Header()
	h.Set("Content-Type", "text/html; charset=utf-8")
	h.Set("Cache-Control", "no-store")
	h.Set("X-Frame-Options", "DENY")
	h.Set("Referrer-Policy", "same-origin")
	h.Set("Content-Security-Policy", fmt.Sprintf(
		"default-src 'none'; style-src 'nonce-%s'; script-src 'nonce-%s'; frame-src 'self'; img-src 'self' data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
		nonce, nonce))
}

func (s *server) serveDashboard(w http.ResponseWriter, r *http.Request) {
	if !s.auth.valid(r) {
		s.notFound(w, r)
		return
	}
	if r.Method == http.MethodPost {
		s.handleDashboardPost(w, r)
		return
	}
	s.renderDashboard(w, r, false)
}

func (s *server) renderDashboard(w http.ResponseWriter, r *http.Request, archived bool) {
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		w.Header().Set("Allow", "GET, HEAD, POST")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	s.writeDashboard(w, r, archived, nil)
}

func (s *server) writeDashboard(w http.ResponseWriter, r *http.Request, archived bool, newToken *newTokenView) {
	data := dashboardData{Nonce: newNonce(), AuthPath: s.auth.path, IsArchive: archived, NewToken: newToken}
	shares := s.collectShares()
	if archived {
		shares = s.collectArchivedShares()
	}
	data.Total = len(shares)
	data.Groups = groupByDate(shares, time.Local)
	setPrivatePageHeaders(w, data.Nonce)
	if r.Method == http.MethodHead {
		return
	}
	if err := s.tmpl.ExecuteTemplate(w, "dashboard.html", data); err != nil {
		log.Printf("dashboard: %v", err)
	}
}

func (s *server) serveLogin(w http.ResponseWriter, r *http.Request) {
	if !s.auth.enabled() {
		s.notFound(w, r)
		return
	}
	switch r.Method {
	case http.MethodGet, http.MethodHead:
		if s.auth.valid(r) {
			http.Redirect(w, r, "/", http.StatusFound)
			return
		}
		s.renderLogin(w, r, "", http.StatusOK)
	case http.MethodPost:
		s.handleLogin(w, r)
	default:
		w.Header().Set("Allow", "GET, HEAD, POST")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func (s *server) handleLogin(w http.ResponseWriter, r *http.Request) {
	if !sameSite(r) {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	ip := clientIP(r)
	if wait, ok := s.auth.limiter.allow(ip); !ok {
		w.Header().Set("Retry-After", fmt.Sprint(int(wait.Seconds())+1))
		s.renderLogin(w, r, "Too many attempts, try again later", http.StatusTooManyRequests)
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, loginBodyLimit)
	if err := r.ParseForm(); err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}
	if !s.auth.check(r.PostForm.Get("login"), r.PostForm.Get("password")) {
		s.auth.limiter.fail(ip)
		log.Printf("login failed from %s", ip)
		time.Sleep(failureDelay)
		s.renderLogin(w, r, "Wrong login or password", http.StatusUnauthorized)
		return
	}
	token, err := s.auth.issue()
	if err != nil {
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	s.auth.limiter.reset(ip)
	s.auth.setCookie(w, token)
	log.Printf("login ok from %s", ip)
	http.Redirect(w, r, "/", http.StatusSeeOther)
}

func (s *server) serveLogout(w http.ResponseWriter, r *http.Request) {
	if !s.auth.valid(r) {
		s.notFound(w, r)
		return
	}
	if r.Method != http.MethodPost {
		w.Header().Set("Allow", "POST")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if !sameSite(r) {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	s.auth.clearCookie(w)
	http.Redirect(w, r, "/"+s.auth.path, http.StatusSeeOther)
}

func (s *server) renderLogin(w http.ResponseWriter, r *http.Request, errMsg string, status int) {
	data := loginData{Nonce: newNonce(), AuthPath: s.auth.path, Error: errMsg}
	setPrivatePageHeaders(w, data.Nonce)
	w.WriteHeader(status)
	if r.Method == http.MethodHead {
		return
	}
	if err := s.tmpl.ExecuteTemplate(w, "login.html", data); err != nil {
		log.Printf("login page: %v", err)
	}
}

func (s *server) collectShares() []dashboardShare {
	return s.collectSharesAt(s.root, "/")
}

func (s *server) collectArchivedShares() []dashboardShare {
	return s.collectSharesAt(s.archiveRoot(), "/"+archivePath+"/")
}

func (s *server) collectSharesAt(root, baseURL string) []dashboardShare {
	entries, err := os.ReadDir(root)
	if err != nil {
		if !os.IsNotExist(err) {
			log.Printf("dashboard: read shares: %v", err)
		}
		return nil
	}
	now := time.Now()
	shares := make([]dashboardShare, 0, len(entries))
	for _, e := range entries {
		if !e.IsDir() || !keyPattern.MatchString(e.Name()) {
			continue
		}
		dir := filepath.Join(root, e.Name())
		meta := loadFullMeta(dir)
		created, err := time.Parse(time.RFC3339, meta.Created)
		if err != nil {
			if info, statErr := e.Info(); statErr == nil {
				created = info.ModTime()
			} else {
				created = time.Time{}
			}
		}
		files, size := folderStats(dir)
		_, indexErr := os.Stat(filepath.Join(dir, indexFile))
		_, markdownIndexErr := os.Stat(filepath.Join(dir, markdownIndexFile))
		tokens, live := tokenViews(dir, now)
		shares = append(shares, dashboardShare{
			Key:          e.Name(),
			ShortKey:     e.Name()[:shortKeyLen] + "…",
			Title:        meta.Title,
			Description:  meta.Description,
			URL:          baseURL + e.Name() + "/",
			Created:      created,
			CreatedLabel: created.In(time.Local).Format(timeFormat),
			IsSite:       !meta.Private && (indexErr == nil || markdownIndexErr == nil),
			Private:      meta.Private,
			Files:        files,
			Size:         humanSize(size),
			Tokens:       tokens,
			LiveTokens:   live,
		})
	}
	sort.SliceStable(shares, func(i, j int) bool { return shares[i].Created.After(shares[j].Created) })
	return shares
}

func tokenViews(dir string, now time.Time) ([]tokenView, int) {
	tf, err := loadTokens(dir)
	if err != nil {
		log.Printf("dashboard: %v", err)
		return nil, 0
	}
	stamp := func(v string) string {
		t, err := time.Parse(time.RFC3339, v)
		if err != nil {
			return ""
		}
		return t.In(time.Local).Format("2006-01-02 15:04")
	}
	views := make([]tokenView, 0, len(tf.Tokens))
	live := 0
	for _, tok := range tf.Tokens {
		status := tok.status(now)
		if status == "active" {
			live++
		}
		views = append(views, tokenView{
			ID:       tok.ID,
			Label:    tok.Label,
			Kind:     tok.kind(),
			Status:   status,
			Created:  stamp(tok.Created),
			Expires:  stamp(tok.Expires),
			LastUsed: stamp(tok.LastUsed),
		})
	}
	return views, live
}

func (s *server) createTokenFromDashboard(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}
	sc, ok := s.keyScope(r.PostForm.Get("key"))
	if !ok {
		s.notFound(w, r)
		return
	}
	kind := r.PostForm.Get("kind")
	var ttl time.Duration
	if raw := strings.TrimSpace(r.PostForm.Get("ttl")); kind != tokenKindAlways && raw != "" {
		var err error
		if ttl, err = parseTTL(raw); err != nil {
			http.Error(w, err.Error(), http.StatusBadRequest)
			return
		}
	}
	plain, tok, err := s.createToken(sc.dir, r.PostForm.Get("label"), kind, ttl, time.Now())
	if err != nil {
		log.Printf("create token: %v", err)
		http.Error(w, "could not create token", http.StatusBadRequest)
		return
	}
	log.Printf("token %s created via dashboard", tok.ID)
	expires := ""
	if exp, ok := tok.expiresAt(); ok {
		expires = exp.In(time.Local).Format("2006-01-02 15:04")
	}
	s.writeDashboard(w, r, false, &newTokenView{
		ShareTitle: sc.title,
		Token:      plain,
		UploadURL:  publicBase(r) + sc.baseURL + uploadSegment + "/" + plain,
		Kind:       tok.kind(),
		Expires:    expires,
	})
}

func loadFullMeta(dir string) fullMeta {
	meta := fullMeta{Title: defaultTitle}
	raw, err := os.ReadFile(filepath.Join(dir, metaFileName))
	if err != nil {
		return meta
	}
	if err := json.Unmarshal(raw, &meta); err != nil || meta.Title == "" {
		meta.Title = defaultTitle
	}
	return meta
}

// folderStats counts what the share actually serves: dotfiles skipped, symlinks not followed.
func folderStats(dir string) (int, int64) {
	var files int
	var size int64
	_ = filepath.WalkDir(dir, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			return nil
		}
		if p != dir && strings.HasPrefix(d.Name(), ".") {
			if d.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		if d.Type().IsRegular() {
			if info, err := d.Info(); err == nil {
				files++
				size += info.Size()
			}
		}
		return nil
	})
	return files, size
}

func groupByDate(shares []dashboardShare, loc *time.Location) []dashboardGroup {
	var groups []dashboardGroup
	today := time.Now().In(loc)
	y, m, d := today.Date()
	todayStart := time.Date(y, m, d, 0, 0, 0, 0, loc)
	for _, sh := range shares {
		local := sh.Created.In(loc)
		date := local.Format("2006-01-02")
		if n := len(groups); n > 0 && groups[n-1].Date == date {
			groups[n-1].Shares = append(groups[n-1].Shares, sh)
			continue
		}
		label := local.Format(dateFormat)
		switch dayStart := time.Date(local.Year(), local.Month(), local.Day(), 0, 0, 0, 0, loc); {
		case dayStart.Equal(todayStart):
			label = "Today"
		case dayStart.Equal(todayStart.AddDate(0, 0, -1)):
			label = "Yesterday"
		}
		groups = append(groups, dashboardGroup{Label: label, Date: date, Shares: []dashboardShare{sh}})
	}
	return groups
}
