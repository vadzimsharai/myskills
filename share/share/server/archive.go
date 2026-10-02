package main

import (
	"errors"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"
)

const (
	archiveDirName = ".archive"
	lastVisitFile  = ".last-visit"
	archiveAfter   = 7 * 24 * time.Hour
	archiveCheck   = time.Hour
)

func (s *server) archiveRoot() string {
	return filepath.Join(s.root, archiveDirName)
}

func (s *server) archiveScope(key string) (scope, bool) {
	if !keyPattern.MatchString(key) {
		return scope{}, false
	}
	dir := filepath.Join(s.archiveRoot(), key)
	if info, err := os.Lstat(dir); err != nil || !info.IsDir() {
		return scope{}, false
	}
	return scope{dir: dir, baseURL: "/" + archivePath + "/" + key + "/", title: s.loadTitle(dir)}, true
}

func (s *server) serveArchive(w http.ResponseWriter, r *http.Request, rest string, hasSlash bool) {
	if !s.auth.valid(r) {
		s.notFound(w, r)
		return
	}
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		w.Header().Set("Allow", "GET, HEAD")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	if !hasSlash || rest == "" {
		s.renderDashboard(w, r, true)
		return
	}
	key, rel, hasKeySlash := strings.Cut(rest, "/")
	sc, ok := s.archiveScope(key)
	if !ok {
		s.notFound(w, r)
		return
	}
	if !hasKeySlash {
		redirectSlash(w, r)
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Frame-Options", "DENY")
	w.Header().Set("Content-Security-Policy", "default-src 'self'; script-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; form-action 'none'; frame-ancestors 'none'")
	s.serveResolved(w, r, sc, rel, strings.HasSuffix(r.URL.Path, "/"))
}

func (s *server) ensureArchiveRoot() error {
	if err := os.MkdirAll(s.archiveRoot(), 0700); err != nil {
		return err
	}
	info, err := os.Lstat(s.archiveRoot())
	if err != nil {
		return err
	}
	if !info.IsDir() {
		return errors.New("archive root is not a directory")
	}
	return nil
}

func (s *server) archiveShare(key string) error {
	if !keyPattern.MatchString(key) {
		return os.ErrNotExist
	}
	s.archiveMu.Lock()
	defer s.archiveMu.Unlock()
	return s.archiveShareLocked(key)
}

func (s *server) archiveShareLocked(key string) error {
	source := filepath.Join(s.root, key)
	if info, err := os.Lstat(source); err != nil || !info.IsDir() {
		return os.ErrNotExist
	}
	if err := s.ensureArchiveRoot(); err != nil {
		return err
	}
	target := filepath.Join(s.archiveRoot(), key)
	if _, err := os.Lstat(target); err == nil {
		return errExists
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	return os.Rename(source, target)
}

func (s *server) restoreShare(key string, now time.Time) error {
	if !keyPattern.MatchString(key) {
		return os.ErrNotExist
	}
	s.archiveMu.Lock()
	defer s.archiveMu.Unlock()
	source := filepath.Join(s.archiveRoot(), key)
	if info, err := os.Lstat(source); err != nil || !info.IsDir() {
		return os.ErrNotExist
	}
	target := filepath.Join(s.root, key)
	if _, err := os.Lstat(target); err == nil {
		return errExists
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	if err := touchVisit(source, now); err != nil {
		return err
	}
	return os.Rename(source, target)
}

func touchVisit(dir string, now time.Time) error {
	marker := filepath.Join(dir, lastVisitFile)
	file, err := os.OpenFile(marker, os.O_CREATE|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	if err := file.Close(); err != nil {
		return err
	}
	return os.Chtimes(marker, now, now)
}

func (s *server) recordVisit(dir string, now time.Time) {
	s.archiveMu.Lock()
	defer s.archiveMu.Unlock()
	if err := touchVisit(dir, now); err != nil {
		log.Printf("share visit: %v", err)
	}
}

func (s *server) archiveInactive(now time.Time) {
	entries, err := os.ReadDir(s.root)
	if err != nil {
		log.Printf("archive scan: %v", err)
		return
	}
	for _, entry := range entries {
		if !entry.IsDir() || !keyPattern.MatchString(entry.Name()) {
			continue
		}
		if err := s.archiveIfInactive(entry.Name(), now); err != nil {
			log.Printf("archive %s: %v", maskPath("/"+entry.Name()), err)
		}
	}
}

func (s *server) archiveIfInactive(key string, now time.Time) error {
	s.archiveMu.Lock()
	defer s.archiveMu.Unlock()
	dir := filepath.Join(s.root, key)
	if info, err := os.Lstat(dir); err != nil || !info.IsDir() {
		return nil
	}
	info, err := os.Stat(filepath.Join(dir, lastVisitFile))
	if errors.Is(err, os.ErrNotExist) {
		return touchVisit(dir, now)
	}
	if err != nil {
		return err
	}
	if info.ModTime().After(now.Add(-archiveAfter)) || hasLiveToken(dir, now) {
		return nil
	}
	if err := s.archiveShareLocked(key); err != nil {
		return err
	}
	log.Printf("share archived after inactivity: %s", maskPath("/"+key))
	return nil
}

func (s *server) runArchiver() {
	ticker := time.NewTicker(archiveCheck)
	defer ticker.Stop()
	for now := range ticker.C {
		s.archiveInactive(now)
	}
}

func archiveActionError(err error) (string, int) {
	switch {
	case errors.Is(err, os.ErrNotExist):
		return "not found", http.StatusNotFound
	case errors.Is(err, errExists):
		return "share already exists", http.StatusConflict
	default:
		return "archive operation failed", http.StatusInternalServerError
	}
}
