# Shared Knowledge Base — Design Spec

**Date:** 2026-06-18
**Status:** Approved for planning
**Supersedes:** the two-store split in `kb-discussion.md` §5b/§5c (see "Departures from prior notes")

Confidence tags: **[Certain]**, **[Likely]**, **[Guessing]**.

---

## 1. Goal

One personal knowledge base that **both** Claude Code (interactive, on the host) and the
Hermes agent (runtime, in a container) read from and write to *in a common way* — so
knowledge accumulated by either tool is visible to the other. It accumulates durable
personal/business context, project knowledge, decisions, and day-to-day learnings.

Non-goals: a governance/CPS exercise; a bespoke "Agent OS" platform. This is a tightly
scoped build — a markdown KB plus the thinnest possible query/write layer over it.

## 2. Decision summary

| Decision | Choice | Why |
|---|---|---|
| Sharing model | **One shared MCP server over a markdown source of truth** | A single mediating server makes sharing bidirectional and serializes writes, dissolving the concurrency argument that drove the prior two-store split |
| Source of truth | **Markdown + git** | Human-readable, diffable, revertable; matches the LLM-Wiki-v3 instinct (markdown is truth, indexes are disposable) |
| Search backend | **pgvector (local container)** from day one | Postgres is not yet in the stack, so we provision a local pgvector container — free, self-contained, no cloud dependency; swappable to Neon/Supabase via connection string |
| Embeddings | **Local, in-process** (`fastembed`, ONNX CPU, `bge-small-en-v1.5`, 384-dim) | No API/subscription cost; no GPU required |
| Language | **Python** | Matches the existing `claude-proxy` (FastAPI) and the Python/TDD preference |
| Transport | **Long-lived HTTP MCP server** | Single mediator process → serialized writes, no per-client stdio subprocesses |
| Build scope | KB repo + Claude Code wiring + `kb-mcp` registered to **both** tools | The complete "shared in a common way" goal in one spec |

### Why this over "just files" [for the record]

For Claude Code *alone*, plain markdown + `@import` + grep would be sufficient — and the
design keeps exactly that for the source of truth. The MCP layer is justified **only** by
requirements files-alone cannot meet, all of which are real here:

1. **Hermes runtime needs a programmatic verb.** A runtime agent mid-task has no human to
   say "open `priorities.md`"; it needs `memory_search` / `memory_write` as API calls.
2. **Write discipline.** Dedup/merge/supersede is the actual engineering value; a folder of
   markdown cannot dedup itself. This is why the TDD focus is the write side.
3. **Retrieval by meaning** scales where filename/structure-based lookup degrades (grows
   with volume; not a day-one benefit).
4. **Concurrency + scope isolation**, enforced by one mediating server rather than hoped for.

If those demands did not exist, files-alone would be the correct, simpler answer.

## 3. Architecture

```
   Claude Code ──┐  (host, 127.0.0.1)          ~/development/knowledge-base/  ← git = TRUTH
   (host CLI)    │                              ├── context/   (identity, @import'd)
                 ├──▶  kb-mcp  ────────────────▶├── wiki/      (curated synthesis)
   Hermes     ──┘   (one HTTP MCP server)       ├── decisions/ (append-only)
   (container,      memory_search / memory_write├── memory/    (atomic facts, scoped)
    via hermes-net)        │                    └── index.md / log.md
                           ▼
                   kb-postgres (pgvector)  ← derived, rebuildable semantic index
```

- **Markdown + git is the single source of truth.** Everything durable is a file.
- **`kb-mcp`** is the one common interface — a long-lived HTTP MCP server. Both tools
  register the *same* server, so writes are serialized and sharing is bidirectional.
- **pgvector is a derived index**, not a store of record. `kb reindex` rebuilds it from
  markdown; it is disposable.
- **Claude Code additionally `@import`s `context/about-me.md`** — a zero-infra always-on
  identity path alongside the MCP.

## 4. Repository layout

