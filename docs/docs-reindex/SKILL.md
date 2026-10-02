---
name: docs-reindex
description: Use when the user wants to rebuild the _index.md frontmatter across the docs/ tree after manual changes — file moves, renames, additions, deletions. Triggers include "/docs-reindex", "rebuild docs index", "refresh docs index", "reindex docs".
---

# docs-reindex

Walk the entire `docs/` tree and regenerate the frontmatter of every `_index.md`, preserving free-form prose underneath. Heavy walking runs in a subagent.

## When to use

After: manually moving / renaming / adding / deleting `.md` files in `docs/`; a large refactor; or whenever you suspect the index frontmatter has drifted from the actual filesystem.

## What this skill does

### 1. Verify bootstrap

Read `<cwd>/docs/_index.md`. If it does not exist, tell the user the project is not bootstrapped — they should run `docs-init` first — and stop.

### 2. Dispatch the reindex subagent

Invoke `Agent` with `subagent_type: general-purpose` and a briefing:

> You are reindexing the documentation graph at `<absolute project root>`. Read `<project root>/docs/_meta/index-format.md` once for the `_index.md` schema.
>
> **Instructions:**
> 1. Walk `<absolute project root>/docs/` bottom-up. For every folder that contains at least one `.md` file or a subfolder with an `_index.md`, ensure it has its own `_index.md` (create one if missing — copy `domain` from the parent's frontmatter). **Exclude `docs/_meta/`** — it holds only the schema file and must NOT get an `_index.md` (it is not a graph node). Also skip any git-ignored scratch dir such as `docs/inbox/`.
> 2. For each `_index.md`, reconcile the frontmatter **entry list** against the filesystem. You are fixing membership — which files are listed — and nothing else:
>    - `children` = subfolders with an `_index.md`; `files` = `.md` files in this folder excluding `_index.md`.
>    - **An entry whose file still exists is copied through byte-for-byte** — same `summary`, same `updated`, same `tags`. You do not re-derive them, do not re-word them, do not "refresh" them, and above all do not reconcile `updated` against `git log`. A file that was neither added nor removed produces no diff. This is the point of the skill, not a detail of it.
>    - **Only a genuinely new entry gets fresh values**: a 1-line summary from the file's H1 + lead paragraph, and `updated` from `git log -1 --format=%cs -- <file>` (today if the file isn't in git yet). **Quote the value** (`summary: "…"`) if it contains a colon-space (`: `), `#`, or a leading `[`/`{`/`*`/`&` — otherwise YAML mis-parses the whole index.
>    - The one exception where you may rewrite an existing entry: the file was **renamed** (same content, new path) — carry its old `summary`/`updated` over to the new `path`.
> 3. Replace ONLY the `---` … `---` frontmatter block in each `_index.md`. Leave every byte under the closing `---` untouched, byte-for-byte.
> 3a. Before you finish, run `git diff --stat -- docs/` and read it. **An empty diff is the expected and most common outcome** — if nothing moved on disk, nothing should move in the indexes, and reporting "no changes needed" is a complete, successful run. If the diff shows lines you cannot tie to a specific added, removed or renamed file, you rewrote something you were supposed to leave alone: revert those hunks (`git checkout -- <file>`) and say so in the report.
> 4. Detect drift (membership drift only — see step 2):
>    - Files referenced in old frontmatter that no longer exist on disk → drop from `files` / `children`.
>    - Files on disk not in old frontmatter → add to `files` / `children`.
>    - Free-form prose that mentions a docs-relative file path (e.g. `features/foo.md`, `./bar.md`) that does NOT resolve under `docs/` → flag in the report (do NOT auto-edit prose). Ignore bare repo-root references like `AGENTS.md` and anything under `_meta/` — these are not docs-graph paths and must not be flagged.
> 5. Return under 250 words:
>    - X `_index.md` files changed (zero is a normal answer),
>    - Y new files added to indexes,
>    - Z stale entries removed,
>    - any hunk you reverted in step 3a, and what it was,
>    - list of prose-level drift warnings (docs-relative file paths mentioned in prose that don't resolve under `docs/`; do not include bare repo-root refs like `AGENTS.md` or `_meta/` paths).

### 3. Surface the report

Show the subagent's report verbatim. If drift warnings exist, suggest the user fix them manually or open a follow-up via `docs-update`.

## Iron rules

- Preserve the body (post-frontmatter prose) of every `_index.md` byte-for-byte.
- **Reconcile membership, never wording.** An entry for a file that still exists is untouched — its `summary` and `updated` are somebody's deliberate text, not your draft. Rewriting them is the failure mode this skill is most prone to and the one that made the docs history unreadable.
- **Never mass-update `updated` from `git log`.** Only a brand-new entry gets a date from git.
- A run that changes nothing is a successful run. Do not justify your existence with a diff.
- Do NOT touch any non-`_index.md` file content.
- Do NOT move or rename files.
- Do NOT commit to git from this skill.
