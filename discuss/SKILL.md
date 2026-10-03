---
name: discuss
description: Use when a report, plan, review, or design discussion has several points for the user to answer, decide, confirm or comment on, and a configured share service is available for an interactive discussion page. Turns the list into numbered items (A1, A2, B1, B1.1…), collects the user's answers from the page round by round, keeps the per-item history, closes settled items and reminds the link while anything is open. Not for a single yes/no question that fits in one line of chat.
---

# discuss: an interactive list of decision points

This skill uses the CLI and server from the sibling `share` skill. Set them up as described in `share/SKILL.md`. Run the `dq` commands below as `./dq` from this skill directory. For a different layout, set `SHARE_CLI` and `SHARE_ROOT`; `DQ_REGISTRY` sets the discussion registry path.

When several points require a decision, turn the list into a working document. Each point has a stable ID, a clear explanation, and a complete discussion history. Collect answers on the page, round by round, until every point is closed.

## When to use

Use this whenever a response contains a list of points that need the user's opinion, answer, decision, or confirmation. The list may come from a review, plan, document analysis, or your own questions before starting work. Ask a single short yes/no question in chat instead.

If a discussion on the same subject is already open (`dq status`), add new points to it. Keep one page per task.

## Components

- `./dq` creates discussions, retrieves answers, updates item histories, and validates the state. Edit JSON directly only for item text and new items.
- A discussion is a `/share` directory containing `state.json` (the source of truth), `index.html` (the page), `assets/` (images and diagrams), and `.feedback.jsonl` (page responses).
- The page renders `state.json`: navigation, an accordion of items, statuses, Markdown, tables, highlighted code, Mermaid diagrams, per-item history, an answer box, and quick reply buttons. The Send button writes to `.feedback.jsonl` through `POST /<key>/feedback`.
- Through the same channel the user adds items from the page — "+ Add an item to section A" at the end of a section, the sub-item button on an item, or the phantom section at the bottom (`newGroup: true`) — as one free-form message without a title: it becomes the first chat message of the item, and the AI gives it a title and description. The user can also close, reopen, and delete items. These are events with a `type` (`new` / `status` / `delete`); the page shows them at once and `dq pull --merge` applies them.
- Item history is shown chat-style: the user on the right, the AI on the left; an AI message sent with `--ask` is highlighted as a question for the user; status changes and earlier versions of the description appear as centred lines. The page has no comment box for the whole list (`general` in `state.json` remains only for older discussions).
- `dq watch` picks up answers in live mode (see "Live mode"); otherwise process them when the user says they have replied.
- If `tcp` is installed, `dq new` and the hook add the link to its paste list once; a link already in the list is not added again.
- The page polls `state.json` every few seconds and refreshes in place, preserving open items, drafts, scroll position, and focus. Items with a new AI reply, or added while the page was open, pulse gently in navigation until opened. Sending an answer collapses that item and advances to the next waiting item, cycling round.

## Responsibilities

The page is a reusable template. Build `state.json` for each round; do not write new HTML for each round.

| | Primary agent | Layout subagent |
|---|---|---|
| Work | **Content**: which points to include, what to say and verify, how to answer, status and resolution; graphics — Mermaid as text, exported architecture diagrams, screenshots, exports, code examples. | **Layout**: transfers the brief into `state.json` using `dq` (edit JSON for new items; `dq reply` / `dq set` for answers and statuses), places images in `assets/`, formats text using the rules below, and runs `dq check` until clean. |
| Result | A round brief, `discuss-<key8>-r<N>.md` in a temporary directory. | An updated page and a short report of additions, changes, and unresolved issues. |

Start the layout subagent after the brief is ready, using `subagent_type: general-purpose`; it may run in the background. The primary agent continues its work; when the subagent returns, it reports per-item changes and the link in chat. The primary agent reads user answers with `dq pull --merge` itself, since it has to read them to decide how to respond.

The **brief** is Markdown, one section per item, with facts and decisions rather than presentation details:

````markdown
# Round 2 · key Lp3x…
## B3 [reply] status=closed
resolution: Ports switch over in two steps.
Reply: checked lb.ts:17 — the pool name derives from the port; we add a second port set, two commits.
## B3.1 [new] status=needs-you · tags: decision needed
title: What to do with the monitoring port
body: 8080 is already taken by the metrics UI (compose.yaml:101). I propose 8180/8543. OK?
details: assets/mon-ports.png; compose.yaml excerpt below
```yaml
ports: { plain: 8180, tls: 8543 }
```
## C [new group] Dev
...
````

Markers: `[new]` adds an item or group, `[reply]` adds an AI reply to item history, `[edit]` changes existing item text without altering history, and `[status]` changes only status. Brief text is the substance; the layout subagent shapes it into "what this is / the problem / my proposal / what I need from you" without inventing facts.

