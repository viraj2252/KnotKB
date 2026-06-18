# Registering kb-mcp

The kb-mcp server can be registered with both Claude Code and Hermes, allowing bidirectional knowledge sharing across clients.

## Prerequisites

1. The kb-mcp stack must be running (`make up` in the knowledge-base repo).
2. Extract the `KB_MCP_KEY` from `.env`:
   ```bash
   grep '^KB_MCP_KEY=' ~/development/knowledge-base/.env | cut -d= -f2
   ```
3. Both the KB stack and Hermes must share the external Docker network `hermes-net`. Ensure hermes-test's compose references this network before starting Hermes, or bring the KB stack up first with `make up` (it creates/joins `hermes-net`).

## Claude Code (host)

Register kb-mcp as an MCP server on your local host:

```bash
KB_MCP_KEY=$(grep '^KB_MCP_KEY=' ~/development/knowledge-base/.env | cut -d= -f2)
claude mcp add kb --transport http http://127.0.0.1:8077/mcp \
  --header "Authorization: Bearer ${KB_MCP_KEY}"
```

Verify the registration:
```bash
claude mcp list
```

Expected output: `kb` listed and reachable.

## Hermes (container via dashboard)

Register kb-mcp via the Hermes dashboard (http://127.0.0.1:9119):

1. Navigate to the dashboard's MCP servers settings.
2. Add a new MCP server entry with:
   - **Transport**: http (streamable)
   - **URL**: `http://kb-mcp:8077/mcp`  (reachable over hermes-net bridge)
   - **Header**: `Authorization: Bearer <KB_MCP_KEY>` (paste the key from `.env`)

The configuration is persisted in the named Docker volume; do not edit files directly in the repo (see hermes-test's CLAUDE.md for details on configuration management).

## Verification

After both registrations are complete, run the bidirectional smoke test:

```bash
~/development/knowledge-base/scripts/smoke-bidirectional.sh
```

This writes a test fact via Claude Code's kb tool, then prompts you to read it back via Hermes, proving shared access to the same knowledge base store.