```
~/development/knowledge-base/
├── context/         about-me.md, about-flintt.md, priorities.md, preferences.md
├── wiki/            curated synthesis pages + index.md  (human/LLM-curated)
│   ├── ai-trends.md, startup-ideas.md, life-lessons.md, ...  (one per topic, emergent)
│   └── index.md
├── decisions/       immutable, dated, append-only
├── sources/         raw transcripts & dropped sources — immutable, read-not-edited
├── memory/          atomic facts written via memory_write (flat, separated by tag)
│   ├── global/      general facts/ideas; topic carried as tags, not folders
│   └── project/<name>/
├── log.md           append-only chronological write/ingest log
├── index.md         top-level catalog
├── kb-mcp/          Python MCP server + tests/
│   ├── server.py
│   ├── store.py          (pgvector + markdown read/write)
│   ├── embeddings.py     (fastembed wrapper)
│   ├── dedup.py          (write-side dedup/merge/supersede)
│   ├── reindex.py        (rebuild index from markdown)
│   └── tests/
├── docker-compose.yml   kb-postgres (pgvector) + kb-mcp, joins hermes-net (external)
├── .env.example         KB_MCP_KEY, KB_DB_URL, KB_EMBED_MODEL, KB_REPO_PATH, KB_MCP_PORT
└── docs/superpowers/specs/
```

### Channels, kept distinct

This makes concrete the reconciliation the prior notes worried about:

- **`memory/`** — atomic facts, **machine-written by `memory_write`**, deduped. The runtime memory.
- **`wiki/`** — curated synthesis, **one page per topic** (e.g. `ai-trends.md`, `startup-ideas.md`,
  `life-lessons.md`, a project page), authored by the `kb-ingest` **skill** (Karpathy ingest loop).
  Never written by `memory_write`.
- **`decisions/`** — append-only human decision log.
- **`sources/`** — raw transcripts and dropped sources. **Immutable** (read, never edited).
  `kb-ingest` distills these into tagged `memory/` facts + the relevant `wiki/` page; they are
  not dumped wholesale into `memory_write` (raw transcripts would wreck dedup and search quality).

`memory/`, `wiki/`, and `decisions/` are **indexed** into pgvector, so `memory_search` retrieves
across all of them; only `memory/` is **written** by the tool. `sources/` is indexed optionally
(it is the firehose — prefer searching the distilled layers).

## 5. The contract (two tools)

```python
memory_write(scope, content, tags=[], source=None) -> {id, path, action}
    # action ∈ {"created", "merged", "skipped"}

memory_search(query, scope=None, tags=None, k=8) -> [
    {content, score, scope, tags, source, ts, path}
]
```

### Two orthogonal axes

Separation works on **two independent dimensions** — conflating them is the trap:

- **Scope = the boundary** (who owns it / default visibility): `global` · `project:<name>` ·
  `agent:<name>:scratch`.
- **Topic = the subject** (what it's about): carried as **free-form tags** (`ai-trends`,
  `startup-idea`, `business-idea`, `life-lesson`, `radar`, …) and, once a topic accumulates,
  a curated **`wiki/<topic>.md`** page.

A startup idea and a life lesson are both `scope=global` but separated by *topic* (`tags`).
Project-only knowledge is separated by *scope* (`project:<name>`). The `tags` filter on
`memory_search` lets you pull a single topic (e.g. "show me startup ideas").

Tags are free-form (no fixed taxonomy) to stay flexible; a `kb lint` health-check (§10) flags
near-duplicate tag variants (`ai-trend` vs `ai-trends`) so the set doesn't drift.

Example mapping:

| What | Scope | Tags | Curated home |
|---|---|---|---|
| Project X discussion/decision | `project:X` | — | `memory/project/X/`, project wiki page |
| Latest AI trends | `global` | `ai-trends` | `wiki/ai-trends.md` |
| Next big thing to try | `global` | `radar` | `wiki/radar.md` |
| Startup idea | `global` | `startup-idea` | `wiki/startup-ideas.md` |
| Business idea | `global` | `business-idea` | `wiki/business-ideas.md` |
| Life lesson | `global` | `life-lesson` | `wiki/life-lessons.md` |

### Scopes

