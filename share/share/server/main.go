package main

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"embed"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"html/template"
	"io/fs"
	"log"
	"mime"
	"net/http"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
	_ "time/tzdata"

	"github.com/yuin/goldmark"
	"github.com/yuin/goldmark/extension"
	"github.com/yuin/goldmark/parser"
)

const (
	metaFileName      = ".share.json"
	indexFile         = "index.html"
	markdownIndexFile = "index.md"
	filesParam        = "files"
	linkPrefix        = "l"
	archivePath       = "archive"
	tokenVersion      = "v1"
	folderHashLen     = 16
	defaultTitle      = "Shared folder"
)

var (
	// The folder name is the secret: long enough that enumeration is hopeless.
	keyPattern   = regexp.MustCompile(`^[A-Za-z0-9_-]{20,128}$`)
	tokenPattern = regexp.MustCompile(`^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$`)
	// Keys, link tokens, upload tokens and private file names are all long runs of
	// this alphabet; every such segment is masked before logging.
	logMask = regexp.MustCompile(`[A-Za-z0-9_-]{20,}`)
)

//go:embed templates/*.html
var templateFS embed.FS

type shareMeta struct {
	Title string `json:"title"`
}

type server struct {
	root      string
	secret    []byte
	tmpl      *template.Template
	auth      *auth
	archiveMu  sync.Mutex
	tokenMu    sync.Mutex
	feedbackMu sync.Mutex
}

// scope is the subtree a request may touch: the share folder plus, for signed
// links, the prefix the token was issued for.
type scope struct {
	dir      string // absolute folder on disk
	prefix   string // "" (whole share), "sub/dir/" or "sub/file"
	baseURL  string // "/<key>/" or "/l/<token>/"
	title    string
	private  bool // no listings for visitors; only random-named files are served
	canWrite bool // owner session on a key URL; never for signed links
}

type listingEntry struct {
	Name     string
	Href     string
	IsDir    bool
	Size     string
	ModTime  string
	Editable bool
	EditHref string
}

type crumb struct {
	Name string
	Href string
}

type listingData struct {
	Title     string
	Path      string
	Crumbs    []crumb
	Entries   []listingEntry
	Parent    string
	Suffix    string // "?files" while browsing a site as a folder, so links stay in that mode
	HasIndex  bool
	CanWrite  bool
	IsArchive bool
	Private   bool
	FilesURL  string
	Key       string // set only at a share root under an owner session (title editing)
	RootURL   string
}

type markdownData struct {
	Title   string
	Content template.HTML
}

var markdownRenderer = goldmark.New(
	goldmark.WithExtensions(extension.GFM, extension.Footnote),
	goldmark.WithParserOptions(parser.WithAutoHeadingID()),
)

func main() {
	root := envOr("SHARE_ROOT", "/srv/share")
	listen := envOr("SHARE_LISTEN", ":8080")
	secret := os.Getenv("SHARE_SECRET")
	if len(secret) < 32 {
		log.Fatalf("SHARE_SECRET must be set (32+ chars); signed links depend on it")
	}
	absRoot, err := filepath.EvalSymlinks(root)
	if err != nil {
		log.Fatalf("share root %q: %v", root, err)
	}
	registerTextTypes()

	authPath := envOr("SHARE_AUTH_PATH", defaultAuthPath)
	if authPath == "" || authPath == linkPrefix || authPath == archivePath || authPath == logoutPath || keyPattern.MatchString(authPath) || strings.ContainsAny(authPath, "/?#") {
		log.Fatalf("SHARE_AUTH_PATH %q: must be a single short path segment", authPath)
	}
	sessionTTL := defaultSessionTTL
	if v := os.Getenv("SHARE_SESSION_TTL"); v != "" {
		if sessionTTL, err = time.ParseDuration(v); err != nil || sessionTTL <= 0 {
			log.Fatalf("SHARE_SESSION_TTL %q: %v", v, err)
		}
	}
	login, password := os.Getenv("SHARE_LOGIN"), os.Getenv("SHARE_PASSWORD")
	if (login == "") != (password == "") {
		log.Fatalf("SHARE_LOGIN and SHARE_PASSWORD must be set together")
	}
	if login != "" && len(password) < 12 {
		log.Fatalf("SHARE_PASSWORD must be at least 12 chars")
	}

	s := &server{
		root:   absRoot,
		secret: []byte(secret),
		tmpl:   template.Must(template.ParseFS(templateFS, "templates/*.html")),
		auth:   newAuth([]byte(secret), authPath, login, password, sessionTTL),
	}
	if s.auth.enabled() {
		log.Printf("share: dashboard enabled at /%s (sessions %s)", authPath, sessionTTL)
		s.archiveInactive(time.Now())
		go s.runArchiver()
	} else {
		log.Printf("share: dashboard disabled (SHARE_LOGIN/SHARE_PASSWORD unset)")
	}
	srv := &http.Server{
		Addr:              listen,
		Handler:           logRequests(s),
		ReadHeaderTimeout: 10 * time.Second,
	}
	log.Printf("share: serving %s on %s", absRoot, listen)
	log.Fatal(srv.ListenAndServe())
}

