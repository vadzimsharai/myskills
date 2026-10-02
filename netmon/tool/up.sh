#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

SURICATA_IMAGE="jasonish/suricata@sha256:46eb25a3577657665185ba7d2ef2c2c92415881b90123397c5dd63ea40a9e759"
DOCKER=(docker)
docker info >/dev/null 2>&1 || DOCKER=(sudo docker)

mkdir -p data/rules data/suricata data/tetragon data/logs
[ -f .env ] || cp .env.example .env
chmod 600 .env

set_env() {
  if grep -q "^$1=" .env; then
    sed -i "s|^$1=.*|$1=$2|" .env
  else
    echo "$1=$2" >> .env
  fi
}

"${DOCKER[@]}" build -t netmon-scheduler:local scheduler

read -r iface host_ip < <("${DOCKER[@]}" run --rm --network host --entrypoint python3 netmon-scheduler:local -c '
import socket
iface = next(line.split()[0] for line in open("/proc/net/route") if line.split()[1] == "00000000")
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
s.connect(("1.1.1.1", 53))
print(iface, s.getsockname()[0])')
set_env NETMON_IFACE "$iface"
set_env NETMON_HOST_IP "$host_ip"
echo "interface=$iface host_ip=$host_ip"

if [ ! -s data/rules/suricata.rules ]; then
  echo "downloading ET Open rules (one time)"
  "${DOCKER[@]}" run --rm -v "$PWD/data/rules:/var/lib/suricata/rules" \
    -v "$PWD/suricata/suricata.yaml:/etc/suricata/suricata.yaml:ro" \
    --entrypoint suricata-update "$SURICATA_IMAGE" --no-test --no-reload -o /var/lib/suricata/rules
fi

if [ ! -s data/geo/ip2asn-v4.tsv.gz ]; then
  echo "downloading iptoasn IPv4 database (one time, public domain)"
  mkdir -p data/geo
  curl -fsS -m 120 -o data/geo/ip2asn-v4.tsv.gz https://iptoasn.com/data/ip2asn-v4.tsv.gz
  sha256sum data/geo/ip2asn-v4.tsv.gz
fi

"${DOCKER[@]}" compose up -d
"${DOCKER[@]}" compose ps --format '{{.Name}}\t{{.Status}}'

grep -q '^CLAUDE_CODE_OAUTH_TOKEN=.\+' .env || echo "note: CLAUDE_CODE_OAUTH_TOKEN is empty in .env, alerts go without AI triage (run: claude setup-token)"
grep -q '^TELEGRAM_CHAT_ID=.\+' .env || echo "note: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID are empty in .env, Telegram alerts are off"
