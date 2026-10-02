#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
NETMON_DIR="$(cd .. && pwd)"
TRUSTED_IPS_FILE="$NETMON_DIR/trusted-ips.txt"
DOCKER=(docker)
docker info >/dev/null 2>&1 || DOCKER=(sudo docker)
on_host() { "${DOCKER[@]}" run --rm -i --privileged --pid=host alpine:3 nsenter -t 1 -m -u -i -n -p -- "$@"; }

[ -f "$TRUSTED_IPS_FILE" ] || : > "$TRUSTED_IPS_FILE"
mkdir -p "$NETMON_DIR/data" && touch "$NETMON_DIR/data/netmon-ban.log"

on_host sh -c 'DEBIAN_FRONTEND=noninteractive apt-get install -y -q fail2ban >/dev/null'
sed "s|@TRUSTED_IPS_FILE@|$TRUSTED_IPS_FILE|; s|@TRUSTED_DYNAMIC_FILE@|$NETMON_DIR/data/trusted-dynamic.json|" netmon-ignoreip \
  | on_host sh -c 'cat > /usr/local/bin/netmon-ignoreip && chmod 755 /usr/local/bin/netmon-ignoreip'
for filter in filter.d/*.conf; do
  on_host sh -c "cat > /etc/fail2ban/$filter" < "$filter"
done
HOST_IP="$(sed -n 's/^NETMON_HOST_IP=//p' "$NETMON_DIR/.env" 2>/dev/null)"
sed "s|@NETMON_DIR@|$NETMON_DIR|; s|@HOST_IP@|$HOST_IP|" netmon-sshd.local | on_host sh -c 'cat > /etc/fail2ban/jail.d/netmon-sshd.local'
on_host sh -c 'fail2ban-client -t >/dev/null && systemctl enable --now fail2ban >/dev/null 2>&1 && systemctl restart fail2ban && sleep 3 && fail2ban-client status'
