#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/server/.env"
if [[ -e "$ENV_FILE" ]]; then
  echo "Configuration already exists: $ENV_FILE"
  exit 0
fi

command -v openssl >/dev/null || { echo "openssl is required" >&2; exit 1; }
umask 077
SECRET="$(openssl rand -hex 32)"
cat > "$ENV_FILE" <<CONFIG
SHARE_SECRET=$SECRET
SHARE_LOGIN=
SHARE_PASSWORD=
SHARE_AUTH_PATH=owner
SHARE_UID=$(id -u)
SHARE_GID=$(id -g)
CONFIG
echo "Created $ENV_FILE. Set SHARE_LOGIN and SHARE_PASSWORD to enable the dashboard."
