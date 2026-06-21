# KB Spec C — Scriptable Ingest + Confidence-Gated Auto-Ingest

**Date:** 2026-06-21
**Status:** Approved for planning
**Builds on:** Spec A (search/rerank/ask/consolidate) + Spec B (entity graph/extract). Reuses `KnowledgeBase.write`, `synth.OpenAIWireClient`/`LLMClient`, `consolidate`, `cli`, `config`, `markdown`.

Confidence tags: **[Certain]**, **[Likely]**, **[Guessing]**.

---

## 1. Goal

Make the `sources/` → facts step **automatable**. Today it's a manual, agent-invoked skill (`kb-ingest`). Spec C adds a deterministic `kb ingest <file>` CLI that distills a raw source into atomic facts via claude-proxy, and a **nightly auto-ingest phase** in `kb consolidate` so dropping a file in the vault's `sources/` (with a scope directive) gets it ingested without a human in the loop — while a **confidence gate** holds shaky facts for review instead of silently committing them.

Non-goals: wiki-page synthesis (facts-only v1; curated pages still come from the Spec B extract + the interactive `kb-ingest` skill); re-ingest on source edit (use `--force`); LLM-inferred scope; any new API account (claude-proxy only).

## 2. Decision summary

| Decision | Choice | Why |
|---|---|---|
| Distillation | One claude-proxy call → `[{content, tags, confidence}]`, written via `KnowledgeBase.write` | Reuses dedup/scope/markdown/entity-injection; one call per source (cheap) |
| Scope | `--scope` > source `kb_scope:` front-matter > `global` | Deterministic; no risky LLM scope inference |
| Output | **Facts only** (v1) | Cheapest, lowest unattended-judgment risk; wiki/entities emerge via Spec B extract |
| Confidence gate | per-fact `confidence` 0–100; **≥ `KB_INGEST_CONFIDENCE` (85)** auto-writes, **<85** → `review/` draft | Human gate exactly where warranted; keeps drop-and-walk-away safe |
| Review flow | low-confidence facts → `review/<id>.md` (not indexed); `kb review list` / `kb review accept` | Clear Obsidian-visible queue; automatable approve |
| Opt-in (nightly) | only sources with a `kb_scope:` directive are auto-ingested | `sources/` stays safe for raw reference material |
| Idempotency | `kb_ingested: true` front-matter flag on the source; `--force` re-ingests | Mirrors the `extracted` flag; survives reindex/Drive-sync; Obsidian-visible |
| Nightly | new **ingest phase in `kb consolidate`, before the extract phase** | source→facts→entities→checks in one nightly run; capped by `KB_INGEST_MAX_SOURCES` |

Honest caveat [Certain]: `confidence` is the model's **self-estimate**, not calibrated — occasionally a wrong fact clears 85 and a good one is held. The gate is a safety floor, not a guarantee; the `review/` queue, write-side dedup, the consolidation report, and git/Drive history are the backstops.

## 3. Architecture

```
kb ingest <file> [--scope S] [--force]      kb consolidate (nightly)
        │                                        │ INGEST phase (new, before EXTRACT):
        ▼                                        │   for each sources/*.md with kb_scope and not kb_ingested
  ingest_file(path, kb, llm, config, scope) ◀────┘     (capped at KB_INGEST_MAX_SOURCES)
        │ 1 claude-proxy call → [{content,tags,confidence}]
        ├─ confidence ≥ 85 → KnowledgeBase.write(scope, content, tags, source)   (dedup/scope/markdown)
        ├─ confidence < 85 → review/<id>.md draft (scope,tags,confidence,source)  (NOT indexed)
        └─ mark source kb_ingested: true
  kb review list            → show review/ drafts
  kb review accept [--source X] → KnowledgeBase.write each survivor, delete its draft
```

Markdown stays the source of truth. `review/` is a new vault folder, **excluded from indexing** (not added to `_INDEXED_DIRS`), so drafts are invisible to search/`ask`/graph until accepted. The interactive `kb-ingest` skill is unchanged and complementary (you-driven, sees the facts); the CLI is the unattended path.

### New / changed files
- **Create** `kb/ingest.py` — `build_ingest_messages(content)`, `parse_facts_json(text)` (tolerant → `[{content, tags, confidence}]`), `read_source_meta(path)` (`kb_scope`/`kb_ingested` from front-matter), `mark_ingested(path)`, `write_review_draft(repo_path, scope, content, tags, confidence, source) -> Path`, `ingest_file(path, kb, llm, config, scope=None, force=False) -> dict`, `ingest_pending_sources(repo_path, kb, llm, config) -> dict`, `accept_reviews(repo_path, kb, source=None) -> dict`, `list_reviews(repo_path) -> list`.
- **Modify** `kb/consolidate.py` — add the ingest phase (before extract), gated by `KB_INGEST_ENABLED` + `llm`; `report["ingested"]`.
- **Modify** `kb/cli.py` — `kb ingest <file> [--scope] [--force]`; `kb review {list,accept [--source]}`; build a `KnowledgeBase` for these (writes need it).
- **Modify** `kb/config.py` — new knobs (below).
- **Modify** `kb/markdown.py` — only if a shared front-matter reader helps (else `read_source_meta` lives in `ingest.py`).

