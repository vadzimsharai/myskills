#!/usr/bin/env bash
set -euo pipefail
PARSE_MODE=""
REPLY_MARKUP=""
while [ $# -gt 1 ]; do
  case "$1" in
    --html) PARSE_MODE="HTML"; shift ;;
    --markup) REPLY_MARKUP="$2"; shift 2 ;;
    *) break ;;
  esac
done
TEXT="${1:?usage: notify.sh [--html] [--markup <json>] <text>}"
if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
  echo "notify skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is empty"
  exit 0
fi
curl -sS -m 20 -o /dev/null -w '%{http_code}\n' -X POST \
  "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" \
  --data-urlencode "text=${TEXT}" \
  --data-urlencode "disable_web_page_preview=true" \
  ${PARSE_MODE:+--data-urlencode "parse_mode=$PARSE_MODE"} \
  ${REPLY_MARKUP:+--data-urlencode "reply_markup=$REPLY_MARKUP"}
