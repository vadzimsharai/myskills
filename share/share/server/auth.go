package main

import (
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	sessionCookie     = "share_session"
	sessionVersion    = "s1"
	defaultAuthPath   = "owner"
	logoutPath        = "logout"
	defaultSessionTTL = 30 * 24 * time.Hour
	loginBodyLimit    = 4096

	maxLoginFailures = 5
	lockoutBase      = 15 * time.Minute
	lockoutMax       = 24 * time.Hour
	failureDelay     = time.Second
	globalFailureCap = 100
	globalWindow     = 15 * time.Minute
)

// auth is the single-account login that unlocks the dashboard at "/". Sessions are
// stateless HMAC tokens; the key is derived from the credentials, so changing the
// password in .env invalidates every cookie ever issued.
type auth struct {
	path       string
	login      []byte
	password   []byte
	sessionKey []byte
	ttl        time.Duration
	limiter    *loginLimiter
}

func newAuth(secret []byte, path, login, password string, ttl time.Duration) *auth {
	mac := hmac.New(sha256.New, secret)
	mac.Write([]byte("session|" + login + "|" + password))
	return &auth{
		path:       path,
		login:      []byte(login),
		password:   []byte(password),
		sessionKey: mac.Sum(nil),
		ttl:        ttl,
		limiter:    newLoginLimiter(),
	}
}

func (a *auth) enabled() bool {
	return len(a.login) > 0 && len(a.password) > 0
}

// check compares both fields unconditionally so timing does not tell a wrong login
// from a wrong password.
func (a *auth) check(login, password string) bool {
	loginOK := subtle.ConstantTimeCompare([]byte(login), a.login)
	passOK := subtle.ConstantTimeCompare([]byte(password), a.password)
	return loginOK&passOK == 1
}

func (a *auth) sign(payload string) string {
	mac := hmac.New(sha256.New, a.sessionKey)
	mac.Write([]byte(payload))
	return base64.RawURLEncoding.EncodeToString(mac.Sum(nil))
}

func (a *auth) issue() (string, error) {
	nonce := make([]byte, 16)
	if _, err := rand.Read(nonce); err != nil {
		return "", err
	}
	exp := time.Now().Add(a.ttl).Unix()
	payload := base64.RawURLEncoding.EncodeToString([]byte(fmt.Sprintf("%s|%d|%x", sessionVersion, exp, nonce)))
	return payload + "." + a.sign(payload), nil
}

func (a *auth) valid(r *http.Request) bool {
	if !a.enabled() {
		return false
	}
	c, err := r.Cookie(sessionCookie)
	if err != nil || !tokenPattern.MatchString(c.Value) {
		return false
	}
	payloadB64, sigB64, _ := strings.Cut(c.Value, ".")
	if subtle.ConstantTimeCompare([]byte(sigB64), []byte(a.sign(payloadB64))) != 1 {
		return false
	}
	payload, err := base64.RawURLEncoding.DecodeString(payloadB64)
	if err != nil {
		return false
	}
	parts := strings.Split(string(payload), "|")
	if len(parts) != 3 || parts[0] != sessionVersion {
		return false
	}
	exp, err := strconv.ParseInt(parts[1], 10, 64)
	return err == nil && time.Now().Unix() <= exp
}

func (a *auth) setCookie(w http.ResponseWriter, token string) {
	http.SetCookie(w, &http.Cookie{
		Name:     sessionCookie,
		Value:    token,
		Path:     "/",
		MaxAge:   int(a.ttl.Seconds()),
		HttpOnly: true,
		Secure:   true,
		SameSite: http.SameSiteStrictMode,
	})
}

func (a *auth) clearCookie(w http.ResponseWriter) {
	http.SetCookie(w, &http.Cookie{
		Name:     sessionCookie,
		Value:    "",
		Path:     "/",
		MaxAge:   -1,
		HttpOnly: true,
		Secure:   true,
		SameSite: http.SameSiteStrictMode,
	})
}

// sameSite rejects cross-site POSTs (CSRF) using the browser's own fetch metadata;
// requests without either header (curl) are allowed through.
func sameSite(r *http.Request) bool {
	switch r.Header.Get("Sec-Fetch-Site") {
	case "", "same-origin", "none":
	default:
		return false
	}
	if origin := r.Header.Get("Origin"); origin != "" && origin != "null" {
		u, err := url.Parse(origin)
		if err != nil || !strings.EqualFold(u.Host, r.Host) {
			return false
		}
	}
	return true
}

// Only Caddy reaches this service, and it appends the real client IP last.
func clientIP(r *http.Request) string {
	if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
		parts := strings.Split(xff, ",")
		return strings.TrimSpace(parts[len(parts)-1])
	}
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return host
}

type ipState struct {
	failures    int
	lockedUntil time.Time
	last        time.Time
}

// loginLimiter locks an IP out for a growing window after repeated failures and
// stops accepting logins from anyone once failures pile up globally.
type loginLimiter struct {
	mu             sync.Mutex
	perIP          map[string]*ipState
	globalFailures int
	globalSince    time.Time
}

func newLoginLimiter() *loginLimiter {
	return &loginLimiter{perIP: map[string]*ipState{}}
}

func (l *loginLimiter) allow(ip string) (time.Duration, bool) {
	l.mu.Lock()
	defer l.mu.Unlock()
	now := time.Now()
	if now.Sub(l.globalSince) > globalWindow {
		l.globalFailures, l.globalSince = 0, now
	}
	if l.globalFailures >= globalFailureCap {
		return globalWindow - now.Sub(l.globalSince), false
	}
	if st, ok := l.perIP[ip]; ok && now.Before(st.lockedUntil) {
		return st.lockedUntil.Sub(now), false
	}
	return 0, true
}

func (l *loginLimiter) fail(ip string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	now := time.Now()
	l.globalFailures++
	st := l.perIP[ip]
	if st == nil {
		st = &ipState{}
		l.perIP[ip] = st
	}
	st.failures++
	st.last = now
	if st.failures >= maxLoginFailures {
		lock := lockoutBase << uint(st.failures-maxLoginFailures)
		if lock > lockoutMax || lock <= 0 {
			lock = lockoutMax
		}
		st.lockedUntil = now.Add(lock)
	}
	if len(l.perIP) > 4096 {
		for k, v := range l.perIP {
			if now.Sub(v.last) > lockoutMax {
				delete(l.perIP, k)
			}
		}
	}
}

func (l *loginLimiter) reset(ip string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	delete(l.perIP, ip)
}
