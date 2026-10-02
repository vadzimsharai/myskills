#!/usr/bin/env bash
set -euo pipefail

MODE="${1:?usage: run.sh auth|check|defend|daily|report}"
NETMON_DIR="$(cd "$(dirname "$0")" && pwd)"
CLAUDE_BIN="${CLAUDE_BIN:-claude}"
CLAUDE_MODEL="${NETMON_MODEL:-sonnet}"
MAX_BUDGET_USD="${NETMON_MAX_BUDGET_USD:-0.5}"
CHECK_WINDOW="15m"
LOG_DIR="$NETMON_DIR/data/logs"
VERDICT_SCHEMA='{"type":"object","properties":{"send":{"type":"boolean"},"severity":{"type":"string","enum":["info","low","medium","high"]},"headline":{"type":"string"},"attention":{"type":"array","items":{"type":"object","properties":{"level":{"type":"string","enum":["high","medium","low"]},"what":{"type":"string"},"who":{"type":"string"},"where":{"type":"string"},"action":{"type":"string"}},"required":["level","what","who","where","action"]}},"benign":{"type":"array","items":{"type":"string"}},"recommendations":{"type":"array","items":{"type":"object","properties":{"what":{"type":"string"},"command":{"type":"string"}},"required":["what","command"]}},"notes":{"type":"array","items":{"type":"object","properties":{"key":{"type":"string"},"note":{"type":"string"}},"required":["key","note"]}}},"required":["send","severity","headline","attention","benign","recommendations"]}'

mkdir -p "$LOG_DIR"
exec 9>"$LOG_DIR/$MODE.lock"
flock -n 9 || { echo "[$(date -Is)] $MODE skipped, previous run active" >> "$LOG_DIR/$MODE.log"; exit 0; }

log() { echo "[$(date -Is)] $*" >> "$LOG_DIR/$MODE.log"; }

claude_available() { [ -n "${CLAUDE_CODE_OAUTH_TOKEN:-}${ANTHROPIC_API_KEY:-}" ]; }

status_line() {
  case "$1" in
    high) echo "🔴 Alert" ;;
    medium) echo "🟠 Warning" ;;
    *) echo "🟢 Normal" ;;
  esac
}

# Renders the HTML report into the share folder and prints its URL (empty when sharing is off).
publish_report() {
  local kind="$1" since="$2" verdict_file="$3" findings_file="${4:-}"
  local data_file="$LOG_DIR/$kind-report-data.json"
  "$NETMON_DIR/netmon.py" report-data --since "$since" \
    | jq --arg host_ip "${NETMON_HOST_IP:-}" '. + {host_ip: $host_ip}' > "$data_file"
  "$NETMON_DIR/report.py" --kind "$kind" --data "$data_file" --verdict "$verdict_file" \
    ${findings_file:+--findings "$findings_file"} 2>>"$LOG_DIR/$MODE.log" || true
}

BRIEF_ITEMS=3

