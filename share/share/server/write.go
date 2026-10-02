package main

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"log"
	"mime"
	"net/http"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"strings"
	"time"
)

// Browser-side writes. Every handler here runs only with a valid session cookie and a
// same-site POST; without the cookie the same URLs answer exactly as before, so the
// public HTML carries no trace of these features.

const (
	editParam        = "edit"
	keyBytes         = 24 // → 32 base64url chars, same as the CLI
	maxUploadBytes   = 2 << 30
	maxTextBytes     = 10 << 20
	maxEditBytes     = 2 << 20
	maxNameLen       = 255
	maxTitleLen      = 200
	maxDescLen       = 1000
	uploadTempPrefix = ".upload-"
	filePerm         = 0o644
	dirPerm          = 0o755
)

var textExtensions = map[string]bool{
	".txt": true, ".md": true, ".markdown": true, ".html": true, ".htm": true, ".css": true,
	".js": true, ".mjs": true, ".ts": true, ".tsx": true, ".jsx": true, ".json": true,
	".yml": true, ".yaml": true, ".toml": true, ".xml": true, ".svg": true, ".csv": true,
	".sh": true, ".py": true, ".go": true, ".log": true, ".env": true, ".sql": true, ".ini": true,
}

type editorData struct {
	Title     string
	Name      string
	FolderURL string
	Content   string
}

var (
	errBadName = errors.New("bad name")
	errExists  = errors.New("name already taken")
)

// cleanName accepts a single plain file/folder name: no separators, no dotfiles
// (those are invisible to the host anyway), no control characters.
func cleanName(name string) (string, error) {
	name = strings.TrimSpace(name)
	if name == "" || len(name) > maxNameLen || name != path.Base(name) || strings.HasPrefix(name, ".") ||
		strings.ContainsAny(name, "/\\") {
		return "", errBadName
	}
	for _, r := range name {
		if r < 0x20 || r == 0x7f {
			return "", errBadName
		}
	}
	return name, nil
}

func newKey() (string, error) {
	b := make([]byte, keyBytes)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}

func isTextFile(name string, size int64) bool {
	if size > maxEditBytes {
		return false
	}
	ext := strings.ToLower(filepath.Ext(name))
	if textExtensions[ext] {
		return true
	}
	return strings.HasPrefix(mime.TypeByExtension(ext), "text/")
}

func clip(s string, max int) string {
	s = strings.TrimSpace(s)
	if len(s) > max {
		return s[:max]
	}
	return s
}

func (s *server) writeMeta(dir string, meta fullMeta) error {
	raw, err := json.MarshalIndent(meta, "", "  ")
	if err != nil {
		return err
	}
	return writeFileAtomic(dir, metaFileName, strings.NewReader(string(raw)+"\n"))
}

// writeFileAtomic streams into a hidden temp file in the same folder and renames it,
// so a half-uploaded file never shows up in a listing.
func writeFileAtomic(dir, name string, src io.Reader) error {
	tmp, err := os.CreateTemp(dir, uploadTempPrefix+"*")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	if _, err := io.Copy(tmp, src); err != nil {
		tmp.Close()
		os.Remove(tmpName)
		return err
	}
	if err := tmp.Close(); err != nil {
		os.Remove(tmpName)
		return err
	}
	if err := os.Chmod(tmpName, filePerm); err != nil {
		os.Remove(tmpName)
		return err
	}
	if err := os.Rename(tmpName, filepath.Join(dir, name)); err != nil {
		os.Remove(tmpName)
		return err
	}
	return nil
}

// handleDashboardPost creates or deletes whole shares from the dashboard form.
func (s *server) handleDashboardPost(w http.ResponseWriter, r *http.Request) {
	if !sameSite(r) {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, maxUploadBytes)
	switch r.URL.Query().Get("action") {
	case "create":
		s.createShare(w, r)
	case "edit":
		s.editShareMeta(w, r)
	case "archive", "restore":
		if err := r.ParseForm(); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		key := r.PostForm.Get("key")
		var err error
		if r.URL.Query().Get("action") == "archive" {
			err = s.archiveShare(key)
		} else {
			err = s.restoreShare(key, time.Now())
		}
		if err != nil {
			message, status := archiveActionError(err)
			if status == http.StatusInternalServerError {
				log.Printf("archive action: %v", err)
			}
			http.Error(w, message, status)
			return
		}
		back := "/"
		if r.URL.Query().Get("action") == "restore" {
			back = "/" + archivePath
		}
		http.Redirect(w, r, back, http.StatusSeeOther)
	case "token-create":
		s.createTokenFromDashboard(w, r)
	case "token-revoke":
		if err := r.ParseForm(); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		sc, ok := s.keyScope(r.PostForm.Get("key"))
		if !ok {
			s.notFound(w, r)
			return
		}
		if err := s.revokeToken(sc.dir, r.PostForm.Get("id")); err != nil && !errors.Is(err, errTokenNotFound) {
			log.Printf("revoke token: %v", err)
			http.Error(w, "revoke failed", http.StatusInternalServerError)
			return
		}
		http.Redirect(w, r, "/", http.StatusSeeOther)
	case "delete":
		if err := r.ParseForm(); err != nil {
			http.Error(w, "bad request", http.StatusBadRequest)
			return
		}
		key := r.PostForm.Get("key")
		if sc, ok := s.keyScope(key); ok {
			if err := os.RemoveAll(sc.dir); err != nil {
				log.Printf("delete share: %v", err)
				http.Error(w, "delete failed", http.StatusInternalServerError)
				return
			}
			log.Printf("share deleted via dashboard")
		}
		http.Redirect(w, r, "/", http.StatusSeeOther)
	default:
		http.Error(w, "unknown action", http.StatusBadRequest)
	}
}

