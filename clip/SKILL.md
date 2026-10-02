---
name: clip
description: Use when the user asks to add text to the tmux paste list or clipboard history.
---

# Save text to the paste list

The `tcp` CLI and its Python implementation are bundled in `tool/`. Run `tool/bin/tcp` from this skill directory. To install the `tcp` command in `~/.local/bin` and bind the tmux picker to F9, run `tool/install.sh` once.

```bash
tool/bin/tcp add -s ai -l "command" -- "<text to paste>"
tool/bin/tcp list
```

Each clip contains one item the user can paste in one action. For several related clips, add them in one command with one `-l` label per clip. Use a quoted heredoc when text contains shell metacharacters or newlines.

The paste list uses local SQLite storage without encryption. Ask before adding credentials or other sensitive values unless the user explicitly requested that value. Do not clear or remove existing clips unless asked.
