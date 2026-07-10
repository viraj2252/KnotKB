> **Historical.** Deployment/scheduling details below assume the hermes-test
> stack; the standalone compose in this repo is now canonical — see
> [../SETUP.md](../SETUP.md). Env knobs and CLI behavior remain accurate.

# spec-a Operations Guide

Operational reference for the KB MCP server (spec-a feature set): reranker, synthesis (`ask`), wikilinks, and nightly consolidation.

## MCP tools exposed

The `kb-mcp` service registers four tools via the FastMCP streamable-HTTP transport:

| Tool | Signature | Purpose |
|------|-----------|---------|
| `memory_write` | `(scope, content, tags?, source?)` | Persist a fact; deduplicates on the fly |
| `memory_search` | `(query, scope?, tags?, k=8)` | Semantic search with optional cross-encoder rerank |
| `ask` | `(question, scope?, k=8)` | Cited synthesis — retrieves facts, calls LLM, returns `{answer, citations, used_facts}` |
| `get_backlinks` | `(slug)` | List facts/pages whose content links to `[[slug]]` |
| `get_links` | `(slug)` | List outgoing `[[wikilinks]]` from the page/fact with the given slug |

`ask` and `get_backlinks`/`get_links` are spec-a additions; both appear automatically once the container is running and the MCP client connects to `http://kb-mcp:8077/mcp` (or `http://127.0.0.1:8077/mcp` from the host).

## Reranker

`memory_search` uses a local ONNX cross-encoder (`BAAI/bge-reranker-base` by default) to rerank the top-`KB_RERANK_CANDIDATES` (default 30) vector hits down to `k` results. No API key or GPU needed — fastembed runs on CPU.

Disable reranking at runtime by setting `KB_RERANK_ENABLED=false` in `.env` and restarting the container. When disabled, vector similarity scores are returned directly.

## Synthesis (`ask`)

`ask` retrieves up to `KB_SYNTH_MAX_FACTS` (default 8) facts via `memory_search`, then calls the LLM at `KB_SYNTH_BASE_URL` (default `http://claude-proxy:8000/v1`) using the OpenAI-wire format. The key env knobs:

```
KB_SYNTH_BASE_URL=http://claude-proxy:8000/v1   # any OpenAI-compatible endpoint
KB_SYNTH_MODEL=claude-sonnet-4-6
KB_SYNTH_KEY=                                    # defaults to HERMES_PROXY_KEY
KB_SYNTH_MAX_FACTS=8
```

If the synth endpoint is unreachable, `ask` returns an error dict `{error: "...", citations: []}` rather than raising.

## Env knobs reference

| Variable | Default | Description |
|----------|---------|-------------|
| `KB_RERANK_ENABLED` | `true` | Enable/disable cross-encoder reranking |
| `KB_RERANK_MODEL` | `BAAI/bge-reranker-base` | fastembed cross-encoder model name |
| `KB_RERANK_CANDIDATES` | `30` | Vector fetch width before reranking |
| `KB_SYNTH_BASE_URL` | `http://claude-proxy:8000/v1` | LLM endpoint for synthesis |
| `KB_SYNTH_MODEL` | `claude-sonnet-4-6` | Model name passed to the LLM |
| `KB_SYNTH_KEY` | _(HERMES_PROXY_KEY)_ | Bearer key for the synth endpoint |
| `KB_SYNTH_MAX_FACTS` | `8` | Max facts sent to the LLM per `ask` call |
| `KB_STALE_DAYS` | `180` | Age threshold (days) for consolidation candidates |
| `KB_AUTOMERGE` | `0.97` | Cosine threshold for safe automatic fact merging |

## Nightly consolidation schedule

`kb consolidate` (dry-run) or `kb consolidate --apply` de-duplicates near-identical facts, supersedes stale ones, and emits a summary report. Run it on demand:

```bash
# dry-run (report only, no writes)
docker compose --env-file .env exec -T kb-mcp kb consolidate

# apply (merges + marks superseded facts)
docker compose --env-file .env exec -T kb-mcp kb consolidate --apply
```

### Install the launchd schedule (macOS)

A snapshot plist lives at `docs/reference/kb-consolidate.plist.snapshot`. It runs `kb consolidate --apply` nightly at 03:30.

```bash
cp docs/reference/kb-consolidate.plist.snapshot \
   ~/Library/LaunchAgents/dev.kb.consolidate.plist
launchctl load ~/Library/LaunchAgents/dev.kb.consolidate.plist
```

Logs go to `/tmp/kb-consolidate.log` and `/tmp/kb-consolidate.err`. To unload:

```bash
launchctl unload ~/Library/LaunchAgents/dev.kb.consolidate.plist
```

## Quick-start operations

```bash
# Rebuild kb-mcp image and restart (first run downloads reranker weights ~90 MB)
cd ~/development/hermes-test
HERMES_UID=$(id -u) HERMES_GID=$(id -g) docker compose --env-file .env up -d --build kb-mcp

# Poll for healthy
for i in $(seq 1 60); do
  curl -fsS http://127.0.0.1:8077/health >/dev/null 2>&1 && { echo healthy; break; }
  sleep 3
done

# Re-index all markdown facts into pgvector
docker compose --env-file .env exec -T kb-mcp kb reindex

# Dry-run consolidation
docker compose --env-file .env exec -T kb-mcp kb consolidate
```
