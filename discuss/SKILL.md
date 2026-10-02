---
name: discuss
description: Use when a report, plan, review, or design discussion has several points for the user to answer or decide, and a configured share service is available for an interactive discussion page.
---

# discuss: an interactive list of decision points

This skill uses the CLI and server from the sibling `share` skill. Set them up as described in `share/SKILL.md`. Run the `dq` commands below as `./dq` from this skill directory. For a different layout, set `SHARE_CLI` and `SHARE_ROOT`; `DQ_REGISTRY` sets the discussion registry path.

When several points require a decision, turn the list into a working document. Each point has a stable ID, a clear explanation, and a complete discussion history. Collect answers on the page, round by round, until every point is closed.

## When to use

Use this whenever a response contains multiple points that need the user's opinion, answer, decision, or confirmation. The list may come from a review, plan, document analysis, or your own questions. Ask a single short yes/no question in chat.

If a discussion on the same subject is already open (`dq status`), add new points to it. Keep one page per task.

## Components

- `./dq` creates discussions, retrieves answers, updates item histories, and validates the state. Edit JSON directly only for item text and new items.
- A discussion is a `/share` directory containing `state.json` (the source of truth), `index.html`, `assets/` (images and diagrams), and `.feedback.jsonl` (page responses).
- The page renders `state.json`: navigation, item status, Markdown, tables, highlighted code, Mermaid diagrams, per-item history, answer fields, and quick response buttons. The Send button writes to `.feedback.jsonl` through `POST /<key>/feedback`. `dq watch` picks up answers in live mode; otherwise process them when the user says they have replied.
- If `tcp` is installed, add the link to the paste list once.
- The page polls `state.json` every few seconds and refreshes in place, preserving open items, drafts, scroll position, and focus. New items and AI replies pulse in navigation until opened. Sending an answer collapses that item and advances to the next waiting item.

## Responsibilities

The page is a reusable template. Build `state.json` for each round; do not write new HTML for each round.

| | Primary agent | Layout subagent |
|---|---|---|
| Work | Decide which points to include, what to say and verify, how to answer, status and resolution; prepare Mermaid text, screenshots, exports, and code examples. | Transfer the brief into `state.json` using `dq` (edit JSON for new items; use `dq reply` / `dq set` for answers and statuses), place images in `assets/`, format text using the rules below, and run `dq check` until clean. |
| Result | A round brief at `/tmp/discuss-<key8>-r<N>.md`. | An updated page and a short report of additions, changes, and unresolved issues. |

Start the layout subagent after the brief is ready, using `subagent_type: general-purpose`; it may run in the background. The primary agent continues its work, then reports per-item changes and the link in chat. The primary agent reads user answers with `dq pull --merge` and decides how to respond.

The **brief** is Markdown, one section per item, with facts and decisions rather than presentation details:

````markdown
# Round 2 · key Abcd…
## B3 [reply] status=closed
resolution: CSV export will be available.
Reply: I checked the export requirements; CSV meets them.
## B3.1 [new] status=needs-you · tags: decision needed
title: Is JSON needed?
body: Only tabular export is currently needed. I propose CSV. Do you also need JSON?
details: example columns in `assets/export-example.png`
## C [new group] Accessibility
...
````

Markers: `[new]` adds an item or group, `[reply]` adds an AI reply to item history, `[edit]` changes existing item text without altering history, and `[status]` changes only status. The layout subagent formats brief text as "what this is / the problem / my proposal / what I need from you" without inventing facts.

**Layout subagent prompt** (substitute paths):

> Build the discussion round using `SKILL.md`, especially "Items" and "state.json format". Brief: `<brief path>`. Discussion key: `<key>`; state: `$(./dq path <key>)`; images go in the adjacent `assets/`. Add AI replies and statuses only through `dq reply` / `dq set`; edit `state.json` for new or changed items. Do not invent details missing from the brief; list gaps in your report. Finish with `dq round <key>` if the brief starts a new round and `dq check <key>` with no errors. Report each changed ID on one line.

Change `page/index.html` only if it lacks a required display capability. One template serves all discussions. Open pages pick up template changes because `dq` syncs the copy in the share directory on each command and hook check.

## Items