**Layout subagent prompt** (substitute paths):

> Build the discussion round using this skill's `SKILL.md`, especially "Items" and "`state.json` format". Brief: `<brief path>`. Discussion key: `<key>`; state: `$(./dq path <key>)`; images go in the adjacent `assets/`. Add AI replies and statuses only through `dq reply` / `dq set`; edit `state.json` for new or changed items. Do not invent details missing from the brief; list gaps in your report. Finish with `dq round <key>` if the brief starts a new round and `dq check <key>` with no errors. Report each changed ID on one line.

Change `page/index.html` only if it lacks a required display capability (a new block type, different navigation). One template serves all discussions. Open pages pick up template changes because `dq` syncs the copy in the share directory on each command and hook check.

## Language

The page interface is in English. Content — titles, descriptions, details, AI messages, `--replies` options — is in the user's language. The page picks its stock quick replies in the content language: the `lang` field in `state.json` (`en`, `ru`, …) or, if absent, a guess from the discussion title.

## Items

**IDs.** A group has a capital letter and a shared topic or mechanism: `A`, `B`. Items are `A1`, `A2`; nested questions are `A1.1`, `A1.2`, and so on. Never change or reuse an ID. If the user answers "about B3", B3 must still refer to the same item three rounds later. Add new items and groups in any round, at the end of their group.

**Title.** Summarize the point in at most ten words, without its ID.

**`body`.** Explain the item to someone who does not remember the conversation, code, or prior rounds. Use plain but accurate technical language:

1. What it is and where it applies, in one or two sentences. Name the system, file, or mechanism in words, not just by path.
2. The problem or question and why it matters for production, work, or people.
3. **My proposal:** one concrete paragraph.
4. **What I need from you:** a question or choice answerable in one sentence.

Keep it **short, 40–90 words**: the user should get the point in half a minute. Everything that explains in more depth goes to `details`. Do not explain the same mechanism twice. Define essential terms in parentheses on first use. Verify numbers, paths, and names.