// editShareMeta rewrites title/description; "back" lets the file manager return to itself.
func (s *server) editShareMeta(w http.ResponseWriter, r *http.Request) {
	if err := r.ParseForm(); err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}
	sc, ok := s.keyScope(r.PostForm.Get("key"))
	if !ok {
		s.notFound(w, r)
		return
	}
	meta := loadFullMeta(sc.dir)
	title := clip(r.PostForm.Get("title"), maxTitleLen)
	if title == "" {
		title = defaultTitle
	}
	meta.Title = title
	if _, ok := r.PostForm["description"]; ok {
		meta.Description = clip(r.PostForm.Get("description"), maxDescLen)
	}
	// Only the dashboard form carries the checkbox; the file manager's rename must not reset it.
	if r.PostForm.Get("privacy") != "" {
		meta.Private = r.PostForm.Get("private") != ""
	}
	if meta.Created == "" {
		meta.Created = time.Now().UTC().Format(time.RFC3339)
	}
	if err := s.writeMeta(sc.dir, meta); err != nil {
		log.Printf("edit meta: %v", err)
		http.Error(w, "write failed", http.StatusInternalServerError)
		return
	}
	back := r.PostForm.Get("back")
	if !strings.HasPrefix(back, "/") || strings.HasPrefix(back, "//") {
		back = "/"
	}
	http.Redirect(w, r, back, http.StatusSeeOther)
}

func (s *server) createShare(w http.ResponseWriter, r *http.Request) {
	mr, err := r.MultipartReader()
	if err != nil {
		http.Error(w, "multipart form expected", http.StatusBadRequest)
		return
	}
	key, err := newKey()
	if err != nil {
		http.Error(w, "internal error", http.StatusInternalServerError)
		return
	}
	dir := filepath.Join(s.root, key)
	if err := os.Mkdir(dir, dirPerm); err != nil {
		log.Printf("create share: %v", err)
		http.Error(w, "create failed", http.StatusInternalServerError)
		return
	}
	// The form puts "private" before "files", so file names are decided knowing the mode.
	meta := fullMeta{Created: time.Now().UTC().Format(time.RFC3339)}
	for {
		part, err := mr.NextPart()
		if err == io.EOF {
			break
		}
		if err != nil {
			os.RemoveAll(dir)
			http.Error(w, "bad upload", http.StatusBadRequest)
			return
		}
		switch part.FormName() {
		case "title":
			meta.Title = readField(part, maxTitleLen)
		case "description":
			meta.Description = readField(part, maxDescLen)
		case "private":
			meta.Private = readField(part, maxNameLen) != ""
		case "files":
			if part.FileName() == "" {
				continue
			}
			if _, _, err := storeUpload(scope{dir: dir, private: meta.Private}, part.FileName(), part); err != nil {
				log.Printf("create share upload: %v", err)
			}
		}
	}
	if meta.Title == "" {
		meta.Title = defaultTitle
	}
	if err := s.writeMeta(dir, meta); err != nil {
		log.Printf("create share meta: %v", err)
	}
	log.Printf("share created via dashboard")
	http.Redirect(w, r, "/"+key+"/?"+filesParam, http.StatusSeeOther)
}

func readField(r io.Reader, max int) string {
	b, _ := io.ReadAll(io.LimitReader(r, int64(max)+1))
	return clip(string(b), max)
}

