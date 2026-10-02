package main

import (
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"mime"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

const (
	uploadSegment    = "upload"
	plainParam       = "plain"
	nameParam        = "name"
	filenameHeader   = "X-Filename"
	fallbackBaseName = "upload"
	maxDedupeTries   = 1000

	pipeURLPlaceholder = "@UPLOAD_URL@"
)

//go:embed templates/pipe.sh
var pipeScript string

var (
	// A private share serves only names that are themselves unguessable keys.
	privateNamePattern = regexp.MustCompile(`^[A-Za-z0-9_-]{20,128}(\.[A-Za-z0-9]{1,16})?$`)
	extPattern         = regexp.MustCompile(`^\.[a-z0-9]{1,16}$`)
	errNoFiles         = errors.New("no files in request")
	errPrivateShare    = errors.New("not available in a private share")
)

// Common types first: mime.ExtensionsByType returns them in arbitrary order.
var preferredExt = map[string]string{
	"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp",
	"application/pdf": ".pdf", "text/plain": ".txt", "application/json": ".json",
	"video/mp4": ".mp4", "application/zip": ".zip",
}

type uploadedFile struct {
	Name string `json:"name"`
	URL  string `json:"url"`
	Size int64  `json:"size"`
}

func isPrivateFileName(name string) bool {
	return privateNamePattern.MatchString(name)
}

func safeExt(name string) string {
	ext := strings.ToLower(filepath.Ext(name))
	if extPattern.MatchString(ext) {
		return ext
	}
	return ""
}

func extForType(contentType string) string {
	mediaType, _, err := mime.ParseMediaType(contentType)
	if err != nil {
		return ""
	}
	if ext, ok := preferredExt[mediaType]; ok {
		return ext
	}
	if exts, _ := mime.ExtensionsByType(mediaType); len(exts) > 0 {
		return safeExt("x" + exts[0])
	}
	return ""
}

func randomFileName(original string) (string, error) {
	key, err := newKey()
	if err != nil {
		return "", err
	}
	return key + safeExt(original), nil
}

// uniqueName appends -1, -2, … so a token upload never replaces an existing file.
func uniqueName(dir, name string) (string, error) {
	if _, err := os.Lstat(filepath.Join(dir, name)); errors.Is(err, os.ErrNotExist) {
		return name, nil
	}
	ext := filepath.Ext(name)
	stem := strings.TrimSuffix(name, ext)
	for i := 1; i <= maxDedupeTries; i++ {
		candidate := fmt.Sprintf("%s-%d%s", stem, i, ext)
		if _, err := os.Lstat(filepath.Join(dir, candidate)); errors.Is(err, os.ErrNotExist) {
			return candidate, nil
		}
	}
	return "", errExists
}

// storeUpload writes one file into the share root and returns the stored name.
func storeUpload(sc scope, original string, src io.Reader) (string, int64, error) {
	var name string
	var err error
	if sc.private {
		name, err = randomFileName(original)
	} else {
		if name, err = cleanName(original); err == nil {
			name, err = uniqueName(sc.dir, name)
		}
	}
	if err != nil {
		return "", 0, err
	}
	counter := &countingReader{r: src}
	if err := writeFileAtomic(sc.dir, name, counter); err != nil {
		return "", 0, err
	}
	return name, counter.n, nil
}

type countingReader struct {
	r io.Reader
	n int64
}

func (c *countingReader) Read(p []byte) (int, error) {
	n, err := c.r.Read(p)
	c.n += int64(n)
	return n, err
}

func publicBase(r *http.Request) string {
	if base := os.Getenv("SHARE_PUBLIC_URL"); base != "" {
		return strings.TrimSuffix(base, "/")
	}
	scheme := "https"
	if proto := r.Header.Get("X-Forwarded-Proto"); proto != "" {
		scheme = proto
	} else if r.TLS == nil {
		scheme = "http"
	}
	return scheme + "://" + r.Host
}

// handleTokenPipe serves GET /<key>/upload/<token>: a shell script that uploads its stdin
// back to the same URL, for `cmd | bash <(curl -sL <upload URL>)`.
func (s *server) handleTokenPipe(w http.ResponseWriter, r *http.Request, key, token string) {
	sc, ok := s.keyScope(key)
	if !ok || !s.tokenActive(sc.dir, token, time.Now()) {
		s.notFound(w, r)
		return
	}
	uploadURL := publicBase(r) + "/" + key + "/" + uploadSegment + "/" + token
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	io.WriteString(w, strings.ReplaceAll(pipeScript, pipeURLPlaceholder, uploadURL))
}

// handleTokenUpload serves POST|PUT /<key>/upload/<token>[/<name>]. The body is either
// multipart (every file part is stored) or the raw file, named by the path, ?name=,
// X-Filename or its Content-Type. Anything wrong with the key or token is the usual 404.
func (s *server) handleTokenUpload(w http.ResponseWriter, r *http.Request, key, token, pathName string) {
	sc, ok := s.keyScope(key)
	if !ok {
		s.notFound(w, r)
		return
	}
	now := time.Now()
	tok, ok := s.useToken(sc.dir, token, now)
	if !ok {
		s.notFound(w, r)
		return
	}
	s.recordVisit(sc.dir, now)
	r.Body = http.MaxBytesReader(w, r.Body, maxUploadBytes)

	var files []uploadedFile
	var err error
	mediaType, _, _ := mime.ParseMediaType(r.Header.Get("Content-Type"))
	if mediaType == "multipart/form-data" {
		files, err = s.storeMultipart(r, sc)
	} else {
		files, err = s.storeRawBody(r, sc, pathName)
	}
	if err != nil {
		var tooBig *http.MaxBytesError
		switch {
		case errors.As(err, &tooBig):
			http.Error(w, "file too large", http.StatusRequestEntityTooLarge)
		case errors.Is(err, errNoFiles), errors.Is(err, errBadName):
			http.Error(w, err.Error(), http.StatusBadRequest)
		default:
			log.Printf("token upload %s: %v", maskPath(r.URL.Path), err)
			http.Error(w, "upload failed", http.StatusInternalServerError)
		}
		return
	}
	base := publicBase(r)
	for i := range files {
		files[i].URL = base + sc.baseURL + url.PathEscape(files[i].Name)
	}
	log.Printf("token %s uploaded %d file(s)", tok.ID, len(files))

	if r.URL.Query().Has(plainParam) {
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		w.WriteHeader(http.StatusCreated)
		for _, f := range files {
			fmt.Fprintln(w, f.URL)
		}
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	_ = json.NewEncoder(w).Encode(map[string][]uploadedFile{"files": files})
}

func (s *server) storeMultipart(r *http.Request, sc scope) ([]uploadedFile, error) {
	mr, err := r.MultipartReader()
	if err != nil {
		return nil, err
	}
	var files []uploadedFile
	for {
		part, err := mr.NextPart()
		if err == io.EOF {
			break
		}
		if err != nil {
			return nil, err
		}
		if part.FileName() == "" {
			continue
		}
		name, size, err := storeUpload(sc, part.FileName(), part)
		if err != nil {
			return nil, err
		}
		files = append(files, uploadedFile{Name: name, Size: size})
	}
	if len(files) == 0 {
		return nil, errNoFiles
	}
	return files, nil
}

func (s *server) storeRawBody(r *http.Request, sc scope, pathName string) ([]uploadedFile, error) {
	original := pathName
	for _, candidate := range []string{r.URL.Query().Get(nameParam), r.Header.Get(filenameHeader)} {
		if original == "" {
			original = candidate
		}
	}
	if original == "" {
		original = fallbackBaseName + extForType(r.Header.Get("Content-Type"))
	}
	name, size, err := storeUpload(sc, original, r.Body)
	if err != nil {
		return nil, err
	}
	if size == 0 {
		os.Remove(filepath.Join(sc.dir, name))
		return nil, errNoFiles
	}
	return []uploadedFile{{Name: name, Size: size}}, nil
}
