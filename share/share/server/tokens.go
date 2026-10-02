package main

import (
	"crypto/rand"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
)

// Upload tokens let a device or script drop files into one share without the dashboard
// session: POST/PUT /<key>/upload/<token>. Only the SHA-256 of a token is stored, in the
// share's own hidden .tokens.json, so tokens follow the folder through `share rotate`.

const (
	tokensFileName  = ".tokens.json"
	tokensFilePerm  = 0o600
	uploadTokenLen  = 32 // bytes → 43 base64url chars
	tokenIDBytes    = 4
	maxTokenLabel   = 100
	tokenKindAlways = "forever"
	tokenKindTTL    = "ttl"
	tokenKindOnce   = "once"
	defaultOnceTTL  = 24 * time.Hour
)

var (
	uploadTokenPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{32,128}$`)
	ttlPattern         = regexp.MustCompile(`^([0-9]{1,6})([mhd])$`)
	errTokenNotFound   = errors.New("token not found")
)

type uploadToken struct {
	ID       string `json:"id"`
	Label    string `json:"label,omitempty"`
	Hash     string `json:"hash"`
	Created  string `json:"created"`
	Expires  string `json:"expires,omitempty"`
	Once     bool   `json:"once,omitempty"`
	Used     string `json:"used,omitempty"`
	LastUsed string `json:"last_used,omitempty"`
}

type tokenFile struct {
	Tokens []uploadToken `json:"tokens"`
}

func hashToken(plain string) string {
	sum := sha256.Sum256([]byte(plain))
	return hex.EncodeToString(sum[:])
}

func parseTTL(s string) (time.Duration, error) {
	m := ttlPattern.FindStringSubmatch(strings.TrimSpace(s))
	if m == nil {
		return 0, fmt.Errorf("bad ttl %q (e.g. 30m, 12h, 7d)", s)
	}
	n, _ := strconv.Atoi(m[1])
	if n == 0 {
		return 0, fmt.Errorf("ttl must be positive")
	}
	unit := map[string]time.Duration{"m": time.Minute, "h": time.Hour, "d": 24 * time.Hour}[m[2]]
	return time.Duration(n) * unit, nil
}

func loadTokens(dir string) (tokenFile, error) {
	var tf tokenFile
	raw, err := os.ReadFile(filepath.Join(dir, tokensFileName))
	if errors.Is(err, os.ErrNotExist) {
		return tf, nil
	}
	if err != nil {
		return tf, err
	}
	if err := json.Unmarshal(raw, &tf); err != nil {
		return tf, fmt.Errorf("%s: %w", tokensFileName, err)
	}
	return tf, nil
}

func saveTokens(dir string, tf tokenFile) error {
	raw, err := json.MarshalIndent(tf, "", "  ")
	if err != nil {
		return err
	}
	if err := writeFileAtomic(dir, tokensFileName, strings.NewReader(string(raw)+"\n")); err != nil {
		return err
	}
	return os.Chmod(filepath.Join(dir, tokensFileName), tokensFilePerm)
}

func (t uploadToken) kind() string {
	switch {
	case t.Once:
		return tokenKindOnce
	case t.Expires != "":
		return tokenKindTTL
	default:
		return tokenKindAlways
	}
}

func (t uploadToken) expiresAt() (time.Time, bool) {
	if t.Expires == "" {
		return time.Time{}, false
	}
	exp, err := time.Parse(time.RFC3339, t.Expires)
	if err != nil {
		// An unreadable expiry must fail closed.
		return time.Time{}, true
	}
	return exp, true
}

// status is "active", "expired" or "used".
func (t uploadToken) status(now time.Time) string {
	if t.Once && t.Used != "" {
		return "used"
	}
	if exp, ok := t.expiresAt(); ok && !now.Before(exp) {
		return "expired"
	}
	return "active"
}

func newUploadToken() (string, error) {
	b := make([]byte, uploadTokenLen)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}

func newTokenID() (string, error) {
	b := make([]byte, tokenIDBytes)
	if _, err := rand.Read(b); err != nil {
		return "", err
	}
	return hex.EncodeToString(b), nil
}

// createToken returns the plain token; it is never stored and cannot be shown again.
func (s *server) createToken(dir, label, kind string, ttl time.Duration, now time.Time) (string, uploadToken, error) {
	plain, err := newUploadToken()
	if err != nil {
		return "", uploadToken{}, err
	}
	id, err := newTokenID()
	if err != nil {
		return "", uploadToken{}, err
	}
	tok := uploadToken{ID: id, Label: clip(label, maxTokenLabel), Hash: hashToken(plain), Created: now.UTC().Format(time.RFC3339)}
	switch kind {
	case tokenKindAlways:
	case tokenKindTTL:
		if ttl <= 0 {
			return "", uploadToken{}, errors.New("ttl required")
		}
		tok.Expires = now.Add(ttl).UTC().Format(time.RFC3339)
	case tokenKindOnce:
		tok.Once = true
		if ttl <= 0 {
			ttl = defaultOnceTTL
		}
		tok.Expires = now.Add(ttl).UTC().Format(time.RFC3339)
	default:
		return "", uploadToken{}, fmt.Errorf("unknown token kind %q", kind)
	}
	s.tokenMu.Lock()
	defer s.tokenMu.Unlock()
	tf, err := loadTokens(dir)
	if err != nil {
		return "", uploadToken{}, err
	}
	tf.Tokens = append(tf.Tokens, tok)
	if err := saveTokens(dir, tf); err != nil {
		return "", uploadToken{}, err
	}
	return plain, tok, nil
}

// useToken checks a presented token and records the use; a one-time token is burnt
// here, before the upload streams, so two parallel requests cannot both use it.
func (s *server) useToken(dir, plain string, now time.Time) (uploadToken, bool) {
	if !uploadTokenPattern.MatchString(plain) {
		return uploadToken{}, false
	}
	hash := hashToken(plain)
	s.tokenMu.Lock()
	defer s.tokenMu.Unlock()
	tf, err := loadTokens(dir)
	if err != nil {
		return uploadToken{}, false
	}
	for i, tok := range tf.Tokens {
		if tok.Hash != hash {
			continue
		}
		if tok.status(now) != "active" {
			return uploadToken{}, false
		}
		stamp := now.UTC().Format(time.RFC3339)
		tf.Tokens[i].LastUsed = stamp
		if tok.Once {
			tf.Tokens[i].Used = stamp
		}
		if err := saveTokens(dir, tf); err != nil {
			return uploadToken{}, false
		}
		return tf.Tokens[i], true
	}
	return uploadToken{}, false
}

// tokenActive checks a token without recording a use, so fetching the pipe script
// leaves a one-time token for the upload itself.
func (s *server) tokenActive(dir, plain string, now time.Time) bool {
	if !uploadTokenPattern.MatchString(plain) {
		return false
	}
	hash := hashToken(plain)
	s.tokenMu.Lock()
	defer s.tokenMu.Unlock()
	tf, err := loadTokens(dir)
	if err != nil {
		return false
	}
	for _, tok := range tf.Tokens {
		if tok.Hash == hash {
			return tok.status(now) == "active"
		}
	}
	return false
}

func (s *server) revokeToken(dir, id string) error {
	s.tokenMu.Lock()
	defer s.tokenMu.Unlock()
	tf, err := loadTokens(dir)
	if err != nil {
		return err
	}
	kept := tf.Tokens[:0]
	found := false
	for _, tok := range tf.Tokens {
		if tok.ID == id {
			found = true
			continue
		}
		kept = append(kept, tok)
	}
	if !found {
		return errTokenNotFound
	}
	tf.Tokens = kept
	return saveTokens(dir, tf)
}

// hasLiveToken marks a share as an upload inbox, which must not be archived for idleness.
func hasLiveToken(dir string, now time.Time) bool {
	tf, err := loadTokens(dir)
	if err != nil {
		return false
	}
	for _, tok := range tf.Tokens {
		if tok.status(now) == "active" {
			return true
		}
	}
	return false
}