### New config (defaults)
```
KB_INGEST_ENABLED     = true       # nightly ingest phase on/off
KB_INGEST_MODEL       = ""         # "" -> synth_model (claude-proxy)
KB_INGEST_MAX_SOURCES = 10         # per nightly run (cost bound)
KB_INGEST_CONFIDENCE  = 85         # facts >= this auto-write; below -> review/
```

## 4. `kb ingest <file>` (and the core `ingest_file`)

1. Read the file. Resolve scope: `--scope` arg > `kb_scope:` front-matter > `"global"`. `validate_scope` it.
2. If the source has `kb_ingested: true` and not `--force` → skip (report `skipped`).
3. **One LLM call** (`OpenAIWireClient` → claude-proxy, model = `KB_INGEST_MODEL or synth_model`). Prompt: "Distill the note into atomic, standalone facts. Return ONLY a JSON array of `{content, tags, confidence}` where confidence is 0–100 for how clearly the fact is stated in the source. Skip speculation." Parse tolerantly (balanced-bracket scan, like `parse_entities_json`; bad/missing → skip the source, leave it un-ingested for next run).
4. For each fact: `confidence >= KB_INGEST_CONFIDENCE` → `kb.write(scope, content, tags, source=<basename>)`; else → `write_review_draft(...)`.
5. `mark_ingested(path)` (set `kb_ingested: true` in the source front-matter).
6. Return `{facts_written, facts_held, skipped}`.

- **Error handling:** LLM/proxy down or unparseable → no facts written, source NOT marked ingested (retried next run), counted `skipped`. Never crashes the nightly run.

## 5. Nightly ingest phase (`kb consolidate`)

- New phase **before** the extract phase: `ingest_pending_sources(repo_path, kb, llm, config)` scans `sources/*.md`, selects files with `kb_scope` set AND no `kb_ingested`, capped at `KB_INGEST_MAX_SOURCES`, calls `ingest_file` for each (scope from the directive). Aggregates `report["ingested"] = {sources_ingested, facts_written, facts_held, skipped}`.
- Ordering: ingest → extract → near-dup/orphan/stale/tag-drift, so a newly-ingested source's facts get entity-extracted and analysed in the same nightly run.
- `consolidate` needs a `KnowledgeBase` (for `.write`); build one from `(store, embedder, repo_path, config)` for the ingest phase. Gated by `KB_INGEST_ENABLED` + `llm is not None`.

## 6. Review flow

- `write_review_draft` writes `review/<id>.md` with front-matter `scope`, `tags`, `confidence`, `source` and the body = fact content. `id = make_id(ts, content_hash)`. `review/` is **not** in `_INDEXED_DIRS`.
- `kb review list` → prints each draft: confidence, source, snippet, path.
- `kb review accept [--source X]` → for each draft (optionally filtered by source): `kb.write(scope, content, tags, source)`, then delete the draft. Returns `{accepted, remaining}`. (You delete unwanted drafts in Obsidian first; accept promotes survivors.)
- Accepted facts then flow through normal dedup + (nightly) entity extraction like any written fact.

## 7. Testing (TDD, fake LLM)

1. **`parse_facts_json`** — tolerant: extracts the array from noisy text; drops malformed entries; coerces `confidence` (missing → treat as 0 / held, or a defined default — pick: missing confidence → 0 so it's held, never silently auto-written).
2. **`ingest_file`** (fake LLM) — facts ≥ threshold are `kb.write`-n (appear in `memory/<scope>/`); facts < threshold land in `review/` (not in `memory/`); source gets `kb_ingested: true`; scope precedence (`--scope` > `kb_scope` > global); `--force` re-ingests an already-ingested source; second run without `--force` skips.
3. **`ingest_pending_sources`** — only `kb_scope`-tagged, un-ingested sources processed; cap respected; a no-directive file is ignored.
4. **review flow** — `list_reviews` returns drafts; `accept_reviews` writes survivors via `kb.write` and deletes their drafts; a draft deleted by the user is simply gone.
5. **consolidate ingest phase** — `report["ingested"]` populated; runs before extract (test with `extract_enabled=False` to isolate the single fake LLM); confidence split honored end-to-end.

## 8. Privacy / deployment

- Specs/plans/code in the **public KnotKB repo** — never commit the literal Obsidian vault path; use `${KB_HOST_PATH}`. `review/` lives at `${KB_HOST_PATH}/review/`. `KB_INGEST_*` env added to the `kb-mcp` compose service.
- Live verify (final task): drop a small test file in `sources/` with `kb_scope: project:test` + a couple of low-confidence-inducing lines, run `kb ingest`/`kb consolidate`, confirm high-confidence facts land in `memory/`, low-confidence in `review/`, and `kb review accept` promotes them.

## 9. Open implementation details (resolve during planning)

- Missing `confidence` in an LLM item → default to **0** (held for review), so a model that omits it never auto-commits.
- `kb_ingested`/`kb_scope` are written into the source's YAML front-matter; if a source has no front-matter, `mark_ingested` must add a front-matter block. Define that path.
- Whether `kb review accept` re-reads scope/tags from each draft's front-matter (yes) and what to do if a draft's `scope` is invalid (skip + report).
- `review/` filename collisions (same content_hash+ts) — `-N` suffix like entity pages.
- Interaction with the agent `kb-ingest` skill: the skill should set `kb_ingested: true` on sources it processes too (so the nightly CLI doesn't re-ingest them) — document, optional follow-up.
