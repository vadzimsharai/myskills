#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET_DIRS=("$HOME/.claude/skills" "$HOME/.agents/skills" "$HOME/.gemini/config/skills")

for target_dir in "${TARGET_DIRS[@]}"; do
  mkdir -p "$target_dir"
  while IFS= read -r -d '' skill_file; do
    skill_dir="$(dirname "$skill_file")"
    skill_name="$(basename "$skill_dir")"
    link_path="$target_dir/$skill_name"
    if [[ -e "$link_path" && ! -L "$link_path" ]]; then
      echo "skip $link_path: directory already exists" >&2
      continue
    fi
    ln -sfn "$skill_dir" "$link_path"
  done < <(find "$REPO_DIR" -mindepth 2 -maxdepth 3 -name SKILL.md -print0)
done
