---
name: docs-search
description: Use when the user wants to find information in the project's docs/ tree — past decisions, specs, architecture notes, glossary terms, how-to guides. Triggers include "search docs", "lookup in docs", "find in docs", "/docs-search".
---

# docs-search

Answer a question by walking the project's `docs/` graph through `_index.md` nodes. Heavy reading runs in a subagent so the main conversation stays clean — only the answer + citations come back.

## When to use

User asks to retrieve something from `docs/` (not to write anything). Example phrases above.

## What this skill does

The main agent does steps 1 and 3. The subagent does step 2.

### 1. Read the docs root

Read `<cwd>/docs/_index.md`. If it does not exist, tell the user the project is not bootstrapped — they should run `docs-init` — and stop.

### 2. Dispatch the search subagent

Invoke `Agent` with `subagent_type: Explore` (Claude Code's read-only explorer; on a non-Claude runtime substitute your general-purpose agent) and a briefing:

> You are a documentation searcher for the project at `<absolute project root>`. Follow the `_index.md` graph protocol — see `<project root>/docs/_meta/index-format.md` for the spec.
>
> **Query:** `<user query, verbatim>`
>
> **Root index (`docs/_index.md`):**
>
> ```
> <inline contents from step 1>
> ```
>
> **Search protocol:**
> 1. Score each child entry in the root by relevance to the query using its `tags` and `summary`. Pick the top 3.
> 2. For each picked node, read its `_index.md`. Repeat the scoring on its children. Recurse depth-first up to depth 4.
> 3. At each leaf level, read at most 3 `.md` files whose summaries match best.
> 4. As a fallback, run `rg -i -F '<key terms from query>' <absolute project root>/docs/ -g '!_meta/' -g '!inbox/'` once. If it surfaces a file outside your BFS path, read it. Ignore anything under `docs/_meta/` (schema/meta, not part of the docs graph) even if it matches.
> 5. Stop when you have a confident answer or have read 12 files total — whichever comes first.
> 6. Return under 300 words:
>    - **Answer**: synthesised, in the same language as the query (Russian if the query is Russian).
>    - **Citations**: `<path>:<line range>` for each fact.
>    - **Explored nodes**: flat list of `_index.md` and `.md` files you read, in order.
>    - **Confidence**: high / medium / low, with a one-line reason.

### 3. Surface the answer

Show the subagent's report verbatim. If the user wants more depth on a specific citation, the main agent reads that file directly without spawning another subagent.

## Iron rules

- Never read more than `docs/_index.md` yourself before dispatching the subagent.
- Trust the subagent's citations; do not re-verify unless the user disputes.
- If the subagent returns "low confidence — nothing relevant found", say so explicitly. Do not fabricate.