// handleShareWrite serves POST /<key>/<folder>/?upload|text|mkdir|delete.
func (s *server) handleShareWrite(w http.ResponseWriter, r *http.Request, sc scope, rel string) {
	if !sameSite(r) {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	cleanRel := path.Clean("/" + rel)
	if hasHiddenSegment(cleanRel) {
		s.notFound(w, r)
		return
	}
	dir, err := s.resolveInside(sc.dir, cleanRel)
	if err != nil {
		s.notFound(w, r)
		return
	}
	if info, err := os.Stat(dir); err != nil || !info.IsDir() {
		s.notFound(w, r)
		return
	}
	back := sc.baseURL + strings.TrimPrefix(cleanRel, "/")
	if !strings.HasSuffix(back, "/") {
		back += "/"
	}
	back += "?" + filesParam

	q := r.URL.Query()
	var werr error
	switch {
	case q.Has("upload"):
		r.Body = http.MaxBytesReader(w, r.Body, maxUploadBytes)
		werr = s.uploadFiles(r, scope{dir: dir, private: sc.private})
	case sc.private && (q.Has("text") || q.Has("mkdir") || q.Has("rename")):
		werr = errPrivateShare
	case q.Has("text"):
		r.Body = http.MaxBytesReader(w, r.Body, maxTextBytes)
		werr = s.saveText(r, dir)
	case q.Has("mkdir"):
		r.Body = http.MaxBytesReader(w, r.Body, loginBodyLimit)
		werr = s.makeFolder(r, dir)
	case q.Has("delete"):
		r.Body = http.MaxBytesReader(w, r.Body, loginBodyLimit)
		werr = s.deleteEntry(r, dir)
	case q.Has("rename"):
		r.Body = http.MaxBytesReader(w, r.Body, loginBodyLimit)
		werr = s.renameEntry(r, dir)
	default:
		http.Error(w, "unknown action", http.StatusBadRequest)
		return
	}
	if werr != nil {
		log.Printf("write %s: %v", maskPath(r.URL.Path), werr)
		if errors.Is(werr, errBadName) {
			http.Error(w, "bad name", http.StatusBadRequest)
		} else if errors.Is(werr, errPrivateShare) {
			http.Error(w, werr.Error(), http.StatusBadRequest)
		} else if errors.Is(werr, errExists) {
			http.Error(w, "a file with that name already exists", http.StatusConflict)
		} else {
			http.Error(w, "write failed", http.StatusInternalServerError)
		}
		return
	}
	http.Redirect(w, r, back, http.StatusSeeOther)
}

func (s *server) uploadFiles(r *http.Request, target scope) error {
	mr, err := r.MultipartReader()
	if err != nil {
		return err
	}
	for {
		part, err := mr.NextPart()
		if err == io.EOF {
			return nil
		}
		if err != nil {
			return err
		}
		if part.FormName() != "files" {
			continue
		}
		if target.private {
			if _, _, err := storeUpload(target, part.FileName(), part); err != nil {
				return err
			}
			continue
		}
		name, err := cleanName(part.FileName())
		if err != nil {
			return err
		}
		if err := writeFileAtomic(target.dir, name, part); err != nil {
			return err
		}
	}
}

func (s *server) saveText(r *http.Request, dir string) error {
	if err := r.ParseForm(); err != nil {
		return err
	}
	name, err := cleanName(r.PostForm.Get("name"))
	if err != nil {
		return err
	}
	// Textareas submit CRLF; files on disk should not inherit that.
	content := strings.ReplaceAll(r.PostForm.Get("content"), "\r\n", "\n")
	return writeFileAtomic(dir, name, strings.NewReader(content))
}

func (s *server) makeFolder(r *http.Request, dir string) error {
	if err := r.ParseForm(); err != nil {
		return err
	}
	name, err := cleanName(r.PostForm.Get("name"))
	if err != nil {
		return err
	}
	return os.MkdirAll(filepath.Join(dir, name), dirPerm)
}

func (s *server) deleteEntry(r *http.Request, dir string) error {
	if err := r.ParseForm(); err != nil {
		return err
	}
	name, err := cleanName(r.PostForm.Get("name"))
	if err != nil {
		return err
	}
	target := filepath.Join(dir, name)
	if _, err := os.Lstat(target); err != nil {
		return err
	}
	return os.RemoveAll(target)
}

func (s *server) renameEntry(r *http.Request, dir string) error {
	if err := r.ParseForm(); err != nil {
		return err
	}
	oldName, err := cleanName(r.PostForm.Get("name"))
	if err != nil {
		return err
	}
	newName, err := cleanName(r.PostForm.Get("newname"))
	if err != nil {
		return err
	}
	if oldName == newName {
		return nil
	}
	if _, err := os.Lstat(filepath.Join(dir, oldName)); err != nil {
		return err
	}
	// os.Rename would silently replace an existing file.
	if _, err := os.Lstat(filepath.Join(dir, newName)); err == nil {
		return errExists
	}
	return os.Rename(filepath.Join(dir, oldName), filepath.Join(dir, newName))
}

// serveEditor renders a text file in a textarea; the form posts back to the folder's ?text.
func (s *server) serveEditor(w http.ResponseWriter, r *http.Request, sc scope, cleanRel, fsPath string, size int64) {
	name := path.Base(cleanRel)
	if !isTextFile(name, size) {
		http.Error(w, "not an editable text file", http.StatusBadRequest)
		return
	}
	raw, err := os.ReadFile(fsPath)
	if err != nil {
		s.notFound(w, r)
		return
	}
	folder := sc.baseURL + strings.TrimPrefix(path.Dir(cleanRel), "/")
	if !strings.HasSuffix(folder, "/") {
		folder += "/"
	}
	data := editorData{Title: sc.title, Name: name, FolderURL: folder, Content: string(raw)}
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	if err := s.tmpl.ExecuteTemplate(w, "editor.html", data); err != nil {
		log.Printf("editor: %v", err)
	}
}

func editHref(name string) string {
	return url.PathEscape(name) + "?" + editParam
}
