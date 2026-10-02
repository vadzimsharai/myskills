---
name: check-slack
description: Use when the user asks to review their Slack inbox, mentions, direct messages, or followed threads and keep a searchable summary on a private share page. Also use for recall questions about earlier checks or when the user explicitly asks to schedule or stop daily checks.
---

# Check Slack

Review messages relevant to the user and maintain one searchable page. Slack access is read-only. Never send messages, react, create tasks, or change Slack content through this skill.

The `sk` helper and page template are bundled here. The sibling `share` skill provides the server and CLI. Run commands from this skill directory. Set `CHECK_SLACK_WORKSPACE` to the Slack workspace subdomain if message records do not already include permalinks. The helper stores its share key and check cursor in `~/.config/check-slack/config.json`; the share data is stored under `SHARE_ROOT` (default `~/.local/share/agent-share`). These local files are private and must stay out of Git.

Copy `channels.example.json` to the ignored `channels.json` when you want full-channel scans. Add channels as objects with `id`, `name`, and `why` fields. Channel IDs and names belong in local configuration. Do not commit them to a public repository.

## Run a check

1. Ensure the share server is configured and running. Run `./sk init` once to create the page. Later checks reuse its URL.
2. Run `./sk pull --merge` to apply page feedback. Investigate any item marked for deeper review.
3. Run `./sk since` for the next time window, `./sk known --open` for existing items, `./sk topics` for existing groups, and `./sk channels` for configured full-channel scans.
4. Identify the user's Slack account from the connected Slack profile. Search mentions, direct messages, and threads involving that account. Read replies before deciding whether an item is new, updated, resolved, or irrelevant. Search and read configured channels if present. Do not silently drop a relevant message. Determine who was addressed from the thread, not from the topic alone.
5. Group related messages into topics. For a reported problem, check available evidence before stating a cause; label unverified claims clearly. Do not copy credentials or private tokens from Slack into the page.
6. Write a JSON patch with `topics` and `items`, then run `./sk upsert <patch.json>` and `./sk check`. Run `./sk done --until <now_ts> --summary "<short result>" --stats '{"new":0,"updated":0}'` when the check is complete, including when nothing new was found.
7. Return the page URL from `./sk url`, the count of new items, and the items that require the user's action. Keep confidential Slack content off public channels and links.

An item has `topic`, `kind` (`problem`, `request`, `info`, `decision`), `status` (`needs-you`, `open`, `watching`, `done`), `severity` (`high`, `medium`, `low`), `title`, `summary`, and `source`. Set `to` to `me` when the user is addressed directly, `backend` when their backend team is addressed, or `other` when someone else is responsible. `source` includes `channel_id`, `channel`, `ts`, `thread_ts`, `author`, `at`, and preferably the Slack message `url`. A `needs-you` item also requires a concrete `ask` and `to=me` or `to=backend`. Reserve `high` severity for items addressed to the user or backend team; other items can be at most `medium`. Optional fields include `body`, `findings`, `tags`, `people`, `links`, `last_reply_ts`, and `note`. Existing items can be updated by `id` or by matching the same thread source. Use `./sk upsert --help` for the patch command.

For a recall question, search the saved page with `./sk find <terms> [--full]`. If no item matches, search Slack directly and cite the message permalink. Use `./sk todo` for open requests to the user.

## Daily checks

Only start scheduling when the user asks. `daily.sh` requires `tmux`, GNU `date`, and a configured Claude CLI. Run it from the project directory whose context the check should use, or set `CHECK_SLACK_CWD`.

```bash
./daily.sh start [HH:MM]
./daily.sh status
./daily.sh stop
./daily.sh run
```

The schedule runs in a local tmux session and writes a local log. It does not grant extra permissions to the CLI. Keep the share page private, review its contents before sharing its link, and do not create a fresh page unless the user requests one.
