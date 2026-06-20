# KB Enrichment — Spec A: Synthesis, Reranking, Wikilinks, Consolidation

**Date:** 2026-06-20
**Status:** Approved for planning
**Builds on:** `2026-06-18-shared-knowledgebase-design.md` (the kb engine: markdown source of truth + pgvector hybrid + RRF + MCP).
**Sequel:** Spec B (knowledge graph + discovery ops) — deferred; designed after Spec A ships.

Confidence tags: **[Certain]**, **[Likely]**, **[Guessing]**.

---

## 1. Goal

Add a curated, high-value subset of GBrain-style capabilities to our existing kb, **without** turning it into a platform. Four additions, all single-user, local-first, and either zero-cost or routed through the Claude backend we already run:

1. **Synthesis with citations** — an `ask` MCP tool that answers from the KB with sourced citations.
2. **Reranking** — a local cross-encoder that sharpens search results (and feeds `ask`).
3. **Wikilinks** — `[[slug]]` parsing + a backlink index + navigation tools + orphan detection.
4. **Nightly consolidation** — a scheduled pass that reports KB health and auto-tidies genuine duplicates.

Non-goals (explicitly skipped — platform/team or domain-specific, per the GBrain inventory at `hermes-test/.git/sdd/gbrain-inventory.md`): knowledge graph/typed entities + discovery ops (Spec B), federation/multi-user, OAuth, job queue/minions, code intelligence, image search, takes/calibration, eval framework, skills system.

## 2. Decision summary

| Decision | Choice | Why |
|---|---|---|
| Scope split | This is **Spec A**; KG + discovery is a later **Spec B** | Four groups incl. a heavy KG is too much for one clean plan; ship value first |
| Synthesis LLM | **Existing claude-proxy** (`http://claude-proxy:8000/v1`), configurable | No new account/key; already authed; best quality |
| Reranker | **Local** `fastembed` cross-encoder (`BAAI/bge-reranker-base`), configurable | No API cost; ONNX CPU; consistent with local embeddings |
| Wikilink ranking boost | **Deferred to Spec B** | Backlink-boosted ranking belongs with the KG work |
| Consolidation auto-apply | **Near-duplicate merges only** (strict threshold); staleness/orphans/tag-drift **report-only** | "Old" ≠ "wrong"; age-based auto-supersede would silently drop valid facts |
| Consolidation safety net | **Git history** (markdown is tracked) | Every auto-change is a reviewable, revertable diff |
| Schedule | **launchd** → `docker compose exec kb-mcp kb consolidate` | Same mechanism as the Drive mirror; one scheduling story |

## 3. Architecture

All four features live behind the **existing** MCP server (`kb/server.py`) and CLI (`kb/cli.py`); none changes the markdown-is-truth / pgvector-is-derived model.

```
  ask(question) ─▶ memory_search (hybrid + RERANK) ─▶ top-k facts ─▶ claude-proxy ─▶ cited answer
                          │
  memory_search ─────────┘  (rerank applied here, so ask + direct search both benefit)

  write/reindex ─▶ parse [[wikilinks]] ─▶ backlink index (derived, rebuildable)

  kb consolidate (nightly) ─▶ local checks ─▶ report (+ auto-merge near-dups via supersede-to-markdown)
```

### New / changed files
- **Create** `kb/rerank.py` — `Reranker` protocol, `FastReranker` (fastembed), `FakeReranker` (tests).
- **Create** `kb/links.py` — wikilink parsing, backlink index build, `get_links`/`get_backlinks`, orphan query.
- **Create** `kb/synth.py` — `ask` orchestration + a thin OpenAI-wire LLM client wrapper (injectable).
- **Create** `kb/consolidate.py` — `consolidate()` + report writer.
- **Modify** `kb/store.py` — `search` gains an optional reranker; expose link/orphan helpers.
- **Modify** `kb/db.py` — link-edge storage + queries; orphan query.
- **Modify** `kb/markdown.py` — extract wikilinks on read/parse.
- **Modify** `kb/server.py` — register `ask`, `get_links`, `get_backlinks` tools.
- **Modify** `kb/cli.py` — add `kb consolidate`.
- **Modify** `kb/config.py` — new env knobs (below).
- **Modify** `kb-mcp/Dockerfile` — pre-pull reranker model (optional, like the embedder).
- **Modify** `hermes-test/docker-compose.yml` — add `KB_SYNTH_*` / `KB_RERANK_*` env to the `kb-mcp` service.

