#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
SHARE_CLI="${SHARE_CLI:-$SCRIPT_DIR/../share/bin/share}"
SHARE_KEY="${SHARE_KEY:?Set SHARE_KEY to the destination share key}"
SHARE_DIR="$("$SHARE_CLI" path "$SHARE_KEY")"
TOKENS_FILE="$SHARE_DIR/.tokens.json"
DEFAULT_TTL=30m
UPLOAD_MTIME_SLACK_SECONDS=5

cmd_url() {
  local ttl="${1:-$DEFAULT_TTL}" error_file upload_url token_id
  "$SHARE_CLI" up >/dev/null 2>&1
  error_file=$(mktemp)
  trap 'rm -f "$error_file"' RETURN
  upload_url=$("$SHARE_CLI" token "$SHARE_KEY" --once --ttl "$ttl" --label "diagnostic upload" 2>"$error_file")
  token_id=$(grep -o '#[0-9a-f]*' "$error_file" | tr -d '#')
  [[ -n "$upload_url" && -n "$token_id" ]] || { cat "$error_file" >&2; exit 1; }
  printf '%s %s\n' "$token_id" "$upload_url"
}

cmd_result() {
  local token_id="$1" used_at used_epoch
  used_at=$(jq -r --arg id "$token_id" '.tokens[] | select(.id == $id) | .used // ""' "$TOKENS_FILE")
  [[ -n "$used_at" ]] || { echo "token #$token_id not used yet" >&2; exit 1; }
  used_epoch=$(date -d "$used_at" +%s)
  find "$SHARE_DIR" -maxdepth 1 -type f ! -name '.*' -printf '%T@ %p\n' \
    | awk -v used="$used_epoch" -v slack="$UPLOAD_MTIME_SLACK_SECONDS" '{delta=$1-used; if (delta<0) delta=-delta; if (delta<=slack) print delta, $2}' \
    | sort -n | head -n 1 | cut -d' ' -f2-
}

case "${1:-}" in
  url) shift; cmd_url "$@" ;;
  result) shift; cmd_result "$1" ;;
  *) echo "usage: $0 url [ttl] | result <token-id>" >&2; exit 2 ;;
esac