**IDs.** A group has a capital letter and a shared topic or mechanism: `A`, `B`. Items are `A1`, `A2`; nested questions are `A1.1`, `A1.2`, and so on. Never change or reuse an ID. If the user answers "about B3", B3 must still refer to the same item three rounds later. Add new items at the end of their group in any round.

**Title.** Summarize the point in at most ten words, without its ID.

**`body`.** Explain the item to someone who does not remember the conversation, code, or prior rounds. Use plain but accurate technical language:

1. What it is and where it applies, in one or two sentences. Name the system, file, or mechanism in words, not just by path.
2. The problem or question and why it matters for production, work, or people.
3. **My proposal:** one concrete paragraph.
4. **What I need from you:** a question or choice answerable in one sentence.

Aim for 60–150 words. Do not explain the same mechanism twice. Define essential terms on first use. Verify numbers, paths, and names.

**`details`.** Put useful but nonessential material here; it is collapsed by default: Mermaid diagrams, before/after code or configuration, request and response examples, option tables, and links such as `path/file.ts@L123`. Put images or exported PNG/SVG diagrams in the share's `assets/` directory and link them with `![](assets/x.png)`. Include a diagram only when it clarifies the point.

**`tags`.** Use short labels such as `decision needed`, `blocker`, `verified`, or `unverified`.

**Status:**

| Status | Meaning | When to set it |
|---|---|---|
| `needs-you` | Awaiting the user | New item; the AI has replied and needs another answer or confirmation. |
| `in-progress` | AI is investigating or acting | Immediately after `pull --merge`; while a subagent works. |
| `closed` | Resolved | The user's answer leaves no open question. Include a one-sentence `resolution`. |

Never delete an item. A closed item stays visible with its resolution. If the user comments on it, reopen it.

## Run a round

**First round**

1. Run `dq new --title "<subject>" --subtitle "<source of the list>"`. It prints the key, URL, and `state.json` path.
2. Write the first brief with `context` (overall subject and decisions already made), groups, and `[new]` items. Prepare diagrams and images in `assets/`.
3. Give the brief to the layout subagent. It fills `state.json` and returns with a clean `dq check`.
4. In chat, give a one-line description and a table of `ID · title · what you need from the user`, using five to ten words per item, plus the link. Keep full item text on the page unless asked to show it in chat.

**Later rounds** begin when the user replies on the page or in chat.

1. Run `dq pull <key> --merge` to put page answers into item histories and move those items to `in-progress`. Record chat answers with `dq reply <key> <ID> --who user --text "..."`.
2. Address every answer. Verify factual claims against code, configuration, queries, or commands. Answer questions directly.
3. Delegate longer actions in the background, using `dq set <key> <ID> --action "work in progress"`. On completion, run `dq set <key> <ID> --action "work completed" --action-state done` and add a reply to history.
4. Write the round brief: each processed item gets `[reply]`, an answer, and a status; `closed` also needs a resolution. Keep the answer shorter than the original explanation: what you checked, what happened, and what follows. A new question becomes a `[new]` child item such as `<ID>.1`. New topics become new items or groups.
5. Give the brief to the layout subagent. It applies replies and statuses with `dq`, adds items, and runs `dq round` and `dq check`.
6. In chat, give one line per changed item and the link. The full history stays on the page.

Record even short replies in item history. A reader should be able to understand the decisions a week later.

**Preserve the wording that received an answer.** You may rewrite an item's body, but its previous version must stay in history so later readers know what the user answered. `dq check` tracks text snapshots in `.bodies.json` and inserts the prior text as `{"who":"ai","kind":"question","round":N}` before the related answers. Run `dq check` after each text edit. If transferring an item with answers from elsewhere, put its original wording first in history with `kind: question`. `dq check` warns if history begins with a user answer.

**Mark new items.** `dq check` sets each item's `round` to its creation round. From round two onward, the page labels new items.

**Finish.** When every item is `closed`, run `dq close <key>` and summarize decisions by group in chat. Transfer decisions to a document, task, or code when requested.

## Live mode

While a discussion is open, a background watcher picks up page answers and lets the AI reply to individual items without a chat prompt.