### New config (`Config`, all with defaults)
```
KB_RERANK_ENABLED   = true
KB_RERANK_MODEL     = "BAAI/bge-reranker-base"
KB_RERANK_CANDIDATES= 30          # top-N from hybrid fed into the reranker
KB_SYNTH_BASE_URL   = "http://claude-proxy:8000/v1"
KB_SYNTH_MODEL      = "claude-sonnet-4-6"
KB_SYNTH_KEY        = ""          # bearer for the synthesis endpoint (proxy is auth-free)
KB_SYNTH_MAX_FACTS  = 8           # context facts passed to the LLM
KB_STALE_DAYS       = 180         # consolidation staleness threshold (report-only)
KB_AUTOMERGE        = 0.97        # strict consolidation auto-merge threshold (well above the 0.92 write-time merge)
```

## 4. Feature: Synthesis — `ask`

**MCP tool:** `ask(question: str, scope: str|list|None = None, k: int = KB_SYNTH_MAX_FACTS) -> dict`

Flow:
1. `kb.search(question, scope, k)` (hybrid + rerank) → top-k facts with provenance.
2. If no facts: return `{answer: "insufficient evidence in the knowledge base", citations: [], used_facts: []}` — **no LLM call**.
3. Build messages: a system prompt — *"Answer ONLY from the numbered context. Cite sources as [n]. If the context doesn't cover it, say 'insufficient evidence'. Be concise."* — and a user message embedding the question + numbered context (`[1] (path) content …`).
4. POST to `KB_SYNTH_BASE_URL` `/chat/completions` (OpenAI wire) with `KB_SYNTH_MODEL`, bearer `KB_SYNTH_KEY` if set.
5. Return `{answer, citations: [{n, path, scope}], used_facts: [...]}` where citations map the `[n]` markers back to the retrieved facts' provenance.

- **Read-only.** Never writes to the KB.
- **LLM client is injectable** (`synth.LLMClient` protocol) so tests use a fake; production uses the OpenAI-wire HTTP client.
- **Errors:** synthesis endpoint unreachable/timeout → tool returns a clear error object; plain `memory_search` is unaffected. [Certain]

## 5. Feature: Reranking

- `Reranker` protocol: `rerank(query: str, candidates: list[tuple[Fact, float]]) -> list[tuple[Fact, float]]` (returns candidates reordered by cross-encoder relevance).
- `FastReranker` wraps `fastembed.rerank.TextCrossEncoder(model=KB_RERANK_MODEL)` (ONNX CPU; lazy import like `FastEmbedder`).
- `FakeReranker` (tests) scores by deterministic token overlap so ordering is assertable.
- **Pipeline:** in `KnowledgeBase.search`, when `KB_RERANK_ENABLED`, retrieve `KB_RERANK_CANDIDATES` from the hybrid store, rerank, then truncate to `k`. When disabled, current behaviour (RRF order) is unchanged.
- `ask` inherits reranking through `search`. [Certain]

## 6. Feature: Wikilinks

- `kb/links.py` `parse_wikilinks(text) -> list[str]` extracts `[[slug]]` and `[[slug|alias]]` (alias ignored for resolution; target = slug).
- A **link edge store**: a table `links(src_id text, dst_slug text)` (or equivalent), rebuilt from markdown on every `write` and on `reindex`. Derived/disposable, like the vector index.
- Resolution: `dst_slug` matches a fact/page by its slug (filename stem / `id`); unresolved links are retained as dangling (reported by consolidation, not an error).
- **MCP tools:** `get_links(slug)` (outgoing), `get_backlinks(slug)` (incoming).
- **Orphan query:** facts/pages with zero inbound links — exposed for consolidation.
- **No ranking boost in Spec A.** Links are navigational + orphan signal only. [Certain]

## 7. Feature: Nightly consolidation — `kb consolidate`