**`details` — real details, not a couple of lines.** Collapsed under "Details". Put here everything that did not fit the short description: the full explanation of the mechanism, why it works this way, options with pros and cons (as a table), what has been verified and how, risks, a Mermaid diagram (```mermaid), before/after code or configuration, request and response examples, and links such as `path/file.ts@L123`. If the details are only a couple of lines, they are not needed: fold them into the description or drop them (the page shows short details uncollapsed anyway). Put images or exported PNG/SVG diagrams in the share's `assets/` directory and link them with `![](assets/x.png)`. One good diagram beats three paragraphs; if a diagram adds nothing, do not draw it.

**Item chat messages are rich Markdown.** The page renders headings, tables, highlighted code, Mermaid diagrams, and callouts `> [!NOTE]`, `> [!TIP]`, `> [!IMPORTANT]`, `> [!WARNING]`, `> [!CAUTION]`; item IDs (`B3.8`) become links automatically. A long chat message is clipped in height and opens in a full-screen viewer (the ⤢ button). So give a detailed reply (option analysis, a summary across several items) structure — a heading, a table, a callout for the main risk — rather than a wall of text.

**`tags`.** Use short labels such as `decision needed`, `blocker`, `verified`, or `unverified`.

**Status:**

| Status | Meaning | When to set it |
|---|---|---|
| `needs-you` | Awaiting the user | New item; the AI has replied and asks further or proposes a decision to confirm. |
| `in-progress` | AI is investigating or acting | Immediately after `pull --merge`; while a subagent works. |
| `closed` | Resolved | The user's answer leaves no open question. Include a one-sentence `resolution`. |

The AI never deletes an item. A closed item stays visible with its resolution. If the user writes in a closed item, it is open again.

**What the user does on the page** — `dq pull --merge` applies it and prints it under "Page actions":

| Action | What `dq` does | What you do |
|---|---|---|
| New item | Assigns an ID (next in the section, sub-item `<ID>.N`, or for `newGroup` a new section with the next letter), `author: user`, status `in-progress`. Without a title: the title is "New item — the AI will name it", the user's text is the first chat message, `untitled: true`. | Treat it as the first message of a dialogue: `dq edit <key> <ID> --title "…" --body -` (title and description per "Items"); a new section — `dq edit <key> <letter> --title "…"`; then reply in the chat, with `--ask` for a question. |
| Close | `closed`, resolution is the user's text or "Closed by you on the page", event in history. | Do not argue; if closing breaks another item, post a message there. |
| Reopen | `in-progress`, event in history. | See what was missing, reply, set `needs-you`. |
| Delete | `deleted`, event in history; the item and its sub-items disappear from the page. | Nothing; the ID is never reused. To restore: `dq set <key> <ID> --status needs-you`. |

## The item description is living

`body` is not the first wording but the **current state of the item**. After each processed answer, rewrite it with `dq edit <key> <ID> --body -`:

- what it is and where — as before;
- **Decided:** — what has been agreed in the chat, one line each;
- **My proposal** — the current proposal, taking the answers into account;
- **What I need from you** — exactly the question the AI is asking now. No question, no line.

Ask the same question in the chat: `dq reply <key> <ID> --ask --status needs-you --replies "option 1|option 2|option 3" --text "…"`. `--replies` gives 2–4 ready answers to this question as short phrases in the user's voice ("Yes, go with option A", "Later, after B1"). On the page they appear as chips above the answer box, before the stock ones; a click inserts the text into the box. A new AI message without `--replies` clears the previous options. This way the question is visible in the chat (highlighted), in the description, and in the line under the collapsed item's title. `dq edit` puts the previous description into history ("show the earlier one" in the chat). For a closed item the description is the outcome of the discussion, without "What I need from you".

## An answer that touches several items

The user may answer several items at once in one item, or recall something in a later item that changes one already answered. So read every answer against the **whole** list, not just its own item — including answered and closed ones:

- `dq pull` prints `→ mentions other items: …` — a hint, not a boundary: an answer can change an item without naming it ("then we leave the ports alone too").
- Post a separate AI message with its source in each affected item: `dq reply <key> A3 --from B1 --text "Following your answer in B1: …"`. Several items with the same text: `dq reply <key> A3,C2 --from B1 …`. On the page the message shows an "after your answer in B1" link.
- If the AI already answered in the affected item and that answer is now outdated, do not edit the old message — append a new one: "Update: after B1 this no longer holds — …". Recompute the status: the answer settles the item — `closed` with a resolution; a new question appears — `needs-you`; a closed item became wrong — reopen it (`needs-you`) and say why.
- In the chat, on the source item's line, list what else was affected: `B1 — accepted; also closed A3, updated C2`.

## Run a round

**First round**

1. Run `dq new --title "<subject>" --subtitle "<source of the list, one phrase>"`. It prints the key, URL, and `state.json` path.
2. Write the first brief with `context` (overall subject and decisions already made), groups, and `[new]` items. Prepare graphics in `assets/`.
3. Give the brief to the layout subagent (see "Responsibilities"). It fills `state.json` and returns with a clean `dq check`.
4. In chat, give one line on what the list is about and a **table of contents**: `ID · title · what you need from the user`, five to ten words per item, plus the link. Do not copy full texts to chat; they are on the page. If the user asks to see items here, show them in full.

**Later rounds** begin when the user says they replied, asks to check, or answers directly in chat.

1. Run `dq pull <key> --merge` to put page answers into item histories and move those items to `in-progress`. Record chat answers the same way with `dq reply <key> <ID> --who user --text "..."`.
2. Address every answer properly. If it relies on a fact, verify it against code, configuration, queries, or commands. If the user asks a question, answer it rather than restating the item. Check what the answer changes in other items (see "An answer that touches several items"), and process the page actions: the user's own items, closes, reopens, deletions.
3. Delegate long actions (editing a document, testing a hypothesis, writing code) to a background subagent: `dq set <key> <ID> --action "what is being done"`. When it returns: `dq set <key> <ID> --action "what was done" --action-state done` and a reply in history.
4. Write the round brief: each processed item gets `[reply]` with an answer and a status (`closed` also needs a resolution). Keep the answer shorter than the explanation: what you checked, what happened, and what follows. A new question born from an answer becomes a `[new]` item `<ID>.1`, not a paragraph in history. New topics become `[new]` items or groups.
5. Give the brief to the layout subagent. It applies replies and statuses with `dq`, adds items, and runs `dq round` and `dq check`.
6. In chat, give one line per changed item (`B3 — closed: we do X`, `C1 — need your choice between X and Y`) and the link. The full history stays on the page.

Record even short replies in item history. A reader should be able to tell a week later who said what and how it ended.

**Preserve the wording that received an answer.** You may rewrite an item's body (after an answer you often should), but its previous version must stay in history so later readers know what the user answered. `dq check` and `dq edit` track text snapshots in `.bodies.json` next to the page and insert the prior text as `{"who":"ai","kind":"question","round":N}` before the related answers. So run `dq check` after each text edit. If an item is carried over from elsewhere (a review, a previous page) together with its answers, put its original wording first in history with `kind: question`; `dq check` warns if history begins with a user answer.

**Mark new items.** `dq check` sets each item's `round` to its creation round. From round two onward, the page labels items of the current round as new.

**Finish.** When every item is `closed`, run `dq close <key>` and summarize decisions by group in chat. Transfer decisions to a document, task, or code only as a separate step, when asked.

## Live mode: answers are picked up automatically

While a discussion is open, a background watcher picks up page answers and lets the AI reply to individual items without the user asking in chat.

1. After publishing and after processing each answer batch, run `dq watch <key>` in Bash with `run_in_background: true` and `timeout: 7200000` (put keys beginning with `-` after `--`). The watcher waits for answers, collects a batch (`--settle 20` seconds of quiet), then exits — which wakes the session. With no answers for `--max 115` minutes it exits with `WATCH TIMEOUT`.
2. On `NEW ANSWERS <key> «…»: B1, C6 (2 items)`, run `dq pull <key> --merge`, assess each answer (verify facts as in a normal round), and reply in place with `dq reply <key> <ID> --text "…" --status needs-you|closed [--resolution "…"]` (`--ask` for a question), then update the item description with `dq edit` (see "The item description is living"). Other items affected by the answer get a `--from` message; the user's own items get a reply in them. The reply shows on the page at once and the item pulses. Use the layout subagent only when adding items, groups, or graphics. Delegate long work to a background subagent and record it with `dq set … --action`.
3. Right after replying, start `dq watch` in the background again; restart it after `WATCH TIMEOUT` too. Exit code 3 (`ALREADY WATCHED`) means a watcher is already running; a second one is not needed.
4. Stop when all items are `closed` (`dq close`) or the user asks to stop.

After processing, put one line per item and the link in chat. Use `dq round` only after a full pass through the list, not for every batch. The hook reports whether a watcher is running; if not and the discussion is open, start one.

## Link at the end of each response

While any item remains open, **end the last message of every response with the line**

```
Questions: <url>
```

Only the link — no list and no reminder of the items. This holds even when the turn was about something else: a subagent returned, you worked on code, you wrote a long report. The `UserPromptSubmit` hook (`dq status --hook`) lists the discussions open in this directory on every turn.

## `state.json` format

```json
{
  "title": "PR #406: remaining work",
  "subtitle": "Review of ops.md, round by round",
  "context": "Markdown: subject and decisions already made",
  "lang": "en",
  "round": 1,
  "processed_until": "",
  "groups": [
    {
      "id": "A",
      "title": "The production model does not match reality",
      "intro": "Optional Markdown",
      "items": [
        {
          "id": "A1",
          "title": "Legacy network carries core traffic",
          "status": "needs-you",
          "round": 1,
          "tags": ["decision needed"],
          "body": "Markdown: what it is, why it matters, my proposal, what I need from you",
          "details": "Markdown: diagram, code, examples",
          "thread": [
            {"who": "ai", "kind": "question", "round": 1, "at": "...", "text": "previous item wording"},
            {"who": "user", "round": 1, "at": "2026-01-01T12:00:00Z", "text": "..."},
            {"who": "ai", "round": 1, "at": "2026-01-01T12:20:00Z", "text": "..."},
            {"who": "ai", "round": 2, "at": "...", "from": "B1", "text": "update after the answer in B1"},
            {"who": "user", "kind": "event", "round": 2, "at": "...", "text": "closed the item: not needed"}
          ],
          "resolution": "Only for closed items: one-sentence outcome",
          "action": {"state": "running", "text": "A subagent is updating the migration section"},
          "author": "user — only for items added from the page",
          "children": []
        }
      ]
    }
  ],
  "general": []
}
```

`dq` maintains `thread`, `processed_until`, `round` (including item rounds), `updated`, `general`, `author`, `kind: event` messages, and the "Your items" group (`own: true`); do not edit them by hand. Status `deleted` means the user deleted the item: the page hides it and counters skip it. `general` holds page comments not tied to an item.

## `dq` commands

| Command | Purpose |
|---|---|
| `dq new --title T [--subtitle S]` | Create a share accepting responses, a page, an empty `state.json`, and a registry entry. |
| `dq check <key>` | Validate format and counts; preserve old item wording in history and set creation rounds. |
| `dq pull <key> [--merge]` | Fetch new page answers and actions; `--merge` puts answers into item histories and applies new items, closes, reopens, and deletions. |
| `dq reply <key> <ID>[,<ID>…] [--from SRC] [--ask] [--replies "a\|b"] [--who user] [--status S] [--resolution R]` | Add a message to the history of one or more items; text from stdin or `--text`. `--from` is the item whose answer caused it; `--ask` marks a question to the user, highlighted in the chat; `--replies` sets answer options shown as chips. |
| `dq edit <key> <ID> [--title T] [--body FILE\|-] [--details FILE\|-]` | Rewrite an item's title, description, or details; for a section (`A`), its title and `intro`. The previous description goes into history. |
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
- Do not recreate the page each round; keep one link for the whole task.
- Do not close an item without a substantive answer. If the user says "understood" to a choice question, clarify the choice instead of closing.
- Do not rewrite history: old messages are never edited, only new ones appended.
- Do not change item text bypassing `dq check` / `dq edit`, or its previous wording is lost.