func envOr(name, fallback string) string {
	if v := os.Getenv(name); v != "" {
		return v
	}
	return fallback
}

// Go's builtin table only knows web types; sources with a known extension
// (e.g. .ts → video/mp2t from /etc/mime.types) would otherwise be forced to download.
func registerTextTypes() {
	plain := "text/plain; charset=utf-8"
	for _, ext := range []string{".md", ".markdown", ".txt", ".log", ".ts", ".tsx", ".py", ".go", ".sh", ".yml", ".yaml", ".toml", ".env", ".csv", ".sql"} {
		_ = mime.AddExtensionType(ext, plain)
	}
}

func (s *server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Cache-Control", "private, no-cache")
	reqPath := r.URL.Path
	if !strings.HasPrefix(reqPath, "/") {
		s.notFound(w, r)
		return
	}
	first, rest, hasSlash := strings.Cut(reqPath[1:], "/")

	// Owner-only pages live on short reserved names, which can never be share keys.
	if !hasSlash {
		switch first {
		case "":
			s.serveDashboard(w, r)
			return
		case s.auth.path:
			s.serveLogin(w, r)
			return
		case logoutPath:
			s.serveLogout(w, r)
			return
		}
	}
	if first == archivePath {
		s.serveArchive(w, r, rest, hasSlash)
		return
	}
	if first != linkPrefix && rest == feedbackSegment && s.feedbackEnabled(first) {
		s.handleFeedback(w, r, first)
		return
	}
	if (r.Method == http.MethodPost || r.Method == http.MethodPut) && first != linkPrefix {
		if after, ok := strings.CutPrefix(rest, uploadSegment+"/"); ok {
			token, name, _ := strings.Cut(after, "/")
			if uploadTokenPattern.MatchString(token) && !strings.Contains(name, "/") {
				s.handleTokenUpload(w, r, first, token, name)
				return
			}
		}
	}
	if (r.Method == http.MethodGet || r.Method == http.MethodHead) && first != linkPrefix {
		if token, ok := strings.CutPrefix(rest, uploadSegment+"/"); ok && uploadTokenPattern.MatchString(token) {
			s.handleTokenPipe(w, r, first, token)
			return
		}
	}
	canWrite := first != linkPrefix && s.auth.valid(r)
	if r.Method == http.MethodPost && canWrite {
		sc, ok := s.keyScope(first)
		if !ok {
			s.notFound(w, r)
			return
		}
		s.handleShareWrite(w, r, sc, rest)
		return
	}
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		w.Header().Set("Allow", "GET, HEAD")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}

	var sc scope
	var ok bool
	if first == linkPrefix {
		var token string
		token, rest, hasSlash = strings.Cut(rest, "/")
		sc, ok = s.linkScope(token)
	} else {
		sc, ok = s.keyScope(first)
		sc.canWrite = canWrite
	}
	if !ok {
		s.notFound(w, r)
		return
	}
	// A private share has no index for visitors: only a file's own random name opens it.
	if sc.private && !sc.canWrite && first != linkPrefix && (!hasSlash || !isPrivateFileName(rest)) {
		s.notFound(w, r)
		return
	}
	if r.Method == http.MethodGet {
		s.recordVisit(sc.dir, time.Now())
	}

	// A file-scoped link is addressed by the token alone; nothing may hang below it.
	fileScoped := sc.prefix != "" && !strings.HasSuffix(sc.prefix, "/")
	if fileScoped {
		if hasSlash {
			s.notFound(w, r)
			return
		}
		s.serveResolved(w, r, sc, sc.prefix, false)
		return
	}
	if !hasSlash {
		redirectSlash(w, r)
		return
	}
	s.serveResolved(w, r, sc, sc.prefix+rest, strings.HasSuffix(r.URL.Path, "/"))
}

