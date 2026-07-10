# KnotKB — personal knowledge base with an MCP server

A single-user knowledge base shared by Claude Code (and optionally the Hermes
agent). Markdown is the source of truth; a local pgvector index is derived and
rebuildable. Everything runs locally in Docker — embeddings are computed
in-process (fastembed, no API cost), and LLM-powered features are optional.

**MCP tools:** `memory_write`, `memory_search`, `ask` (cited synthesis),
`get_backlinks`, `get_links`, `find_experts`, `get_entity`, `find_orphans`.
**CLI:** `kb reindex | lint | consolidate | extract | ingest | review`.

## Quickstart

Prerequisites: Docker with the compose plugin (Docker Desktop, Colima, or
docker engine). Python 3.12 is only needed for development.

```bash
git clone https://github.com/viraj2252/KnotKB knowledge-base
cd knowledge-base
cp .env.example .env
# in .env, set:
#   KB_MCP_KEY=$(openssl rand -hex 32)
#   KB_HOST_PATH=  -> your Obsidian vault's agent-kb/ folder, or leave empty
#                     to use a local ./kb-data directory
make up        # first boot builds the image and downloads ~90 MB of models
make health    # -> healthy
make reindex   # build the pgvector index from the markdown vault
```

Register with Claude Code:

```bash
KB_MCP_KEY=$(grep '^KB_MCP_KEY=' .env | cut -d= -f2)
claude mcp add kb --transport http --scope user http://127.0.0.1:8077/mcp \
  --header "Authorization: Bearer ${KB_MCP_KEY}"
claude mcp list    # expect: kb ... ✔ Connected
```

That's a fully working install: search and write work out of the box. The
LLM-backed features (`ask`, entity extraction, auto-ingest) are off until you
set `KB_SYNTH_BASE_URL` — see the setup guide.

## Full setup guide

**[docs/SETUP.md](docs/SETUP.md)** covers macOS, Windows (WSL2), and Linux
end-to-end: prerequisites per platform, every configuration knob, the nightly
consolidation job (`make schedule-install` — launchd / systemd / cron),
wiring an LLM backend (OpenAI-compatible: claude-proxy, OpenAI, Ollama,
LiteLLM), Hermes integration, verification, and troubleshooting.

## Repo map

- `kb-mcp/` — the Python package (server, CLI, tests: `cd kb-mcp && pytest`)
- `docker-compose.yml` — canonical standalone stack; `docker-compose.hermes.yml` — optional overlay joining `hermes-net`
- `deploy/` — nightly-job assets + `install-scheduler.sh`
- `scripts/verify-standalone.sh` — end-to-end verification on an isolated stack (`make verify`)
- `example/` — sample vault layout
- `docs/SETUP.md` — the setup guide; `docs/reference/` — ops notes (partly historical); `docs/superpowers/` — design specs and plans

Real knowledge never lives in this public repo — content directories are
gitignored; the vault lives wherever `KB_HOST_PATH` points.