send_brief() {
  local title="$1" verdict_file="$2" url="$3" link_label="$4"
  local severity brief text
  severity=$(jq -r '.severity' "$verdict_file")
  brief=$(jq -r --argjson n "$BRIEF_ITEMS" '
    def mark: {"high": "🔴", "medium": "🟠", "low": "⚪"}[.] // "⚪";
    ([.headline | @html]
     + [.attention[:$n][] | "\(.level | mark) \(.what | @html) — \(.who | @html) → \(.where | @html)"])
    | join("\n")' "$verdict_file")
  text="<b>$(status_line "$severity") · $title</b>
$brief"
  if [ -n "$url" ]; then
    text="$text

📄 <a href=\"$url\">$link_label</a> · <a href=\"${NETMON_SHARE_URL%/}/index.html\">all reports</a>"
  fi
  "$NETMON_DIR/notify.sh" --html "$text" >> "$LOG_DIR/$MODE.log"
}

ask_claude() {
  local prompt_file="$1" context_file="$2" out_file="$3"
  (cd "$LOG_DIR" && "$CLAUDE_BIN" -p "$(cat "$prompt_file")

<netmon_data>
$(cat "$context_file")
</netmon_data>" \
    --model "$CLAUDE_MODEL" --restricted --tools "" --strict-mcp-config \
    --max-budget-usd "$MAX_BUDGET_USD" \
    --output-format json --json-schema "$VERDICT_SCHEMA") > "$out_file" 2>>"$LOG_DIR/$MODE.log"
  jq -c 'if type == "array" then .[-1] else . end | .structured_output // (.result | fromjson)' "$out_file"
}

case "$MODE" in
  check)
    findings_file="$LOG_DIR/check-findings.json"
    "$NETMON_DIR/netmon.py" check --since "$CHECK_WINDOW" > "$findings_file"
    count=$(jq '.findings | length' "$findings_file")
    actionable=$(jq '[.findings[] | select(.severity != "low")] | length' "$findings_file")
    if [ "$actionable" -eq 0 ]; then
      log "ok, findings=$count (low only)"
      exit 0
    fi
    if ! claude_available; then
      high=$(jq -r '.findings[] | select(.severity == "high") | "• " + .summary' "$findings_file")
      log "findings=$count, no Claude token, high findings sent raw"
      [ -n "$high" ] && "$NETMON_DIR/notify.sh" "🛡 netmon [high, without AI review]
$high" >> "$LOG_DIR/$MODE.log"
      exit 0
    fi
    context_file="$LOG_DIR/check-context.json"
    jq -n --slurpfile check "$findings_file" \
      --slurpfile hour <("$NETMON_DIR/netmon.py" summary --since 1h) \
      --arg host_ip "${NETMON_HOST_IP:-}" \
      '{host_ip: $host_ip, check: $check[0], last_hour: $hour[0]}' > "$context_file"
    verdict_file="$LOG_DIR/check-verdict.json"
    ask_claude "$NETMON_DIR/prompts/triage.md" "$context_file" "$LOG_DIR/check-claude.json" > "$verdict_file"
    log "findings=$count verdict=$(cat "$verdict_file")"
    # Only high verdicts go out at once; everything else waits for the daily digest.
    if [ "$(jq -r '.send and .severity == "high"' "$verdict_file")" = "true" ]; then
      url=$(publish_report alert 1h "$verdict_file" "$findings_file")
      send_brief "netmon" "$verdict_file" "$url" "Alert report"
    fi
    ;;
  defend)
    log "$("$NETMON_DIR/netmon.py" defend | jq -c '{candidates, decisions: [.decisions[] | {subnet, action}]}')"
    ;;
  auth)
    auth_file="$LOG_DIR/auth-findings.json"
    "$NETMON_DIR/netmon.py" auth-check > "$auth_file"
    jq -c '.findings[]' "$auth_file" | while read -r f; do
      log "$(jq -c '{type, summary}' <<<"$f")"
      "$NETMON_DIR/notify.sh" --html --markup "$(jq -c '.reply_markup' <<<"$f")" \
        "$(jq -r '.telegram' <<<"$f")" >> "$LOG_DIR/$MODE.log"
    done
    ;;
  daily|report)
    context_file="$LOG_DIR/$MODE-summary.json"
    "$NETMON_DIR/netmon.py" report-data --since 24h \
      | jq --arg host_ip "${NETMON_HOST_IP:-}" 'del(.timeline, .flow_graph) + {host_ip: $host_ip}' > "$context_file"
    if ! claude_available; then
      log "no Claude token, $MODE skipped"
      exit 0
    fi
    verdict_file="$LOG_DIR/$MODE-verdict.json"
    ask_claude "$NETMON_DIR/prompts/daily.md" "$context_file" "$LOG_DIR/$MODE-claude.json" > "$verdict_file"
    log "verdict=$(cat "$verdict_file")"
    url=$(publish_report daily 24h "$verdict_file")
    if [ "$MODE" = daily ]; then
      send_brief "netmon · daily" "$verdict_file" "$url" "24-hour report"
      "$NETMON_DIR/netmon.py" prune >> "$LOG_DIR/$MODE.log"
    else
      send_brief "netmon · requested report" "$verdict_file" "$url" "24-hour report"
    fi
    ;;
  *) echo "unknown mode: $MODE" >&2; exit 2 ;;
esac