- `kb/consolidate.py` `consolidate(store, embedder, repo_path, config, apply: bool) -> Report` + `kb consolidate` CLI (mirrors `reindex`/`lint`). `--apply` enables auto-fixes; default in the scheduled job is apply-on.
- **Checks (all local, zero-token):**
  - **near-duplicates** — same-scope pairs with cosine ≥ `dedup_merge`.
  - **staleness** — facts with `ts` older than `KB_STALE_DAYS`.
  - **orphans** — zero inbound wikilinks.
  - **tag drift** — reuse `lint._normalize` near-duplicate tag detection.
- **Auto-apply (the safe subset only):** near-duplicate pairs with cosine ≥ `KB_AUTOMERGE` (default 0.97) are merged via the existing supersede-to-markdown path (`markdown.set_superseded` + index update) — supersede-not-delete, provenance retained. **Staleness, orphans, tag-drift, and lower-confidence near-dups are report-only.** [Certain — this is the §2 safety decision]
- **Safety net:** markdown is git-tracked, so every auto-merge is a revertable diff. The report names exactly what was auto-applied.
- **Output:** a dated report file under `.kb/reports/YYYY-MM-DD.md` (gitignored runtime state) + a `log.md` line; CLI prints a summary and exits non-zero if report-only issues remain (so it's scriptable).
- **Schedule:** a host `launchd` job runs `cd ~/development/hermes-test && docker compose exec -T kb-mcp kb consolidate --apply` nightly. A snapshot plist is committed under `docs/reference/` (like the Drive-mirror plist). [Likely]

## 8. Error handling

- **Reranker model load failure** → log + fall back to RRF order (search still works), surfaced in health. [Likely]
- **Synthesis endpoint down/timeout** → `ask` returns a clear error; search/write unaffected.
- **Unresolved wikilink** → dangling edge retained, reported, never fatal.
- **Consolidation:** an auto-merge that fails to write markdown aborts *that* merge (logged in the report) without aborting the run; report-only checks never mutate.

## 9. Testing (TDD focus)

1. **Reranker** — `FakeReranker` reorders deterministically; `search` returns reranked top-k when enabled and RRF order when disabled; candidate-N honored.
2. **Wikilinks** — `parse_wikilinks` handles `[[slug]]`, `[[slug|alias]]`, multiple/none; backlink index correct after write + after reindex; `get_backlinks` returns inbound; orphan query finds zero-inbound facts.
3. **Synthesis** — `ask` with an **injected fake LLM client**: correct prompt assembly (numbered, source-tagged context), citation `[n]`→provenance mapping, return shape; no-facts path returns "insufficient evidence" **without** calling the LLM; endpoint-error path returns a clear error.
4. **Consolidation** — near-dup ≥ `KB_AUTOMERGE` is auto-merged + superseded in markdown + survives reindex (superseded stays hidden); staleness/orphans/tag-drift are reported but **not** mutated; report file written; `--apply` vs report-only modes behave correctly.

## 10. Build order (within Spec A)

1. **Reranking** (`rerank.py` + `search` integration) — self-contained, improves everything downstream.
2. **Wikilinks** (`links.py` + storage + tools + orphan query) — needed by consolidation's orphan check.
3. **Synthesis** (`synth.py` + `ask` tool) — depends on search/rerank.
4. **Consolidation** (`consolidate.py` + CLI + schedule) — uses dedup + orphans + supersede.
5. **Wiring** — compose env, optional Dockerfile model pre-pull, launchd snapshot, docs.

## 11. Open implementation details (resolve during planning)

- Exact `fastembed` reranker API surface/model id (confirm `TextCrossEncoder` + `BAAI/bge-reranker-base` availability; pick a fallback like `Xenova/ms-marco-MiniLM-L-6-v2` if needed).
- Link store: a dedicated `links` table vs. a JSON column on `facts` — pick during planning (lean: dedicated table, rebuilt on reindex).
- Slug identity for resolution: filename stem vs. a `slug` frontmatter field (current ids are timestamped; wikilinks likely target human slugs — may need a `slug`/`title` field on durable facts, or resolve against `path` stem). Resolve in planning; may add an optional `slug` to `Fact`.
- Citation parsing robustness (model emits `[n]` markers) — map by index; tolerate missing/extra markers.
- `.kb/reports/` location + retention.