func (s *server) keyScope(key string) (scope, bool) {
	if !keyPattern.MatchString(key) {
		return scope{}, false
	}
	dir := filepath.Join(s.root, key)
	if info, err := os.Lstat(dir); err != nil || !info.IsDir() {
		return scope{}, false
	}
	meta := loadFullMeta(dir)
	return scope{
		dir:     dir,
		baseURL: "/" + key + "/",
		title:   meta.Title,
		private: meta.Private,
	}, true
}

// linkScope validates "<payload>.<sig>" where payload is
// base64url("v1|<folder-hash>|<prefix>|<exp>") and sig is HMAC-SHA256 of the
// raw payload. The folder is found by hashing directory names, so the token
// never carries the key.
func (s *server) linkScope(token string) (scope, bool) {
	if !tokenPattern.MatchString(token) {
		return scope{}, false
	}
	payloadB64, sigB64, _ := strings.Cut(token, ".")
	sig, err := base64.RawURLEncoding.DecodeString(sigB64)
	if err != nil {
		return scope{}, false
	}
	mac := hmac.New(sha256.New, s.secret)
	mac.Write([]byte(payloadB64))
	if !hmac.Equal(sig, mac.Sum(nil)) {
		return scope{}, false
	}
	payload, err := base64.RawURLEncoding.DecodeString(payloadB64)
	if err != nil {
		return scope{}, false
	}
	parts := strings.Split(string(payload), "|")
	if len(parts) != 4 || parts[0] != tokenVersion {
		return scope{}, false
	}
	folderHash, prefix, expStr := parts[1], parts[2], parts[3]
	exp, err := strconv.ParseInt(expStr, 10, 64)
	if err != nil || time.Now().Unix() > exp {
		return scope{}, false
	}
	if prefix != "" && (hasHiddenSegment("/"+prefix) || path.Clean("/"+prefix) != "/"+strings.TrimSuffix(prefix, "/")) {
		return scope{}, false
	}
	dir, ok := s.findFolderByHash(folderHash)
	if !ok {
		return scope{}, false
	}
	meta := loadFullMeta(dir)
	return scope{
		dir:     dir,
		prefix:  prefix,
		baseURL: "/" + linkPrefix + "/" + token + "/",
		title:   meta.Title,
		private: meta.Private,
	}, true
}

func folderHash(name string) string {
	sum := sha256.Sum256([]byte(name))
	return hex.EncodeToString(sum[:])[:folderHashLen]
}

func (s *server) findFolderByHash(h string) (string, bool) {
	if len(h) != folderHashLen {
		return "", false
	}
	entries, err := os.ReadDir(s.root)
	if err != nil {
		return "", false
	}
	for _, e := range entries {
		if e.IsDir() && keyPattern.MatchString(e.Name()) && folderHash(e.Name()) == h {
			return filepath.Join(s.root, e.Name()), true
		}
	}
	return "", false
}

func (s *server) loadTitle(dir string) string {
	raw, err := os.ReadFile(filepath.Join(dir, metaFileName))
	if err != nil {
		return defaultTitle
	}
	var meta shareMeta
	if err := json.Unmarshal(raw, &meta); err != nil || meta.Title == "" {
		return defaultTitle
	}
	return meta.Title
}

func redirectSlash(w http.ResponseWriter, r *http.Request) {
	target := r.URL.Path + "/"
	if r.URL.RawQuery != "" {
		target += "?" + r.URL.RawQuery
	}
	http.Redirect(w, r, target, http.StatusFound)
}

