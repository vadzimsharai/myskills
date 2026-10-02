#!/usr/bin/env bash
# Links `tcp` into ~/.local/bin, sources the F9 binding from the tmux config and reloads the running tmux.
set -euo pipefail
PROJECT_DIR=$(cd "$(dirname "$0")" && pwd)
BIN_DIR="$HOME/.local/bin"
TMUX_CONF="${TMUX_CONF:-$(readlink -f "$HOME/.tmux.conf")}"
SOURCE_LINE="source-file -q \"$PROJECT_DIR/tmux/tmuxcopypast.conf\""

mkdir -p "$BIN_DIR"
ln -sfn "$PROJECT_DIR/bin/tcp" "$BIN_DIR/tcp"

if ! grep -qF "$PROJECT_DIR/tmux/tmuxcopypast.conf" "$TMUX_CONF"; then
  printf '\n%s\n' "$SOURCE_LINE" >> "$TMUX_CONF"
fi

if command tmux info >/dev/null 2>&1; then
  command tmux source-file "$PROJECT_DIR/tmux/tmuxcopypast.conf"
fi
echo "installed: $BIN_DIR/tcp, F9 bound via $TMUX_CONF"
