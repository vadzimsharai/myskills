---
name: docs-update
description: Use when the user wants to save a decision, update project documentation, record outcomes from a discussion, or capture a deferred-work note (todo) into the docs/ tree. Triggers include "update docs", "save decision", "record decision", "add todo", "todo note", "remind to do later", "/docs-update".
---

# docs-update

Capture knowledge from the current conversation into the project's `docs/` tree, organised as a graph of folders, each with an `_index.md` node. Heavy work runs in a subagent so the main conversation stays clean.

## When to use

The user has just finished discussing something worth recording — an architectural decision, a spec change, a how-to, a fact about the system — and asks to save it. Triggers above.

## What this skill does

The main agent does steps 1, 2, and 4. The subagent does step 3.

### 1. Distil the input

From the current conversation, write 2–3 sentences capturing:

- **What** to record — the actual content (decision text, fact, design note, etc.).
- **Type guess**: `feature` (living description of a shipped feature) / `adr` (immutable decision) / `spec` (living requirement) / `architecture` (living description) / `guide` (how-to) / `glossary` (term) / `todo` (deferred work — something to do later, not yet a spec) / `inbox` (raw, undecided).
- **Optional pointers**: file paths or modules from the conversation that the subagent may need.

If the conversation does not contain enough material to distil, ask the user a single clarifying question and stop. Do not invoke the subagent on a vague brief.

### 1a. Feature mode (only when type is `feature`)

Before dispatching, the main agent gathers feature-specific context:

- **Asana link**: ask the user for the Asana task URL if it wasn't already given. If they have none, proceed without it (omit the field — never invent one).
- **Branch**: run `git rev-parse --abbrev-ref HEAD` and record the result for the `branch:` frontmatter field.
- **OpenSpec change**: if the conversation references an `openspec/changes/<id>` change, capture its path for the `openspec:` field.

Pass these into the subagent briefing. A feature is **one file** `docs/features/<slug>.md` synthesised from code + the OpenSpec change, written human-readable — see `AGENTS.md` and `docs/_meta/index-format.md`.

### 2. Read the docs root

Read `<cwd>/docs/_index.md`. If it does not exist, tell the user the project hasn't been bootstrapped yet — they should run `docs-init` first — and stop.

### 3. Dispatch the subagent

Invoke `Agent` with `subagent_type: general-purpose` and a briefing structured like this:

