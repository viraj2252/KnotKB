# KB Spec B — Knowledge Graph + Discovery

**Date:** 2026-06-21
**Status:** Approved for planning
**Builds on:** Spec A (`2026-06-20-kb-spec-a-…`) — reranking, `[[wikilinks]]` + backlink/orphan index, `Fact.slug`/`aliases`, `ask`, nightly consolidation.
**Reference:** GBrain feature inventory at `hermes-test/.git/sdd/gbrain-inventory.md` (this is a tightly-scoped subset).

Confidence tags: **[Certain]**, **[Likely]**, **[Guessing]**.

---

## 1. Goal

Add a **typed-entity knowledge graph** over the existing link layer, plus a few **discovery** ops — the deferred Spec B from Spec A. Entities (person/company/project/topic) are extracted by an LLM, surfaced as markdown pages in the Obsidian vault, and connected to the facts that mention them, enabling "who/what knows about X" retrieval.

Non-goals (explicitly out): subject-predicate-object relationship triples (mentions + co-occurrence only); `find_anomalies` (cut — low payoff at single-user scale); federation/multi-user/OAuth/job-queue; any new external API account (LLM steps use the existing claude-proxy).

## 2. Decision summary

| Decision | Choice | Why |
|---|---|---|
| Entity typing | **LLM auto-extraction** via claude-proxy | User wants the "graph builds itself"; reuses the synth backend, no new account |
| Extraction timing | **Nightly batch** in `kb consolidate` (+ manual `kb extract`) | Writes stay instant/free; bounded async cost; reuses Spec A consolidation infra |
| Re-charge avoidance | **Cache extracted entities in the fact's front-matter** | LLM runs once per fact; reindex/rebuild is free |
| Entity storage | **Markdown entity pages** in vault `entities/<slug>.md` (typed) | GBrain-style; Obsidian shows them as graph nodes you can click/annotate |
| Fact ↔ entity link | **Inject `[[entity]]` links into the fact body** (idempotent) | Native Obsidian graph/backlinks; reuses Spec A `build_link_index` |
| Relationship richness | **Mentions + co-occurrence only** (no triples) | Powers find_experts/get_entity/orphans without an extraction schema |
| Discovery ops | **`find_experts`, `get_entity`, `find_orphans`** | High-value navigation/recall; `find_anomalies` cut (YAGNI) |
| Ranking | **Optional backlink boost** (`final = rerank + w·log(1+inbound)`) | Listed in scope; configurable; marginal at small scale |
| Dedup | **Normalize + alias-match + LLM canonical/aliases; merge not duplicate** | Best-effort; entity pages are hand-editable for correction |

## 3. Architecture

The nightly consolidation pass (Spec A) gains an **extract phase** before its existing checks:

```
kb consolidate (nightly) ──▶ EXTRACT phase (new):
  for each fact with no `entities:` front-matter (capped at KB_EXTRACT_MAX_FACTS):
    1. claude-proxy LLM → [{name, type, canonical, aliases}]  (prompt includes existing entity names)
    2. resolve/dedup each against existing entities/<slug>.md (slug or alias match)
    3. upsert entities/<slug>.md (typed front-matter; add new aliases)
    4. cache `entities: [<slug>…]` on the fact's front-matter
    5. inject "Entities: [[slug]]…" into the fact body (idempotent)
  ──▶ then the existing consolidate checks (near-dup/stale/orphan/tag-drift)
```

- **Markdown is the source of truth.** Entity pages + the facts' `entities:`/injected links are markdown. The graph (mentions, co-occurrence) is **derived** via `build_link_index` over all `.md` (now including `entities/`), rebuildable for free by `kb reindex` — the LLM only runs on facts lacking `entities:`.
- `entities/` is added to the indexed dirs so entity pages are searchable and part of the link graph.

