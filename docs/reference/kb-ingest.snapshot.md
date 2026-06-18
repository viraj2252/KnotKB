---
name: kb-ingest
description: Use when the user drops a transcript/source into the KB sources/ folder or asks to "ingest", "add to the knowledge base", or "compile notes". Distills raw sources into tagged atomic facts + a curated wiki page.
---

# KB Ingest (Karpathy loop)

## Inputs
- A raw file under `~/development/knowledge-base/sources/` (or pasted content).

## Steps
1. **Read** the source. Do NOT dump it wholesale into memory_write.
2. **Distill** 3–10 atomic, standalone facts. For each, call the `kb` tool
   `memory_write(scope, content, tags, source)`:
   - `scope`: `global` for general knowledge; `project:<name>` if clearly project-bound.
   - `tags`: free-form topic tags (`ai-trends`, `startup-idea`, `business-idea`,
     `life-lesson`, `radar`, ...). Reuse existing tags; check with `memory_search` first.
   - `source`: the source filename.
3. **Synthesise** into the matching `wiki/<topic>.md` page: create it if missing,
   otherwise integrate (don't append blindly — merge, cross-reference, supersede stale lines).
4. **Update** `wiki/index.md` if a new topic page was created.
5. **Log** one line to `log.md`: `## [YYYY-MM-DD] ingest | <source> | <summary>`.
6. **Report** what was written (facts + which wiki page) and any `kb lint` follow-ups.

## Rules
- Raw `sources/` files are immutable — never edit them.
- Prefer reusing an existing tag over coining a near-duplicate (avoid `ai-trend` vs `ai-trends`).
- One topic per wiki page; keep pages human-browsable.