- `global` — cross-project facts and general knowledge (separated internally by tag/topic)
- `project:<name>` — project-scoped
- `agent:<name>:scratch` — private working memory that must not pollute others

### Search default

Defaults to `global` + the caller's project. An explicit `scope` argument widens or narrows;
an optional `tags` filter narrows to one or more topics. The caller's project is derived from
the MCP session context (see §8 Open implementation details).

### Durability rule

- **Durable scopes** (`global`, `project:*`) → write a markdown file under `memory/<scope>/`
  **and** index in pgvector.
- **`agent:*:scratch`** → **pgvector-only, ephemeral** (TTL-expired), never written to git.

## 6. Data flow

### Write
1. Embed `content`; run **dedup** against same-scope rows (vector similarity ≥ threshold).
   - merge (supersede-not-delete: mark prior superseded, keep provenance) → `action="merged"`
   - exact/near-exact already present → `action="skipped"`
   - otherwise → `action="created"`
2. If durable scope: **write markdown first** under `memory/<scope>/`, append a line to
   `log.md` (`## [date] write | scope | summary`).
3. Embed + upsert pgvector row with provenance: `path, scope, ts, tags, content_hash, superseded_by`.
4. If scratch scope: skip steps 2; write only an ephemeral pgvector row with a TTL.

**Markdown is written before indexing**, so the source of truth survives a DB outage. A
pending-reindex marker records any row that failed to index for later recovery.

### Search
1. Embed query.
2. pgvector **hybrid**: vector cosine similarity + `tsvector` full-text, fused via
   **reciprocal-rank fusion (RRF)**.
3. Apply scope filter (default `global` + caller project, unless overridden) and, if given,
   the `tags` topic filter.
4. Exclude superseded rows. Return top-`k` with provenance.

### Reindex
`kb reindex` rereads all markdown (`memory/`, `wiki/`, `decisions/`), re-embeds, and rebuilds
the pgvector tables. The index is disposable by design; this is also the DB-outage recovery path.

## 7. Deployment & wiring

- **`kb-postgres`** — `pgvector/pgvector` image, named volume, on `hermes-net`.
- **`kb-mcp`** — Python HTTP MCP server. Binds `127.0.0.1:${KB_MCP_PORT}` (default `8077`);
  **bind-mounts the KB repo** (`KB_REPO_PATH`). Safe to bind-mount because the KB dir is an
  ordinary host directory — unlike `hermes-data`, it does not rely on `flock`.
- **Claude Code (host):** `claude mcp add` (HTTP transport) → `http://127.0.0.1:8077`, with
  `KB_MCP_KEY` as bearer. Plus `~/.claude/CLAUDE.md` pointer + `@import context/about-me.md`.
- **Hermes (container):** register the MCP server at `http://kb-mcp:8077` over `hermes-net`,
  with `KB_MCP_KEY`.
- Both `kb-postgres` and `kb-mcp` live in the **knowledge-base repo's** own
  `docker-compose.yml`, joining `hermes-net` as an **external** network (so it can be brought
  up independently of the hermes-test stack but still reach Hermes).

### Security
- `kb-mcp` requires a bearer token (`KB_MCP_KEY`, generated like `HERMES_PROXY_KEY`); bound to
  `127.0.0.1`. The `claude-proxy` is auth-free by design, so the KB layer does its own auth. [Certain]
- Scope isolation (`agent:scratch` never leaks) is enforced server-side and TDD-covered.
- **[Likely]** If write access is later exposed to less-trusted external skills, gate
  `memory_write` behind Hermes `approvals` (`mode: manual`) + `command_allowlist`, **not** the
  proxy (which has no gate).

### Draft `~/.claude/CLAUDE.md`
```markdown
# Global preferences
- Senior advisor mode: accuracy over agreement. Tag confidence [Certain]/[Likely]/[Guessing].
- Python, TDD, spec-driven. Run pytest, not unittest.
- Prose over bullets unless asked.

# Knowledge base
Durable personal/business knowledge lives at ~/development/knowledge-base/.
A shared MCP server `kb` exposes memory_search / memory_write over it.
Prefer memory_search for recall; read context/ and wiki/index.md on demand.
Do NOT inline-read the whole KB; pull only what the task needs.

@~/development/knowledge-base/context/about-me.md
```

