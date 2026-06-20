# Spec B ops — knowledge graph

- Nightly `kb consolidate --apply` now also extracts entities (claude-proxy) for
  un-extracted facts, writes `entities/<slug>.md` pages, caches `entities:` on facts,
  and injects `Entities: [[slug]]` links. Manual run: `kb extract`.
- New MCP tools: `find_experts(query, entity_type, k, scope)`, `get_entity(slug)`, `find_orphans()`.
- Search applies a small backlink boost when `KB_BACKLINK_BOOST > 0`.
- Cost: one LLM call per not-yet-extracted fact, capped by `KB_EXTRACT_MAX_FACTS` per run.
  Set `KB_EXTRACT_ENABLED=false` to disable extraction.

## Environment variables (kb-mcp service)

| Variable | Default | Description |
|---|---|---|
| `KB_EXTRACT_ENABLED` | `true` | Enable/disable LLM entity extraction during consolidate/extract runs |
| `KB_EXTRACT_MODEL` | _(inherits `KB_SYNTH_MODEL`)_ | Model override for extraction calls (any OpenAI-wire endpoint) |
| `KB_EXTRACT_MAX_FACTS` | `50` | Max facts processed per `kb extract` run — hard cost cap |
| `KB_ENTITY_TYPES` | `person,company,project,topic` | Comma-separated entity types the LLM is prompted to find |
| `KB_BACKLINK_BOOST` | `0.3` | Additive score boost applied to search results with backlinks; `0` = Spec A ranking |

## Manual operations

```bash
# Extract entities from un-extracted facts (calls claude-proxy):
docker compose --env-file .env exec -T kb-mcp kb extract

# Rebuild the vector index after extraction (no LLM calls):
docker compose --env-file .env exec -T kb-mcp kb reindex

# List entity pages written to the vault:
docker compose --env-file .env exec -T kb-mcp sh -c 'ls /kb/entities'
```

## Output

`kb extract` prints a summary line:

```
facts_extracted=N entities_created=M skipped=K
```

- `facts_extracted` — facts where the LLM returned at least one entity
- `entities_created` — new entity pages written (upsert; existing pages are updated, not counted)
- `skipped` — facts where extraction was skipped (already extracted, proxy error, bad JSON)

Entity pages land in `<vault>/agent-kb/entities/<slug>.md` with front-matter `type:`, `aliases:`, `mentions:` and are picked up by `kb reindex` as searchable source documents.

## Error handling

- Proxy unreachable or HTTP error → fact is skipped (logged), run continues, exit 0.
- Malformed JSON from LLM → fact is skipped (logged), run continues.
- Re-running is safe: already-extracted facts (front-matter `extracted: true`) are skipped automatically.
