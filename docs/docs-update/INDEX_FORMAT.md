# `_index.md` format

Every folder under `docs/` (except `_meta/` and `inbox/`) contains an `_index.md` that acts as a graph node for navigation by AI agents using the `docs-*` skills. `_meta/` holds only this schema, and `inbox/` is a local git-ignored scratch area (created on demand by `docs-update`) — **neither is part of the committed graph, so neither gets an `_index.md` or a graph node**. This file is the canonical, project-local schema every `docs-*` skill reads at runtime. Project-wide doc conventions live in `AGENTS.md` at the repo root.

## Frontmatter (machine-readable)

```yaml
---
domain: <root | features | architecture | decisions | guides | glossary | todo | other>
tags: [short-tag, kebab-case]
children:
  - path: <subfolder>/_index.md
    summary: <one-line description of what's in that subtree>
    tags: [tag, tag]
files:
  - path: <filename>.md
    summary: <one-line description, ≤100 chars>
    updated: <YYYY-MM-DD>
---
```

Rules:

- `domain` is the canonical category. Top-level folders use the matching name; nested folders inherit unless they specialise.
- `tags` are short kebab-case identifiers used for search filtering.
- `children` lists *immediate* subfolder index files only — not transitive descendants.
- `files` lists `.md` files in this folder excluding `_index.md` itself.
- `summary` is what the BFS walker reads to decide whether to descend or open the file. Keep it ≤ 100 chars. **Quote it** (`summary: "…"`) whenever the text contains a colon-space (`: `), `#`, or a leading `[`/`{`/`*`/`&` — otherwise YAML mis-parses the whole index and the graph fails to load.
- `updated` is the date the file last changed in a way worth noticing, written by whoever changed it. On a *new* entry, seed it from `git log -1 --format=%cs -- <file>` (today's date if the file isn't in git yet).

### Stability: these two fields are written once and then left alone

Both `summary` and `updated` are **sticky**. This is the single most violated rule in this schema, and violating it is not cosmetic — it has produced whole commits whose entire content was re-worded summaries and shuffled dates, which buries the one line that actually mattered.

- **Never reword a `summary` that is still true.** It is rewritten when *what the file is about* changes — a new topic, a dropped one, a renamed subject. A summary that means the same thing in different words is noise, and so is "improving" someone else's phrasing.
- **Never rewrite `updated` on its own.** If the only thing you would change in an entry is its date, change nothing. In particular, do not "reconcile" dates against `git log` across the tree: git already knows when a file changed, this field is a convenience for a human scanning the index, and a date that lags reality by a week costs nothing at all.
- **Touch only the entry for the file you actually changed.** Not its neighbours, not the whole block, not the parent's `children` summary unless a genuinely new topic became visible at that level.

### What a `summary` is for — and why your latest change doesn't belong in it

A summary answers one question for someone scanning the graph: **would opening this file help me?** It names what the document is *about* — the subsystem, the contract, the moving parts a reader comes looking for. It is not a changelog, not a release note, and not a record of what was done last.

This matters because of a specific trap. The thing freshest in your mind when you edit an index is the small fix you just made, so that is what tends to end up in the summary — while the durable facts get squeezed out to keep the line short. The result reads as current and is worse than what it replaced: the next reader gets last week's bug instead of the shape of the feature.

A real example, produced while adding one control to one editor screen:

```yaml
# before
summary: "Control Panel for courses and lessons: editing, localization, and scoped ordering through Content V2."

# after implementing a free-text entry for the UI-tabs field
summary: "Control Panel for courses and lessons: custom uiTabs, editing, localization, and ordering."
```

Three things went wrong in one line, and all three are typical:

- **The new detail was promoted to the front**, ahead of everything durable — as if the page were now mainly about `custom uiTabs`. It is one field in one form.
- **`through Content V2` disappeared.** That was the architectural anchor: which API generation this screen speaks to. It is the single most load-bearing fact in the line for anyone deciding whether this doc is the one they need.
- **`scoped ordering` quietly became `ordering`**, losing the qualifier that made it meaningful.

Net effect: a fresher-looking date on a line that tells the next reader strictly less. Meanwhile the uiTabs control is described properly in the document body, where it belongs and where it already was.

Two rules follow, and they are cheap to apply:

- **A new field, a bugfix, an edge case, a "now also handles X" — never go into a summary.** They go in the file. If your change is of that kind, the summary needs nothing at all, which means the entry needs nothing but its date.
- **A summary must not lose an element to gain one.** If you are adding something, name what you are dropping and why it is now less useful than what you added. Cannot answer that — you are not improving the line, you are trading it down. Words that name a subsystem, an API generation or a contract (`through Content V2`, `chests field`, `in getReward`) are the last things to go, not the first.

## Feature files (`docs/features/*.md`)

Files in the `features/` domain carry a richer frontmatter (one feature = one file):

```yaml
---
domain: features
feature: <slug>
status: draft | in-progress | shipped | deprecated
audience: [internal, client]
consumer: [<who uses the feature>]
dependencies: [<features/services relied on>]
code_paths: [<source dirs/files this feature lives in>]
openspec: openspec/changes/<change-id>   # or archived spec path; omit if none
asana: <task URL>                          # omit if not provided
branch: <git branch where docs were added>
updated: YYYY-MM-DD
---
```

Field semantics: `audience` is one or both of the fixed values `internal` / `client`; `consumer` is a free-form list of who uses the feature. `code_paths` lists the source dirs/files this feature is implemented in, so the doc can be refreshed when they change. After the linked OpenSpec change is archived, set `openspec:` to the archived (`openspec/changes/archive/YYYY-MM-DD-<id>`) or canonical (`openspec/specs/<capability>/spec.md`) path.

Required H2 sections (each title is a single literal heading — the `/` is part of the heading text, not a separator): `How it works`, `For clients`, `Consumers`, `Dependencies and caveats`, `TODO`. See `AGENTS.md` for the full convention.

## Body (human-readable)

Below the closing `---`, free-form markdown prose:

```markdown
# <Folder name>

<What this folder is for, conventions, gotchas. Anything written for human readers.>
```

The `docs-reindex` skill reconciles the frontmatter with the filesystem — added, removed, renamed files — and preserves every byte under the closing `---`. It is not a rewriter: entries whose file still exists come through untouched, including their `summary` and `updated`. The `docs-update` skill may append to the prose when relevant but should never delete existing prose.

## Invariants

- Frontmatter `summary` fields stay short — they are scanned in bulk during search.
- A folder that contains any `.md` file or any subfolder with an `_index.md` must itself have an `_index.md` so the graph stays connected.
- Frontmatter `children`/`files` lists are a *cache* of filesystem state. They drift; `docs-reindex` reconciles **the list**, not its wording.
- File summaries are derived from each file's H1 heading and lead paragraph at the moment the entry is created; do not invent content not present in the file, and do not re-derive a summary that is still accurate.
- **A reindex over a tree where no file was added, removed or renamed produces an empty diff.** If it produced one anyway, that run misbehaved — the change is the bug, not the state it "fixed".
