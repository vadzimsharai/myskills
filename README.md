# Agent skills

Reusable skills for coding agents. Each skill contains `SKILL.md`; skills that call custom commands include their source and setup scripts.

- `docs/` contains documentation skills.
- `share/` contains the share server, CLI, and upload skills.
- `clip/` contains the `tcp` Python CLI and tmux integration.
- `check-slack/` contains a Slack inbox helper, page template, and optional scheduler.
- `convert-documents-to-markdown/` contains an offline wrapper and a pinned npm dependency manifest.
- `discuss/`, `image/`, and `netmon/` include their helper code.

Run `./link.sh` to link skill directories into supported agent runtimes. It does not deploy servers or install external packages. Follow each skill's setup instructions for its runtime dependencies.

Service URLs, credentials, host addresses, trusted users, and runtime data belong in local configuration and are not included in this repository. Keep generated `.env` files and other secrets out of Git.