## 8. Error handling

- **Embedding model load failure** → server fails fast at startup; health check reports unhealthy.
- **pgvector unavailable** → `memory_search` returns a clear error; `memory_write` **still
  writes markdown** (truth) and records a pending-reindex marker. Markdown writes never depend
  on the DB.
- **Dedup** → near-duplicate threshold configurable; merges supersede-not-delete with retained provenance.
- **Malformed / unknown scope** → reject with a descriptive error.
- **Concurrent writes** → serialized by the single server process.

## 9. Testing (TDD focus — the write side)

Per the prior 5b reasoning, vector search is a library call; the engineering risk is on writes.

1. **Near-duplicate detection threshold** — create vs. merge vs. skip behave correctly at the boundary.
2. **Scope-boundary enforcement** — an `agent:<a>:scratch` write never surfaces in agent `<b>`'s
   default search, nor in `global`/`project` searches.
   - **Topic/tag filtering** — `memory_search(..., tags=["startup-idea"])` returns only matching
     topics within the resolved scope.
3. **Ranking determinism** — RRF fusion is stable/reproducible for a fixed corpus + query.
4. **Markdown-write-survives-DB-down** — with pgvector unreachable, `memory_write` persists the
   markdown file and a pending-reindex marker.
5. **Reindex reproducibility** — `kb reindex` rebuilds the index from markdown alone and yields
   equivalent search results (index is disposable).

## 10. Build order (all in scope)

1. **KB repo scaffold** + seed `context/*` + create `~/.claude/CLAUDE.md` + `@import`.
   *(Immediate value, zero infra; the existing `kb-discussion.md` is the seed material.)*
2. **`kb-mcp` server** (MCP SDK + `fastembed` + pgvector) + `docker-compose.yml` with
   `kb-postgres`. TDD the write side per §9.
3. **Register to Claude Code and Hermes.** Sharing goes live; verify bidirectional
   (write via one tool, read via the other).
4. **`kb-ingest` skill** at `~/.claude/skills/kb-ingest/` — the Karpathy ingest loop that
   distills raw `sources/` (transcripts, drops) into tagged `memory/` facts + the relevant
   topical `wiki/<topic>.md` page, updates `index.md`, appends `log.md`.
5. **`kb lint`** maintenance command — health-check for tag drift (near-duplicate variants),
   contradictions, stale claims, orphans, and gaps (the Karpathy lint operation).

## 11. Departures from prior notes (made explicit)

- **Single store, not two.** Supersedes §5b/§5c's markdown-for-Claude-Code / pgvector-for-Hermes
  split. The single mediating MCP server removes the concurrency rationale that justified two stores
  and eliminates the drift risk those sections were written to manage.
- **`agent:*:scratch` is pgvector-only** (ephemeral), not markdown — keeps git clean of transient
  working memory.
- **The wiki ingest loop is a skill, not part of `memory_write`.** `memory_write` records atomic
  facts; curation into `wiki/` is a separate deliberate act.
- **Postgres is provisioned locally** (a container in this repo's compose), since §5b's assumption
  that Postgres was "already in the stack" does not hold for `hermes-test`.

## 12. Open implementation details (resolve during planning)

- How `kb-mcp` derives the **caller's project** for default-scope search (explicit arg from the
  client vs. session metadata vs. a per-client config). [Guessing] explicit `scope` arg is simplest
  and most predictable.
- TTL value / sweep mechanism for `agent:*:scratch` rows.
- Exact dedup similarity threshold (start ~0.92 cosine, tune via tests).
- Markdown file granularity in `memory/` (one file per fact vs. dated append files) — affects git
  noise vs. diffability.
- `kb lint` tag-hygiene rules — how aggressively to flag/merge near-duplicate tag variants, and
  whether merging is suggested-only or automatic.
- Whether `sources/` is indexed into pgvector at all, or kept search-excluded (distilled layers only).
```
