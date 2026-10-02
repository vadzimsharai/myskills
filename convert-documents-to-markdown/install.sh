#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
command -v npm >/dev/null || { echo "npm is required" >&2; exit 1; }
npm ci --prefix "$SCRIPT_DIR" --omit=dev --ignore-scripts