### New / changed files
- **Create** `kb/extract.py` — `extract_entities(fact, llm, existing) -> list[Entity]` (LLM call + parse), `Entity` shape, entity-page upsert + dedup, fact front-matter caching + `[[entity]]` injection (idempotent).
- **Create** `kb/discovery.py` — `find_experts`, `get_entity`, `find_orphans` over the link index.
- **Modify** `kb/consolidate.py` — add the extract phase (gated by `KB_EXTRACT_ENABLED`, capped).
- **Modify** `kb/cli.py` — add `kb extract` subcommand.
- **Modify** `kb/markdown.py` — index `entities/`; round-trip `entities:`/`type` front-matter (Fact gains `entities`/`entity_type`); helper to write entity pages.
- **Modify** `kb/models.py` — `Fact.entities: list[str]` and `Fact.entity_type: str | None` (entity pages reuse `Fact` with `entity_type` set).
- **Modify** `kb/store.py` — optional backlink boost in `search`; `find_experts`/`get_entity`/`find_orphans` methods.
- **Modify** `kb/server.py` — register `find_experts`, `get_entity`, `find_orphans` MCP tools; wire an extraction LLM client (reuse `OpenAIWireClient`).
- **Modify** `kb/config.py` — new knobs (below).

### New config (defaults)
```
KB_EXTRACT_ENABLED   = true            # run the nightly extract phase
KB_EXTRACT_MODEL     = ""              # falls back to synth_model (claude-proxy)
KB_EXTRACT_MAX_FACTS = 50              # per-run cap (cost bound)
KB_ENTITY_TYPES      = "person,company,project,topic"
KB_BACKLINK_BOOST    = 0.3             # weight w in final = rerank + w·log(1+inbound)
```

## 4. Extraction (`kb/extract.py`)

- **Input:** facts whose front-matter lacks an `entities:` key (so it runs once per fact). Bounded to `KB_EXTRACT_MAX_FACTS` per run.
- **LLM call:** an `LLMClient` (the Spec A protocol; production = `OpenAIWireClient` → claude-proxy). Prompt: the fact content + the list of existing entity names/aliases, instruction to return strict JSON `[{name, type, canonical, aliases}]` with `type ∈ KB_ENTITY_TYPES`, reusing an existing `canonical` when the mention refers to a known entity. Parse defensively (tolerate extra prose / bad JSON → skip the fact, leave it for next run).
- **Per entity:** `slug = slugify(canonical)`. If `entities/<slug>.md` exists OR the name/alias matches an existing entity's slug/`aliases` → that entity (add any new alias to its page). Else create `entities/<slug>.md` with front-matter `type`, `slug`, `aliases`, and a one-line summary.
- **Cache + link:** set the fact's `entities: [<slug>…]` front-matter and append/refresh an `Entities: [[slug]], …` line in the fact body. **Idempotent** — re-running never re-calls the LLM (front-matter present) and never double-injects (the line is replaced, not appended again).
- **Errors:** LLM/proxy down → that fact is skipped (no `entities:` written), retried next run; extraction never blocks the rest of consolidation.

## 5. Entity pages + dedup

