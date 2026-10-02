package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"os"
	"path/filepath"
	"time"
)

// Feedback lets a page served from a share send answers back without a session:
// POST /<key>/feedback appends one JSON line to <share>/.feedback.jsonl, and
// GET /<key>/feedback returns everything sent so far. The share opts in by having
// that file at all (`share feedback on <key>` creates it); without it the path is
// the usual 404. Dotfiles are never served, so the log is reachable only here.
const (
	feedbackSegment  = "feedback"
	feedbackFile     = ".feedback.jsonl"
	maxFeedbackBody  = 256 << 10
	maxFeedbackTotal = 20 << 20
)

var errFeedbackFull = errors.New("feedback log is full")

type feedbackEntry struct {
	At   string          `json:"at"`
	Data json.RawMessage `json:"data"`
}

func feedbackPath(dir string) (string, bool) {
	p := filepath.Join(dir, feedbackFile)
	info, err := os.Lstat(p)
	return p, err == nil && info.Mode().IsRegular()
}

func (s *server) feedbackEnabled(key string) bool {
	sc, ok := s.keyScope(key)
	if !ok {
		return false
	}
	_, enabled := feedbackPath(sc.dir)
	return enabled
}

func (s *server) handleFeedback(w http.ResponseWriter, r *http.Request, key string) {
	sc, ok := s.keyScope(key)
	if !ok {
		s.notFound(w, r)
		return
	}
	logPath, enabled := feedbackPath(sc.dir)
	if !enabled {
		s.notFound(w, r)
		return
	}
	switch r.Method {
	case http.MethodGet, http.MethodHead:
		s.serveFeedbackLog(w, r, logPath)
	case http.MethodPost:
		s.appendFeedback(w, r, sc, logPath)
	default:
		w.Header().Set("Allow", "GET, HEAD, POST")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
	}
}

func (s *server) serveFeedbackLog(w http.ResponseWriter, r *http.Request, logPath string) {
	s.feedbackMu.Lock()
	raw, err := os.ReadFile(logPath)
	s.feedbackMu.Unlock()
	if err != nil {
		log.Printf("feedback read %s: %v", maskPath(r.URL.Path), err)
		http.Error(w, "read failed", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/x-ndjson; charset=utf-8")
	if r.Method == http.MethodGet {
		w.Write(raw)
	}
}

func (s *server) appendFeedback(w http.ResponseWriter, r *http.Request, sc scope, logPath string) {
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxFeedbackBody))
	if err != nil {
		var tooBig *http.MaxBytesError
		if errors.As(err, &tooBig) {
			http.Error(w, "feedback too large", http.StatusRequestEntityTooLarge)
			return
		}
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}
	body = bytes.TrimSpace(body)
	if !json.Valid(body) || len(body) == 0 || body[0] != '{' {
		http.Error(w, "body must be a JSON object", http.StatusBadRequest)
		return
	}
	now := time.Now().UTC()
	var compact bytes.Buffer
	if err := json.Compact(&compact, body); err != nil {
		http.Error(w, "body must be a JSON object", http.StatusBadRequest)
		return
	}
	line, err := json.Marshal(feedbackEntry{At: now.Format(time.RFC3339), Data: compact.Bytes()})
	if err != nil {
		http.Error(w, "bad request", http.StatusBadRequest)
		return
	}
	if err := s.appendFeedbackLine(logPath, append(line, '\n')); err != nil {
		if errors.Is(err, errFeedbackFull) {
			http.Error(w, err.Error(), http.StatusInsufficientStorage)
			return
		}
		log.Printf("feedback write %s: %v", maskPath(r.URL.Path), err)
		http.Error(w, "write failed", http.StatusInternalServerError)
		return
	}
	s.recordVisit(sc.dir, now)
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(map[string]string{"at": now.Format(time.RFC3339)})
}

func (s *server) appendFeedbackLine(logPath string, line []byte) error {
	s.feedbackMu.Lock()
	defer s.feedbackMu.Unlock()
	info, err := os.Lstat(logPath)
	if err != nil {
		return err
	}
	if info.Size()+int64(len(line)) > maxFeedbackTotal {
		return errFeedbackFull
	}
	f, err := os.OpenFile(logPath, os.O_WRONLY|os.O_APPEND, 0)
	if err != nil {
		return err
	}
	if _, err := f.Write(line); err != nil {
		f.Close()
		return err
	}
	return f.Close()
}
