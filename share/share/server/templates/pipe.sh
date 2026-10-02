#!/bin/sh
# share pipe: uploads stdin to this share and prints the link.
# Usage: cmd | bash <(curl -sL <upload URL>)
set -e
UPLOAD_URL='@UPLOAD_URL@'
MAX_BYTES=26214400
command -v curl >/dev/null 2>&1 || { echo "error: curl required" >&2; exit 1; }
if [ -t 0 ]; then
  echo "error: no stdin input. Usage: cmd | bash <(curl -sL <upload URL>)" >&2
  exit 1
fi
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT INT TERM
cat > "$TMP"
SIZE=$(wc -c < "$TMP" | tr -d ' ')
if [ "$SIZE" -eq 0 ]; then
  echo "error: empty input" >&2
  exit 1
fi
if [ "$SIZE" -gt "$MAX_BYTES" ]; then
  echo "error: input too large ($SIZE bytes, max 25MB)" >&2
  exit 1
fi
URL=$(curl -sS --max-time 60 -X POST --data-binary @"$TMP" \
  -H "Content-Type: text/plain; charset=utf-8" \
  "$UPLOAD_URL?name=clip.txt&plain" | head -n 1)
case "$URL" in
  https://*|http://*) ;;
  *)
    echo "error: upload failed: $URL" >&2
    exit 1
    ;;
esac
echo "$URL"
# stdin is the payload and stdout is the URL, so the OSC 52 escape goes to the terminal.
if [ -z "${SHARE_NO_COPY:-}" ] && [ -w /dev/tty ]; then
  B64=$(printf '%s' "$URL" | base64 | tr -d '\n')
  if [ -n "${TMUX:-}" ]; then
    printf '\033Ptmux;\033\033]52;c;%s\007\033\\' "$B64" 2>/dev/null > /dev/tty || true
  else
    printf '\033]52;c;%s\007' "$B64" 2>/dev/null > /dev/tty || true
  fi
fi