- `entities/<slug>.md` front-matter: `type` (person/company/project/topic), `slug`, `aliases`, `ts` (mtime fallback applies). Body: a short auto-summary; Obsidian's Backlinks panel shows the facts that mention it (via the injected `[[slug]]`).
- **Dedup** is best-effort string matching: normalize (lowercase, strip punctuation) the LLM `canonical` + `name` + `aliases` and match against existing entity slugs and `aliases:`. On match → merge (extend `aliases`), don't create a duplicate. The wikilink resolver (Spec A) already routes `[[Alias]]` to the page via `aliases`.
- **Imperfect by nature** [Certain]: auto-extraction will occasionally mis-type or duplicate. Entity pages are plain markdown — hand-edit/merge in Obsidian; a re-run respects your edits (won't recreate a deleted dup unless re-mentioned).

## 6. Discovery (`kb/discovery.py`) + tools

All read the link index (`build_link_index` over facts + entity pages) — no LLM at query time.

- **`find_experts(query, type="person", k=5)`** → search facts for `query` (hybrid+rerank), gather entities of the given type mentioned in the top hits, rank by relevance-weighted mention count, return ranked entity pages.
- **`get_entity(slug)`** → `{entity page, mentions: [facts that link it], related: [co-occurring entities]}`.
- **`find_orphans(kind="all")`** → pages with no inbound links: facts (existing `orphans()`), and entity pages mentioned ≤1×. Promoted to an MCP tool.
- MCP tools registered: `find_experts`, `get_entity`, `find_orphans` (alongside Spec A's `get_links`/`get_backlinks`).

## 7. Backlink-boosted ranking

In `KnowledgeBase.search`, after rerank, optionally adjust: `final = rerank_score + KB_BACKLINK_BOOST · log(1 + inbound_link_count)`, computed from the link index. `KB_BACKLINK_BOOST = 0` disables it. Applied only when reranking is on; never reorders across scope/active filters. [Likely marginal at small scale]

## 8. Error handling

- **Proxy/LLM down during extract** → affected facts skipped, retried next run; consolidation's other phases still run. [Certain]
- **Malformed LLM JSON** → skip that fact (logged in the consolidation report), no front-matter written.
- **Unresolved/ambiguous entity** → still created as its own page; a later hand-merge fixes it.
- **Re-extraction idempotency** → guaranteed by the `entities:` front-matter gate + body-line replacement.
- **Reindex** → rebuilds the graph from front-matter/links with no LLM calls.

## 9. Testing (TDD focus)

1. **Extraction (fake LLM)** — deterministic entities cached to front-matter; idempotent (second run makes no LLM call, no double-injection); `KB_EXTRACT_MAX_FACTS` respected; malformed-JSON fact skipped (left for next run).
2. **Entity pages + dedup** — typed page created; a mention of a known alias merges into the existing page (no duplicate); `[[Alias]]` resolves to it.
3. **Fact↔entity link injection** — fact body gains `Entities: [[slug]]…`; `build_link_index` connects fact→entity; re-run doesn't duplicate the line.
4. **Discovery** — `find_experts`: the person in the most query-relevant facts ranks first; `get_entity`: returns mentions + co-occurring entities; `find_orphans`: flags a ≤1-mention entity.
5. **Backlink boost** — with boost on, a well-linked fact outranks an equally-relevant unlinked one; boost=0 reproduces Spec A order.

## 10. Build order

1. Config knobs + `Fact.entities`/`entity_type` + markdown round-trip + index `entities/`.
2. `extract.py` — extraction, entity-page upsert/dedup, front-matter cache + link injection (fake-LLM TDD).
3. Wire extract phase into `kb consolidate` + `kb extract` CLI + extraction LLM client.
4. `discovery.py` — `find_experts`/`get_entity`/`find_orphans` + MCP tools.
5. Backlink-boosted ranking in `search`.
6. Wiring + live verify (real claude-proxy extraction over the seeded Flintt facts → entity pages appear in the vault; `find_experts("brand engagement")` returns a sensible entity).

## 11. Privacy / deployment

- Specs/plans/code are in the **public KnotKB repo** — never commit the literal Obsidian vault path; use `${KB_HOST_PATH}` (already wired). Entity pages are written into the vault at `${KB_HOST_PATH}/entities/`.
- Extraction uses claude-proxy (already on `hermes-net`); no new credentials. `KB_EXTRACT_*` env added to the `kb-mcp` compose service.

## 12. Open implementation details (resolve during planning)

- Slugify rules (CJK, collisions when two canonical names slugify the same — suffix or merge-by-type).
- Entity-page summary: static stub vs. a one-line LLM summary (extra cost) — lean stub for v1.
- `find_experts` ranking formula (weight by fact rerank score vs. raw mention count) — tune in tests.
- Whether `topic` entities overlap with existing `wiki/<topic>.md` pages (avoid double nodes) — likely treat an existing wiki slug as the entity page rather than creating `entities/<topic>.md`.
- Extract-phase reporting in the consolidation report (counts of new entities / facts extracted / skipped).
