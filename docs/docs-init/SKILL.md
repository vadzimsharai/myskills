---
name: docs-init
description: Use when the user wants to bootstrap or initialise the docs/ folder structure with the hierarchical _index.md graph in a project that doesn't have it yet. Triggers include "/docs-init", "init docs", "create docs structure", "bootstrap docs".
---

# docs-init

Bootstrap the `docs/` graph in the current project: standard subfolders + `_index.md` at each node, classifying any pre-existing markdown into the right domain. Heavy classification runs in a subagent.

## When to use

A new project, or an existing project where `docs/` exists but has no `_index.md` files yet. Run once per project.

## What this skill does

### 1. Probe `docs/`

Run a single Bash command:

```bash
find <cwd>/docs -type f -name '*.md' -not -path '*/inbox/*' -not -path '*/_meta/*' 2>/dev/null | sort
```

(`*.md` already matches `_index.md`. `inbox/` (local git-ignored scratch) and `_meta/` (schema only) are excluded — their files must never be classified into the committed graph.)

From the output decide:

- If the command returns nothing and `docs/` doesn't exist: create the directory.
- If any `_index.md` already exists: stop. Tell the user the project is already bootstrapped — they should use `docs-reindex` to refresh.
- Otherwise: proceed.

### 2. Create skeleton folders

Single Bash invocation:

```bash
mkdir -p <cwd>/docs/{features,decisions,architecture,guides,glossary,todo,_meta}
```

Existing folders are preserved. Existing files inside them are not touched. (`other` is a classification *label* for pre-existing non-standard folders — not a folder to pre-create here. `inbox/` is deliberately NOT created: it is a **local git-ignored scratch** area, made on demand by `docs-update`, and is **not** part of the committed graph.)

### 2a. Write the schema file

Write `<cwd>/docs/_meta/index-format.md` with the canonical `_index.md` + feature-file schema (this is the file every other `docs-*` skill reads at runtime, project-relative so it stays portable across machines). **Copy it verbatim from the reference bundled with the `docs-update` skill (`docs-update/INDEX_FORMAT.md`, sibling of this skill)** — that file is already the exact canonical schema. That bundled `INDEX_FORMAT.md` is the sole source of truth for the `_index.md` + feature-file schema (`AGENTS.md` does NOT contain the field schema). If even the bundled `docs-update/INDEX_FORMAT.md` is missing, tell the user the canonical schema is unavailable and stop — do NOT reconstruct it from memory. If `docs/_meta/index-format.md` already exists, leave it untouched.

### 3. Dispatch the classifier subagent

Invoke `Agent` with `subagent_type: general-purpose` and a briefing:

> You are bootstrapping the documentation graph for the project at `<absolute project root>`. Read `<project root>/docs/_meta/index-format.md` once for the `_index.md` schema.
>
> **Existing docs tree (pre-bootstrap):**
>
> ```
> <output of the find command from step 1>
> ```
>
> **Instructions:**
> 1. Read every existing `.md` file. For each, classify into a domain: `features` / `architecture` / `decisions` / `guides` / `glossary` / `todo`. Heuristics: per-feature descriptions (how a shipped feature works, client usage) → `features`; filenames matching `*-design.md`, `*-protocol*.md`, `*-architecture*.md` → `architecture`; `*-mvp*.md`, `*-spec*.md` → `architecture` or flag for the user (formal spec-driven requirements belong in `openspec/`, not `docs/`); ADR-style numbered files (`NNNN-*.md`) → `decisions`; how-to / setup / quickstart / deploy / dev-environment files → `guides`; glossary / terms files → `glossary`; `TODO*`, `BACKLOG*`, `*-todo.md`, files mostly listing deferred tasks ("later", "to do") → `todo`. When genuinely unsure, leave the file where it is and flag it — do NOT route it into `inbox` (inbox is local git-ignored scratch, not a committed domain).
> 2. Do NOT move or rename any existing file. Classification is metadata only — record it in the parent folder's `_index.md` `files` list with the file's actual current path.
> 3. Write `_index.md` at every folder under `docs/`: the root, the standard subfolders (`features`, `decisions`, `architecture`, `guides`, `glossary`, `todo`), plus any pre-existing subfolders. **Skip `_meta/` and `inbox/`** — `_meta` holds only the schema, and `inbox` is local git-ignored scratch; neither is part of the committed graph, so neither gets an `_index.md`. Each written index must include:
>    - `domain` matching the folder.
>    - `tags` derived from the contained files' topics (kebab-case, ≤ 5 tags).
>    - `children` listing all subfolders that have an `_index.md`, each with a one-line `summary` and `tags` rolled up from that child's own frontmatter `tags`.
>    - `files` listing all `.md` files in this folder excluding `_index.md`, each with a 1-line summary derived from the file's H1 + lead paragraph and `updated` from `git log -1 --format=%cs -- <file>` (today's date if not in git). Quote a `summary` (`summary: "…"`) if it contains a colon-space (`: `) or other YAML-special leading char, else the index won't parse.
> 4. The root `docs/_index.md` lists all standard subfolders (and any extras, **excluding `_meta` and `inbox`**) as `children`. Below the frontmatter, write a short prose section: 2 paragraphs explaining how this docs tree is organised, what the `features/` domain is for, and pointing to the `docs-update` / `docs-search` workflow and `AGENTS.md`.
> 5. Return under 250 words:
>    - count of files classified per domain,
>    - any classification you flagged as low-confidence (so the user can re-file later),
>    - paths of every `_index.md` you wrote.

### 4. Surface the report

Show the subagent's report verbatim. Mention that the user can manually move files between domains and then run `docs-reindex` to refresh.

## Iron rules

- Never move or rename existing files in this skill. File reorganisation is a manual, user-driven step.
- If `_index.md` files already exist anywhere under `docs/`, do not run — direct the user to `docs-reindex`.
- Do not invent files or content; describe only what's actually present.
- Do not commit to git from this skill.
