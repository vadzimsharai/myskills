#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
SESSION="check-slack"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/check-slack"
LOG="$STATE_DIR/daily.log"
LOCK="$STATE_DIR/daily.lock"
WORK_DIR="${CHECK_SLACK_CWD:-$PWD}"
CLAUDE_BIN="${CLAUDE_BIN:-claude}"
DEFAULT_AT="09:00"
PROMPT="Use the check-slack skill at $SKILL_DIR/SKILL.md to run a scheduled check. Do not ask interactive questions. Record completion with sk done --mode daily, then print the page URL and a short result."

mkdir -p "$STATE_DIR"

seconds_until() {
  local at="$1" now target
  now=$(date +%s)
  target=$(date -d "today $at" +%s)
  (( target <= now )) && target=$(date -d "tomorrow $at" +%s)
  echo $(( target - now ))
}

run_check() {
  exec 9>"$LOCK"
  if ! flock -n 9; then
    echo "[$(date -Is)] skipped: previous run still active" >> "$LOG"
    return 0
  fi
  {
    echo "=== $(date -Is) check start ==="
    cd "$WORK_DIR"
    "$CLAUDE_BIN" -p "$PROMPT" 2>&1 || echo "(claude exit $?)"
    echo "=== $(date -Is) check done ==="
    echo
  } >> "$LOG" 2>&1
}

case "${1:-status}" in
  start)
    at="${2:-$DEFAULT_AT}"
    date -d "today $at" >/dev/null 2>&1 || { echo "bad time: $at (HH:MM)"; exit 2; }
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "already running ($(cat "$STATE_DIR/at" 2>/dev/null)); stop it first to change the time"
      exit 0
    fi
    echo "$at" > "$STATE_DIR/at"
    printf -v command '%q %q %q' "$SKILL_DIR/daily.sh" loop "$at"
    tmux new-session -d -s "$SESSION" -c "$WORK_DIR" "$command"
    echo "daily check every day at $at (tmux session '$SESSION'), log: $LOG"
    ;;
  stop)
    tmux kill-session -t "$SESSION" 2>/dev/null && echo "stopped" || echo "not running"
    ;;
  status)
    if tmux has-session -t "$SESSION" 2>/dev/null; then
      echo "running: every day at $(cat "$STATE_DIR/at" 2>/dev/null), log: $LOG"
    else
      echo "not running"
    fi
    [[ -f "$LOG" ]] && grep -E '^=== ' "$LOG" | tail -4 || true
    ;;
  run)
    run_check
    ;;
  loop)
    at="${2:-$DEFAULT_AT}"
    echo "[$(date -Is)] scheduler up, daily at $at" >> "$LOG"
    while true; do
      sleep "$(seconds_until "$at")"
      run_check
      sleep 60
    done
    ;;
  *)
    echo "usage: daily.sh start [HH:MM] | stop | status | run"
    exit 2
    ;;
esac
