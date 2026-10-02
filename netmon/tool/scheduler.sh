#!/usr/bin/env bash
set -uo pipefail
NETMON_DIR="$(cd "$(dirname "$0")" && pwd)"
AUTH_PERIOD_SECONDS=30
CHECK_PERIOD_SECONDS=600
DAILY_HOUR_UTC=6

if [ "${NETMON_AUTO_DEFEND:-0}" = 1 ]; then
  "$NETMON_DIR/netmon.py" restore-bans
fi
last_check_slot=$(( $(date +%s) / CHECK_PERIOD_SECONDS ))
last_daily=""
while true; do
  sleep $(( AUTH_PERIOD_SECONDS - $(date +%s) % AUTH_PERIOD_SECONDS ))
  "$NETMON_DIR/run.sh" auth &
  slot=$(( $(date +%s) / CHECK_PERIOD_SECONDS ))
  if [ "$slot" -ne "$last_check_slot" ]; then
    last_check_slot="$slot"
    "$NETMON_DIR/run.sh" check &
    if [ "${NETMON_AUTO_DEFEND:-0}" = 1 ]; then
      "$NETMON_DIR/run.sh" defend &
    fi
  fi
  today=$(date -u +%F)
  if [ "$(date -u +%-H)" -eq "$DAILY_HOUR_UTC" ] && [ "$last_daily" != "$today" ]; then
    last_daily="$today"
    "$NETMON_DIR/run.sh" daily &
  fi
done