// serveResolved serves rel (relative to the share folder) within the scope:
// files directly, folders via an index page or a listing.
func (s *server) serveResolved(w http.ResponseWriter, r *http.Request, sc scope, rel string, trailingSlash bool) {
	cleanRel := path.Clean("/" + rel)
	if hasHiddenSegment(cleanRel) || !withinPrefix(cleanRel, sc.prefix) {
		s.notFound(w, r)
		return
	}
	fsPath, err := s.resolveInside(sc.dir, cleanRel)
	if err != nil {
		s.notFound(w, r)
		return
	}
	info, err := os.Stat(fsPath)
	if err != nil {
		s.notFound(w, r)
		return
	}

	if !info.IsDir() {
		if trailingSlash {
			s.notFound(w, r)
			return
		}
		if sc.canWrite && r.URL.Query().Has(editParam) {
			s.serveEditor(w, r, sc, cleanRel, fsPath, info.Size())
			return
		}
		serveFile(w, r, fsPath, info)
		return
	}
	if sc.private && !sc.canWrite {
		s.notFound(w, r)
		return
	}
	if !trailingSlash {
		redirectSlash(w, r)
		return
	}
	// "?files" opens the folder view even where index.html would make it a site.
	indexPath, indexErr := s.resolveInside(fsPath, "/"+indexFile)
	var idx fs.FileInfo
	if indexErr == nil {
		idx, indexErr = os.Stat(indexPath)
	}
	hasIndex := indexErr == nil && idx.Mode().IsRegular()
	if hasIndex && !r.URL.Query().Has(filesParam) {
		serveFile(w, r, indexPath, idx)
		return
	}
	if !hasIndex {
		markdownPath, err := s.resolveInside(fsPath, "/"+markdownIndexFile)
		if err == nil {
			markdownInfo, statErr := os.Stat(markdownPath)
			if statErr == nil && markdownInfo.Mode().IsRegular() {
				hasIndex = true
				if !r.URL.Query().Has(filesParam) {
					s.serveMarkdownIndex(w, r, sc.title, markdownPath)
					return
				}
			}
		}
	}
	s.renderListing(w, r, sc, cleanRel, fsPath, hasIndex)
}