1. After publishing and after processing each answer batch, run `dq watch <key>` in Bash with `run_in_background: true` and `timeout: 7200000`. Put keys beginning with `-` after `--`. The watcher waits for answers, settles a batch after `--settle 20` seconds of inactivity, then exits to wake the session. With no answers for `--max 115` minutes it exits with `WATCH TIMEOUT`.
2. On `NEW ANSWERS <key> "…": B1, C6 (2 items)`, run `dq pull <key> --merge`, assess each answer, and reply in place with `dq reply <key> <ID> --text "…" --status needs-you|closed [--resolution "…"]`. The page updates immediately. Use the layout subagent only when adding items, groups, or images. Delegate long work and record it with `dq set … --action`.
3. Restart `dq watch` after answering or after `WATCH TIMEOUT`. Exit code 3 (`ALREADY WATCHED`) means another watcher is running.
4. Stop when all items are `closed` (`dq close`) or the user asks to stop.

After processing, put one line per item and the link in chat. Use `dq round` only after a full pass through the list, not for every batch. The hook reports whether a watcher is running; restart it if a discussion remains open.

## Link at the end of each response

While any item remains open, end the last message of every response with:

```
Questions: <url>
```

Include only the link, not a repeated item list. This applies even when the current turn concerns other work. The `UserPromptSubmit` hook (`dq status --hook`) lists discussions open in this directory on every turn.

## `state.json` format

```json
{
  "title": "PR #406: remaining work",
  "subtitle": "Review of ops.md, round by round",
  "context": "Markdown: subject and decisions already made",
  "round": 1,
  "processed_until": "",
  "groups": [
    {
      "id": "A",
      "title": "Export format",
      "intro": "Optional Markdown",
      "items": [
        {
          "id": "A1",
          "title": "File format for export",
          "status": "needs-you",
          "round": 1,
          "tags": ["decision needed"],
          "body": "Markdown: context, impact, proposal, and question for the user",
          "details": "Markdown: diagram, code, examples",
          "thread": [
            {"who": "ai", "kind": "question", "round": 1, "at": "...", "text": "previous item wording"},
            {"who": "user", "round": 1, "at": "2025-01-01T12:00:00Z", "text": "..."},
            {"who": "ai", "round": 1, "at": "2025-01-01T12:20:00Z", "text": "..."}
          ],
          "resolution": "Only for closed items: one-sentence outcome",
          "action": {"state": "running", "text": "Updating the export example"},
          "children": []
        }
      ]
    }
  ],
  "general": []
}
```

`dq` maintains `thread`, `processed_until`, `round` (including item rounds), `updated`, and `general`; do not edit them by hand. `general` contains comments on the whole discussion.

## `dq` commands

| Command | Purpose |
|---|---|
| `dq new --title T [--subtitle S]` | Create a share accepting responses, a page, an empty `state.json`, and a registry entry. |
| `dq check <key>` | Validate format and counts; preserve old item wording in history and set creation rounds. |
| `dq pull <key> [--merge]` | Fetch new page answers; `--merge` puts them into item histories. |
| `dq reply <key> <ID> [--who user] [--status S] [--resolution R]` | Add a message to item history; read text from stdin. |
| `dq set <key> <ID> [--status S] [--resolution R] [--action T --action-state running\|done\|failed]` | Update status, resolution, or ongoing action. |
| `dq round <key>` | Start the next round. |
| `dq watch <key> [--settle 20] [--max 115]` | Wait for page answers in the background; exit with `NEW ANSWERS …`, `WATCH TIMEOUT`, or code 3 if already watched. |
| `dq status [--all]` | List discussions open in this directory. |
| `dq url <key>`, `dq path <key>` | Show the link or `state.json` path. |
| `dq page <key>` | Refresh the page immediately; usually unnecessary because the copy updates automatically. |
| `dq close <key>` | Stop reminders for the discussion. |

`<key>` may be the full share key, its first eight characters, or a configured `--slug`. Put keys beginning with `-` after `--`, for example `dq check -- -bGh7YOY`.

## Avoid

- Do not put secrets on the page. Anyone with the link can open it.
- Keep one page for all rounds of a task.
- Do not close an item without a substantive answer. If the user says "understood" to a choice question, clarify the choice.
- Append to history; do not rewrite old messages.
- Run `dq check` after changing item text so its previous wording is preserved.