> You are a documentation curator for the project at `<absolute project root>`. The docs follow the `_index.md` graph protocol — read `<project root>/docs/_meta/index-format.md` and `<project root>/AGENTS.md` once at the start of your run.
>
> **Material to record:**
>
> > <distilled summary from step 1>
>
> **Pointers:** <file paths / modules from step 1, or NONE>
>
> **Type hint:** `<feature | adr | spec | architecture | guide | glossary | todo | inbox>` — you may override if exploration reveals a better fit.
>
> **Feature context (only when type is `feature`):** branch=`<git branch>`, asana=`<url or NONE>`, openspec=`<openspec/changes/... or NONE>`.
>
> **Root index (`docs/_index.md`):**
>
> ```
> <inline contents from step 2>
> ```
>
> **Instructions:**
> 1. BFS through the index graph from the root, descending only into nodes whose tags/summaries are relevant. Read at most 8 `_index.md` files.
> 2. Decide the action:
>    - **Feature** → create or edit `docs/features/<slug>.md` (one file per feature). Synthesise from the actual code and the linked OpenSpec change — human-readable, not a copy of the formal spec. Frontmatter must follow the feature schema in `docs/_meta/index-format.md` (`domain, feature, status, audience, consumer, dependencies, code_paths, openspec, asana, branch, updated`); set `asana`/`openspec` only if provided (omit otherwise), `branch` from the context, and `code_paths` to the main source dirs/files the feature actually touches (derive them from the files you read). Required H2 sections: `How it works`, `For clients`, `Consumers`, `Dependencies and caveats`, `TODO`. If the file already exists, edit it in place and refresh `updated`/`status`. If the linked OpenSpec change has been archived, set `openspec:` to the archived (`openspec/changes/archive/YYYY-MM-DD-<id>`) or canonical (`openspec/specs/<capability>/spec.md`) path, not the old `changes/<id>`.
>    - **ADR** → create `docs/decisions/NNNN-<slug>.md` with the next free number (look at existing files; pad to 4 digits). Append-only — never edit existing ADRs.
>    - **Spec / architecture / guide / glossary** → if a file already covers the topic, edit it in place; otherwise create a new file in the right subfolder.
>    - **Todo (deferred work)** → create `docs/todo/<slug>.md` (one work item per file). The body must contain: a `# <title>` H1, a one-sentence description of what to do, a `**Why:**` line, an optional `**Where:**` line with file paths or modules involved, and an optional `**Notes:**` block. If a file with the same slug already exists, append a `## Update YYYY-MM-DD` section instead of overwriting. Do not invent priorities or deadlines unless the source material gave them.
>    - **Inbox note** → drop into `docs/inbox/YYYY-MM-DD-<slug>.md` (one note per file). `inbox/` is **local git-ignored scratch**, not part of the committed graph. If a file with the same slug+date already exists, append a `## Update YYYY-MM-DD` section or suffix the slug (`-2`) instead of overwriting.
> 3. Update the `_index.md` of the **immediate parent, and only for the file you touched**:
>    - **File is new to the index** → add an entry: `path`, a 1-line `summary` from its H1 + lead paragraph, `updated` = today. Quote a `summary` containing `: ` or other YAML-special chars.
>    - **File already listed** → set `updated` to today, and **leave the `summary` exactly as it is** unless what the file is *about* actually changed — a new topic, a dropped one, a renamed subject. Do not re-word an accurate summary, do not "sharpen" it, do not rewrite it because you happened to read the file. A summary that means the same thing in different words is pure noise in the diff.
>    - **Never put the change you just made into the summary.** A new field, a bugfix, an edge case, a newly handled failure mode — those live in the document, not in the one line that tells a reader whether to open it. This is the most common way summaries rot here, and it rots them three ways at once: the fresh detail gets promoted to the front as if the page were now about it; a durable fact — the API generation, the subsystem, the contract — gets squeezed out to keep the line short; and a qualifier quietly disappears (`scoped ordering` → `ordering`). The entry ends up looking newer and saying less. If you are adding something to a summary, state in your report what you dropped and why it matters less — if you can't answer that, don't touch the line. Worked example with a real before/after: `docs/_meta/index-format.md`, section "What a `summary` is for".
>    - **Every other entry in that index, and every other index in the tree, stays untouched** — no date reconciliation, no tidying, no re-wording of neighbours. Bubble a `children` summary up to the root only when a genuinely new topic became visible at that level, which is rare.
>    - Full rules for these two fields: `docs/_meta/index-format.md`, section "Stability". **Exception: inbox notes get NO `_index.md`** — `inbox/` is local scratch outside the committed graph, so do not create or maintain `docs/inbox/_index.md`.
> 4. **Edit additively — the existing text is not yours to rewrite.** When a file already covers the topic, insert or update the part your material actually changes and leave everything else byte-identical: do not restructure the page, do not compress existing prose, do not drop sections that your topic simply didn't mention. A small addition must not shrink the document. If something has to be removed because the new material makes it factually wrong, remove exactly that, and quote the removed text with the reason in your report.
>
>    Concretely, on an existing file: **do not rename, reorder or re-level headings you did not add**, and do not re-wrap or re-punctuate paragraphs you are not changing — a diff full of reflowed lines hides the one line that matters. Before you finish, run `git diff --stat -- <file>`: if your edit deleted more lines than it added, or rewrote more than roughly a quarter of a file you were asked to extend, **stop and report it instead of shipping it** — that is a rewrite wearing the clothes of an update, and it is how half a page of hard-won detail has already been lost here. A wholesale rewrite happens only when the briefing explicitly asked for one.
> 5. Do NOT touch files outside `<absolute project root>/docs/`. Do NOT rename or move existing docs. Do NOT commit to git.
> 6. Return a report under 250 words:
>    - paths created or modified,
>    - chosen type and one-sentence reason,
>    - which `_index.md` nodes were updated and what changed,
>    - anything deleted from an existing doc, quoted, with the reason,
>    - any judgment call you flagged (e.g. picked between two plausible homes).

### 4. Surface the report

Show the subagent's report verbatim. If the change is large (>200 lines added in a single file or a brand-new top-level subfolder under `docs/`), explicitly ask the user "ok to keep?" before continuing.

Then check what the run actually did, with `git diff --stat -- docs/` — this catches both failure modes the subagent is prone to, and both have already happened in this repo:

- **A doc got rewritten instead of extended.** Any file with deletions where the user asked only to add: open `git diff -- <file>`, show the removed lines, and ask before leaving them in. Deleted paragraphs are not a stylistic choice.
- **Indexes churned.** The only `_index.md` that may appear in the diff is the immediate parent of the file you touched, and inside it only that file's entry. Re-worded summaries of other files, shuffled `updated` dates, "tidied" neighbours — revert them (`git checkout -- <path>`) and say so. This noise is what makes the docs history unreadable.

## Index format reference

`docs/_meta/index-format.md` (project-relative, created by `docs-init`) — schema for `_index.md` files and the feature-file frontmatter. The subagent reads it once at the start of its run. Project-wide doc conventions live in `AGENTS.md` at the repo root.

## Iron rules

- Never invoke the subagent without a concrete distilled summary.
- Never let the subagent touch files outside `<cwd>/docs/`.
- Never let a small addition delete existing content. If the report shows an edit that removed material the user didn't ask to remove, say so plainly and offer to restore it — the diff is easier to undo now than the knowledge is to recover later.
- Never let a docs edit turn into an index sweep. One file touched → one entry in one `_index.md`. Everything else in the tree stays byte-identical.
- Never commit to git from this skill.
- The main agent must not re-read every doc file itself; that's the subagent's job. The main context only sees the report.