func (s *server) serveMarkdownIndex(w http.ResponseWriter, r *http.Request, title, fsPath string) {
	source, err := os.ReadFile(fsPath)
	if err != nil {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}
	var content bytes.Buffer
	if err := markdownRenderer.Convert(source, &content); err != nil {
		log.Printf("markdown %s: %v", maskPath(r.URL.Path), err)
		http.Error(w, "could not render Markdown", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if r.Method == http.MethodHead {
		return
	}
	if err := s.tmpl.ExecuteTemplate(w, "markdown.html", markdownData{Title: title, Content: template.HTML(content.String())}); err != nil {
		log.Printf("markdown page %s: %v", maskPath(r.URL.Path), err)
	}
}

// withinPrefix keeps signed-link requests inside the subtree they were issued for.
func withinPrefix(cleanRel, prefix string) bool {
	if prefix == "" {
		return true
	}
	p := path.Clean("/" + prefix)
	return cleanRel == p || strings.HasPrefix(cleanRel, p+"/")
}

func hasHiddenSegment(cleanRel string) bool {
	for _, seg := range strings.Split(cleanRel, "/") {
		if strings.HasPrefix(seg, ".") {
			return true
		}
	}
	return false
}

// resolveInside joins rel onto dir and refuses anything whose real location
// (after symlinks) escapes the share root.
func (s *server) resolveInside(dir, cleanRel string) (string, error) {
	candidate := filepath.Join(dir, filepath.FromSlash(cleanRel))
	real, err := filepath.EvalSymlinks(candidate)
	if err != nil {
		return "", err
	}
	relativeDir, err := filepath.Rel(s.root, dir)
	if err != nil || relativeDir == ".." || strings.HasPrefix(relativeDir, ".."+string(filepath.Separator)) {
		return "", errors.New("outside share root")
	}
	segments := strings.Split(relativeDir, string(filepath.Separator))
	shareRoot := filepath.Join(s.root, segments[0])
	if segments[0] == archiveDirName {
		if len(segments) < 2 {
			return "", errors.New("outside archived share")
		}
		shareRoot = filepath.Join(shareRoot, segments[1])
	}
	if real != shareRoot && !strings.HasPrefix(real, shareRoot+string(filepath.Separator)) {
		return "", errors.New("outside share")
	}
	return real, nil
}

// http.ServeFile is avoided on purpose: it redirects .../index.html to ./ and
// would serve a directory listing of its own. The filename header lets
// `curl -OJ` on a token-only link save the real name.
func serveFile(w http.ResponseWriter, r *http.Request, fsPath string, info fs.FileInfo) {
	f, err := os.Open(fsPath)
	if err != nil {
		http.Error(w, "not found", http.StatusNotFound)
		return
	}
	defer f.Close()
	// Both forms: curl only reads the plain filename=, browsers prefer filename*=.
	w.Header().Set("Content-Disposition", fmt.Sprintf("inline; filename=%q; filename*=UTF-8''%s",
		asciiName(info.Name()), url.PathEscape(info.Name())))
	http.ServeContent(w, r, info.Name(), info.ModTime(), f)
}

func asciiName(name string) string {
	return strings.Map(func(r rune) rune {
		if r < 0x20 || r > 0x7e || r == '"' || r == '\\' {
			return '_'
		}
		return r
	}, name)
}

func (s *server) renderListing(w http.ResponseWriter, r *http.Request, sc scope, cleanRel, fsPath string, hasIndex bool) {
	dirEntries, err := os.ReadDir(fsPath)
	if err != nil {
		s.notFound(w, r)
		return
	}
	entries := make([]listingEntry, 0, len(dirEntries))
	for _, de := range dirEntries {
		name := de.Name()
		if strings.HasPrefix(name, ".") {
			continue
		}
		// Resolve through symlinks so linked folders list as folders, and
		// anything pointing outside the root is omitted rather than 404ing later.
		real, err := s.resolveInside(fsPath, "/"+name)
		if err != nil {
			continue
		}
		info, err := os.Stat(real)
		if err != nil {
			continue
		}
		entry := listingEntry{
			Name:    name,
			Href:    url.PathEscape(name),
			IsDir:   info.IsDir(),
			ModTime: info.ModTime().UTC().Format("2006-01-02 15:04"),
		}
		if info.IsDir() {
			entry.Href += "/"
		} else {
			entry.Size = humanSize(info.Size())
			if sc.canWrite && isTextFile(name, info.Size()) {
				entry.Editable = true
				entry.EditHref = editHref(name)
			}
		}
		entries = append(entries, entry)
	}
	sort.SliceStable(entries, func(i, j int) bool {
		if entries[i].IsDir != entries[j].IsDir {
			return entries[i].IsDir
		}
		return strings.ToLower(entries[i].Name) < strings.ToLower(entries[j].Name)
	})

	// Crumbs start at the scope root, so a signed link never shows folders above it.
	scopeRoot := path.Clean("/" + sc.prefix)
	inScope := strings.TrimPrefix(strings.TrimPrefix(cleanRel, scopeRoot), "/")
	data := listingData{
		Title:     sc.title,
		Path:      inScope,
		Crumbs:    buildCrumbs(sc, inScope),
		RootURL:   sc.baseURL,
		Entries:   entries,
		HasIndex:  hasIndex,
		CanWrite:  sc.canWrite,
		IsArchive: strings.HasPrefix(sc.baseURL, "/"+archivePath+"/"),
		Private:   sc.private,
		FilesURL:  "?" + filesParam,
	}
	if sc.canWrite && inScope == "" {
		data.Key = strings.Trim(sc.baseURL, "/")
	}
	if r.URL.Query().Has(filesParam) {
		data.Suffix = "?" + filesParam
	}
	if inScope != "" {
		data.Parent = "../"
	}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	if err := s.tmpl.ExecuteTemplate(w, "listing.html", data); err != nil {
		log.Printf("listing %s: %v", maskPath(r.URL.Path), err)
	}
}

// The title in the header links to the scope root, so crumbs only cover the subpath.
func buildCrumbs(sc scope, inScope string) []crumb {
	var crumbs []crumb
	if inScope == "" {
		return crumbs
	}
	href := sc.baseURL
	for _, seg := range strings.Split(inScope, "/") {
		href += url.PathEscape(seg) + "/"
		crumbs = append(crumbs, crumb{Name: seg, Href: href})
	}
	return crumbs
}

func humanSize(n int64) string {
	const unit = 1024
	if n < unit {
		return fmt.Sprintf("%d B", n)
	}
	div, exp := int64(unit), 0
	for m := n / unit; m >= unit; m /= unit {
		div *= unit
		exp++
	}
	return fmt.Sprintf("%.1f %cB", float64(n)/float64(div), "KMGTPE"[exp])
}

func (s *server) notFound(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(http.StatusNotFound)
	if r.Method == http.MethodHead {
		return
	}
	if err := s.tmpl.ExecuteTemplate(w, "notfound.html", nil); err != nil {
		log.Printf("notfound page: %v", err)
	}
}

func maskPath(p string) string {
	return logMask.ReplaceAllString(p, "~")
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (rec *statusRecorder) WriteHeader(code int) {
	rec.status = code
	rec.ResponseWriter.WriteHeader(code)
}

// The key/token segment is masked, so the log stays useless to anyone who obtains it.
func logRequests(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(rec, r)
		log.Printf("%s %s %d", r.Method, maskPath(r.URL.Path), rec.status)
	})
}
